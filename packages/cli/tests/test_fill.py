"""`eq run` against a fake Tiingo: what a fill asks for, and what it leaves out.

The fake serves the synthetic walk as Tiingo JSON and records every price
request, so each test reads the requests a run made.
"""

import io
import json
import zipfile

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

import httpx2
import pandas as pd
import pytest

from index_cli import main as cli
from index_cli.main import main
from index_core.sources.synthetic import SyntheticSource
from index_core.sources.tiingo import TiingoSource
from index_core.store import Store


if TYPE_CHECKING:
    from pathlib import Path

WIRE_NAMES = {
    "adj_open": "adjOpen",
    "adj_high": "adjHigh",
    "adj_low": "adjLow",
    "adj_close": "adjClose",
    "adj_volume": "adjVolume",
    "div_cash": "divCash",
    "split_factor": "splitFactor",
}
MEMBERS = [f"M{i:02d}" for i in range(12)]
# The last one left the index in March 2024.
FORMER = MEMBERS[-1]
CURRENT = MEMBERS[:-1]
START = "2024-01-01"
FIRST_END = date(2026, 9, 30)
NEXT_MONTH = date(2026, 10, 30)


@dataclass
class FakeTiingo:
    """Serves bars up to the end asked for. `restated` maps a ticker to the day
    before which its adjusted prices are halved, as after a split."""

    listed: dict[str, tuple[str, str]] = field(default_factory=dict)
    restated: dict[str, date] = field(default_factory=dict)
    requests: list[tuple[str, str, str]] = field(default_factory=list)
    listing_requests: int = 0
    # Answer 429 to every price request after this many.
    refuse_after: int | None = None

    def source(self) -> TiingoSource:
        return TiingoSource(
            "secret-key", transport=httpx2.MockTransport(self._respond), min_interval=0
        )

    def asked(self, ticker: str) -> list[tuple[str, str]]:
        return [(s, e) for t, s, e in self.requests if t == ticker]

    def _respond(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "apimedia.tiingo.com":
            self.listing_requests += 1
            return httpx2.Response(200, content=self._listing_zip())
        if self.refuse_after is not None and len(self.requests) >= self.refuse_after:
            return httpx2.Response(429, text="over the limit")
        ticker = request.url.path.split("/")[3]
        start = date.fromisoformat(request.url.params["startDate"])
        end = date.fromisoformat(request.url.params["endDate"])
        self.requests.append((ticker, start.isoformat(), end.isoformat()))
        bars = SyntheticSource().fetch(ticker, start, end)
        split = self.restated.get(ticker)
        if split is not None:
            restate = pd.Series(
                [0.5 if day.date() < split else 1.0 for day in bars.index],
                index=bars.index,
            )
            adjusted = ["adj_open", "adj_high", "adj_low", "adj_close"]
            bars[adjusted] = bars[adjusted].mul(restate, axis=0)
        bars.insert(0, "date", [f"{day.date()}T00:00:00.000Z" for day in bars.index])
        body = bars.rename(columns=WIRE_NAMES).to_json(
            orient="records", date_format="iso"
        )
        return httpx2.Response(200, content=body)

    def _listing_zip(self) -> bytes:
        lines = ["ticker,exchange,assetType,priceCurrency,startDate,endDate"]
        for ticker in [*MEMBERS, "SPY"]:
            first, last = self.listed.get(ticker, ("1990-01-02", ""))
            kind = "ETF" if ticker == "SPY" else "Stock"
            lines.append(f"{ticker},NYSE,{kind},USD,{first},{last}")
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("supported_tickers.csv", "\n".join(lines) + "\n")
        return out.getvalue()


def _eq_run(tmp_path: Path, fake: FakeTiingo, end: date) -> None:
    universe = tmp_path / "members.txt"
    if not universe.exists():
        universe.write_text(
            "".join(f"{t},2000-01-03\n" for t in CURRENT)
            + f"{FORMER},2000-01-03,2024-03-01\n"
        )
    main(
        [
            *["run", "--universe", str(universe), "--filings", "synthetic"],
            *["--start", START, "--end", end.isoformat()],
            *["--db", str(tmp_path / "db.sqlite"), "--out", str(tmp_path / "s.json")],
            *["--cache", str(tmp_path / "cache")],
        ]
    )


@pytest.fixture
def tiingo(monkeypatch: pytest.MonkeyPatch) -> FakeTiingo:
    fake = FakeTiingo()
    monkeypatch.setitem(cli.PRICES, "tiingo", fake.source)
    return fake


def test_a_second_run_asks_for_nothing_and_next_month_only_for_the_new_bars(
    tmp_path: Path, tiingo: FakeTiingo
) -> None:
    _eq_run(tmp_path, tiingo, FIRST_END)
    assert sorted(t for t, _, _ in tiingo.requests) == sorted(["SPY", *MEMBERS])
    assert all(start == START for _, start, _ in tiingo.requests)
    tiingo.requests.clear()
    tiingo.listing_requests = 0

    _eq_run(tmp_path, tiingo, FIRST_END)

    assert tiingo.requests == []
    assert tiingo.listing_requests == 0

    _eq_run(tmp_path, tiingo, NEXT_MONTH)

    # The former member is held through the label window after its last day.
    assert sorted(t for t, _, _ in tiingo.requests) == sorted(["SPY", *CURRENT])
    # The overlap reaches a few bars back from 2026-09-30, not to the start.
    assert all("2026-09-20" <= start <= "2026-09-28" for _, start, _ in tiingo.requests)
    assert {end for _, _, end in tiingo.requests} == {NEXT_MONTH.isoformat()}
    with Store.open(tmp_path / "db.sqlite", read_only=True) as store:
        bars = store.read_prices(["M00"])
    assert bars["date"].max().date() == NEXT_MONTH


def test_a_split_inside_the_overlap_refetches_that_ticker_whole_once(
    tmp_path: Path, tiingo: FakeTiingo
) -> None:
    _eq_run(tmp_path, tiingo, FIRST_END)
    tiingo.requests.clear()
    # M03 split two trading days before the last stored bar.
    tiingo.restated["M03"] = date(2026, 9, 28)

    _eq_run(tmp_path, tiingo, NEXT_MONTH)

    whole = [(t, s) for t, s, _ in tiingo.requests if s == START]
    assert whole == [("M03", START)]
    assert len(tiingo.asked("M03")) == 2
    assert all(len(tiingo.asked(t)) == 1 for t in ["SPY", *CURRENT] if t != "M03")
    assert tiingo.asked(FORMER) == []
    with Store.open(tmp_path / "db.sqlite", read_only=True) as store:
        bars = store.read_prices(["M03"]).set_index("date")["adj_close"]
        reference = SyntheticSource().fetch("M03", date(2024, 1, 1), NEXT_MONTH)
    # The old bars before the split are the restated ones now.
    halved = reference["adj_close"] * 0.5
    assert bars.loc["2026-09-01"] == pytest.approx(halved.loc["2026-09-01"])


def test_a_run_that_stopped_carries_on_where_it_stopped(
    tmp_path: Path, tiingo: FakeTiingo
) -> None:
    tiingo.refuse_after = 5
    with pytest.raises(SystemExit, match="over the limit"):
        _eq_run(tmp_path, tiingo, FIRST_END)
    first = [t for t, _, _ in tiingo.requests]
    assert len(first) == 5
    tiingo.requests.clear()
    tiingo.refuse_after = None

    _eq_run(tmp_path, tiingo, FIRST_END)

    # Only what the stopped run had not stored is asked for.
    assert sorted(t for t, _, _ in tiingo.requests) == sorted(
        {"SPY", *MEMBERS} - set(first)
    )


def test_a_month_later_the_gaps_are_filled_before_the_stored_tickers_are_updated(
    tmp_path: Path, tiingo: FakeTiingo
) -> None:
    tiingo.refuse_after = 5
    with pytest.raises(SystemExit):
        _eq_run(tmp_path, tiingo, FIRST_END)
    stored = {t for t, _, _ in tiingo.requests}
    tiingo.requests.clear()
    tiingo.refuse_after = 4

    with pytest.raises(SystemExit):
        _eq_run(tmp_path, tiingo, NEXT_MONTH)

    # SPY, which every label needs, then three tickers the first run never reached.
    asked = [t for t, _, _ in tiingo.requests]
    assert asked[0] == "SPY"
    assert len(asked) == 4
    assert stored.isdisjoint(asked[1:])


def test_a_ticker_that_another_company_reused_is_dropped_and_reported(
    tmp_path: Path, tiingo: FakeTiingo, capsys: pytest.CaptureFixture[str]
) -> None:
    # Tiingo lists M05 only from 1995 to 2003, but it is a member from 2000-01-03
    # to now, and the run window is 2024 on: nothing listed overlaps the window.
    tiingo.listed["M05"] = ("1995-01-03", "2003-06-30")

    _eq_run(tmp_path, tiingo, FIRST_END)

    assert tiingo.asked("M05") == []
    err = capsys.readouterr().err
    assert "another company under 1 tickers (M05)" in err
    scores = json.loads((tmp_path / "s.json").read_text())
    assert {"ticker": "M05", "reason": "other company"} in scores["left_out"]
    assert "M05" not in {row["ticker"] for row in scores["rows"]}
    tiingo.requests.clear()

    _eq_run(tmp_path, tiingo, FIRST_END)

    assert tiingo.requests == []
