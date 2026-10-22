from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import threading
import zipfile

from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

import index_core.ledger as ledger_module
import pytest

from index_cli.main import main
from index_core.audit import audit
from opentimestamps.core.notary import (  # type: ignore[import-untyped]
    BitcoinBlockHeaderAttestation,
    PendingAttestation,
)
from opentimestamps.core.serialize import (  # type: ignore[import-untyped]
    BytesSerializationContext,
)
from opentimestamps.core.timestamp import Timestamp  # type: ignore[import-untyped]


if TYPE_CHECKING:
    from collections.abc import Iterator


class _Calendar:
    def __init__(self, *, bitcoin: bool = False) -> None:
        calendar = self
        self.bitcoin = bitcoin

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                size = int(self.headers["Content-Length"])
                digest = self.rfile.read(size)
                timestamp = Timestamp(digest)
                if calendar.bitcoin:
                    timestamp.attestations.add(BitcoinBlockHeaderAttestation(123))
                else:
                    timestamp.attestations.add(PendingAttestation(calendar.url))
                context = BytesSerializationContext()
                timestamp.serialize(context)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(context.getbytes())

            def log_message(self, *_args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.thread.join()


class _FixedDatetime:
    current = datetime(2025, 1, 2, tzinfo=UTC)

    @classmethod
    def now(cls, tz: object) -> datetime:
        return cls.current


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch: pytest.MonkeyPatch) -> type[_FixedDatetime]:
    _FixedDatetime.current = datetime(2025, 1, 2, tzinfo=UTC)
    monkeypatch.setattr(ledger_module, "datetime", _FixedDatetime)
    return _FixedDatetime


class _Bundle:
    def __init__(self, payload: bytes) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/ledger"
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.thread.join()


class _BlockTimeSource:
    def __init__(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"a" * 64)

            def log_message(self, *_args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/block/{{height}}"
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.thread.join()


@pytest.fixture
def calendar() -> Iterator[_Calendar]:
    server = _Calendar()
    yield server
    server.close()


def _scores(
    path: Path, as_of: str, *, rows: list[dict[str, object]] | None = None
) -> None:
    path.write_text(
        json.dumps(
            {
                "as_of": as_of,
                "universe": "synthetic",
                "horizon_days": 63,
                "rows": rows or [{"ticker": "AAA", "score": 10, "prob": 0.9}],
            }
        )
    )


def _publish(scores: Path, ledger: Path, calendar: _Calendar) -> None:
    main(
        [
            "ledger",
            "publish",
            "--scores",
            str(scores),
            "--ledger",
            str(ledger),
            "--calendar",
            calendar.url,
        ]
    )


def _deploy_test_ends() -> tuple[date, date]:
    first = datetime.now(UTC).date()
    while first.weekday() >= 5:
        first -= timedelta(days=1)
    second = first + timedelta(days=1)
    while second.weekday() >= 5:
        second += timedelta(days=1)
    return first, second


def test_a_clean_ledger_passes_through_eq_audit(
    tmp_path: Path, calendar: _Calendar, capsys: pytest.CaptureFixture[str]
) -> None:
    scores = tmp_path / "scores.json"
    _scores(scores, "2025-01-02")
    ledger = tmp_path / "ledger"
    _publish(scores, ledger, calendar)

    main(["audit", str(ledger)])

    output = capsys.readouterr().out
    assert "PASS chain is unbroken" in output
    assert "PASS every file matches its hash" in output
    assert "PENDING timestamps: 1" in output
    assert "not before 2025-04-01" in output
    assert audit(ledger).status["earliest_stamp"] is None


def test_empty_ledger_passes_and_reports_no_forecasts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    ledger.mkdir()

    main(["audit", str(ledger)])

    output = capsys.readouterr().out
    assert "PASS chain is unbroken: no forecasts committed" in output
    assert "PASS matured track record: no forecasts committed" in output


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("bytes", "FAIL edited bytes"),
        ("missing", "FAIL missing entry"),
        ("reordered", "FAIL reordered entry"),
    ],
)
def test_each_ledger_tamper_has_its_own_failure(
    tmp_path: Path,
    calendar: _Calendar,
    tamper: str,
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scores = tmp_path / "scores.json"
    _scores(scores, "2025-01-02")
    ledger = tmp_path / "ledger"
    _publish(scores, ledger, calendar)
    entry = ledger / "000001-2025-01-02"
    if tamper == "bytes":
        entry.joinpath("scores.json").write_text(
            entry.joinpath("scores.json").read_text().replace("0.9", "0.8")
        )
    elif tamper == "missing":
        second = tmp_path / "second.json"
        _scores(second, "2025-01-03")
        _publish(second, ledger, calendar)
        shutil.rmtree(entry)
    else:
        second = tmp_path / "second.json"
        _scores(second, "2025-01-03")
        _publish(second, ledger, calendar)
        first_path = ledger / "000001-2025-01-02"
        second_path = ledger / "000002-2025-01-03"
        moved = tmp_path / "moved-entry"
        first_path.rename(moved)
        second_path.rename(first_path)
        moved.rename(second_path)

    with pytest.raises(SystemExit, match="audit failed"):
        main(["audit", str(ledger)])
    assert message in capsys.readouterr().out


def test_stamp_after_horizon_uses_bitcoin_attestation_and_block_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calendar = _Calendar(bitcoin=True)
    try:
        scores = tmp_path / "scores.json"
        _scores(scores, "2025-01-02")
        ledger = tmp_path / "ledger"
        _publish(scores, ledger, calendar)
        block_times = tmp_path / "blocks.json"
        block_times.write_text('{"123": "2025-01-03T00:00:00Z"}')

        with pytest.raises(SystemExit, match="audit failed"):
            main(["audit", str(ledger), "--block-times", str(block_times)])
        assert "FAIL stamp after horizon" in capsys.readouterr().out
    finally:
        calendar.close()


def test_custom_block_source_does_not_fall_back_to_blockstream(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calendar = _Calendar(bitcoin=True)
    block_source = _BlockTimeSource()
    try:
        scores = tmp_path / "scores.json"
        _scores(scores, "2025-01-02")
        ledger = tmp_path / "ledger"
        _publish(scores, ledger, calendar)

        with pytest.raises(SystemExit, match="audit failed"):
            main(["audit", str(ledger), "--block-times", block_source.url])
        output = capsys.readouterr().out
        assert block_source.url in output
        assert "returned a block hash, not a block time" in output
    finally:
        block_source.close()
        calendar.close()


def test_duplicate_as_of_is_refused(tmp_path: Path, calendar: _Calendar) -> None:
    scores = tmp_path / "scores.json"
    _scores(scores, "2025-01-02")
    ledger = tmp_path / "ledger"
    _publish(scores, ledger, calendar)
    with pytest.raises(SystemExit, match="already exists"):
        _publish(scores, ledger, calendar)


def test_publish_refuses_on_the_horizon_date(
    tmp_path: Path,
    calendar: _Calendar,
    fixed_clock: type[_FixedDatetime],
) -> None:
    fixed_clock.current = datetime(2025, 1, 3, tzinfo=UTC)
    scores = tmp_path / "scores.json"
    _scores(scores, "2025-01-02")
    ledger = tmp_path / "ledger"

    with pytest.raises(
        SystemExit,
        match="publication date 2025-01-03 is on or after horizon start 2025-01-03",
    ):
        _publish(scores, ledger, calendar)

    assert not ledger.exists()


def test_publish_accepts_the_day_before_the_horizon(
    tmp_path: Path, calendar: _Calendar
) -> None:
    scores = tmp_path / "scores.json"
    _scores(scores, "2025-01-02")
    ledger = tmp_path / "ledger"

    _publish(scores, ledger, calendar)

    assert (ledger / "000001-2025-01-02").is_dir()


def test_code_worktree_dirty_ignores_ledger_and_deploy_outputs(
    tmp_path: Path, calendar: _Calendar, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "ledger").mkdir()
    (repo / "deploy").mkdir()
    (repo / "ledger" / ".gitkeep").write_text("clean\n")
    (repo / "deploy" / "status.json").write_text("clean\n")
    (repo / "source.py").write_text("clean\n")
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "initial"], cwd=repo, check=True)
    monkeypatch.chdir(repo)

    (repo / "ledger" / ".gitkeep").write_text("changed\n")
    (repo / "deploy" / "status.json").write_text("changed\n")
    scores = tmp_path / "scores.json"
    _scores(scores, "2025-01-02")
    ledger = repo / "ledger"
    _publish(scores, ledger, calendar)
    first_manifest = json.loads(
        (ledger / "000001-2025-01-02" / "manifest.json").read_text()
    )
    assert first_manifest["code_worktree_dirty"] is False

    (repo / "source.py").write_text("changed\n")
    second_scores = tmp_path / "second.json"
    _scores(second_scores, "2025-01-03")
    _publish(second_scores, ledger, calendar)
    second_manifest = json.loads(
        (ledger / "000002-2025-01-03" / "manifest.json").read_text()
    )
    assert second_manifest["code_worktree_dirty"] is True


def test_empty_source_digests_need_an_explanation(
    tmp_path: Path, calendar: _Calendar, capsys: pytest.CaptureFixture[str]
) -> None:
    scores = tmp_path / "scores.json"
    _scores(scores, "2025-01-02")
    ledger = tmp_path / "ledger"
    _publish(scores, ledger, calendar)
    manifest = ledger / "000001-2025-01-02" / "manifest.json"
    value = json.loads(manifest.read_text())
    value.pop("source_response_note")
    manifest.write_text(json.dumps(value))

    with pytest.raises(SystemExit, match="audit failed"):
        main(["audit", str(ledger)])
    assert "FAIL provenance" in capsys.readouterr().out


def test_track_record_uses_63_supplied_trading_dates_and_reports_deciles(
    tmp_path: Path, calendar: _Calendar, capsys: pytest.CaptureFixture[str]
) -> None:
    scores = tmp_path / "scores.json"
    _scores(
        scores,
        "2025-01-02",
        rows=[
            {"ticker": "AAA", "score": 1, "prob": 0.1},
            {"ticker": "BBB", "score": 10, "prob": 0.9},
        ],
    )
    ledger = tmp_path / "ledger"
    _publish(scores, ledger, calendar)
    prices: dict[str, dict[str, float]] = {"SPY": {}, "AAA": {}, "BBB": {}}
    start = date(2025, 1, 2)
    for offset in range(64):
        day = start + timedelta(days=offset)
        prices["SPY"][day.isoformat()] = 100.0
        prices["AAA"][day.isoformat()] = 99.0 if offset == 63 else 100.0
        prices["BBB"][day.isoformat()] = 101.0 if offset == 63 else 100.0
    realised = tmp_path / "prices.json"
    realised.write_text(json.dumps(prices))

    main(["audit", str(ledger), "--prices", str(realised)])

    output = capsys.readouterr().out
    assert "PASS matured track record: 2 forecasts" in output
    result = audit(ledger, realised)
    assert result.status["track_record"]["matured"] == 2
    assert result.status["track_record"]["rank_ic"] == 1.0
    deciles = result.status["track_record"]["deciles"]
    assert deciles[0]["hit_rate"] == 0.0
    assert deciles[9]["hit_rate"] == 1.0
    assert deciles[0]["lo"] is not None and deciles[0]["hi"] is not None


def test_track_record_does_not_mature_on_two_prices(
    tmp_path: Path, calendar: _Calendar, capsys: pytest.CaptureFixture[str]
) -> None:
    scores = tmp_path / "scores.json"
    _scores(scores, "2025-01-02")
    ledger = tmp_path / "ledger"
    _publish(scores, ledger, calendar)
    prices = tmp_path / "prices.json"
    prices.write_text(
        json.dumps(
            {
                "SPY": {"2025-01-02": 100, "2025-01-03": 101},
                "AAA": {"2025-01-02": 100, "2025-01-03": 102},
            }
        )
    )

    main(["audit", str(ledger), "--prices", str(prices)])

    assert "none matured yet" in capsys.readouterr().out


def test_audit_fetches_a_ledger_url_through_main(
    tmp_path: Path, calendar: _Calendar, capsys: pytest.CaptureFixture[str]
) -> None:
    scores = tmp_path / "scores.json"
    _scores(scores, "2025-01-02")
    ledger = tmp_path / "ledger"
    _publish(scores, ledger, calendar)
    files = io.BytesIO()
    with zipfile.ZipFile(files, "w") as archive:
        for path in ledger.iterdir():
            for child in path.iterdir():
                archive.writestr(f"ledger/{path.name}/{child.name}", child.read_bytes())
    server = _Bundle(files.getvalue())
    try:
        main(["audit", server.url])
    finally:
        server.close()
    assert "PASS remote ledger bytes received:" in capsys.readouterr().out


def test_missing_ledger_fails_closed(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    with pytest.raises(SystemExit, match="audit failed"):
        main(["audit", str(tmp_path / "does-not-exist")])
    assert "FAIL missing ledger" in capsys.readouterr().out


def test_deploy_data_stops_before_writing_on_a_broken_chain(tmp_path: Path) -> None:
    repo = Path(__file__).parents[3]
    calendar = _Calendar()
    ledger = tmp_path / "ledger"
    try:
        scores = tmp_path / "scores.json"
        _scores(scores, "2025-01-02")
        _publish(scores, ledger, calendar)
        second = tmp_path / "second.json"
        _scores(second, "2025-01-03")
        _publish(second, ledger, calendar)
        second_manifest = ledger / "000002-2025-01-03" / "manifest.json"
        second_manifest.write_text(
            second_manifest.read_text().replace(
                '"previous_manifest_sha256": "', '"previous_manifest_sha256": "broken'
            )
        )
    except BaseException:
        calendar.close()
        raise
    environment = {
        **os.environ,
        "DEPLOY_LEDGER_DIR": str(ledger),
        "DEPLOY_DIR": str(tmp_path / "deploy"),
        "DEPLOY_DB": str(tmp_path / "db.sqlite"),
        "DEPLOY_CACHE": str(tmp_path / "cache"),
        "OTS_CALENDAR": calendar.url,
    }
    try:
        result = subprocess.run(
            ["scripts/deploy-data.sh", "--end", "2025-01-03"],
            cwd=repo,
            env=environment,
            capture_output=True,
            text=True,
        )
        output = result.stdout + result.stderr
        assert result.returncode != 0
        assert "FAIL chain" in output
        assert "expected" in output and "got" in output
        assert not (tmp_path / "deploy" / "scores.json").exists()
    finally:
        calendar.close()


def test_deploy_data_appends_a_second_pending_entry(tmp_path: Path) -> None:
    repo = Path(__file__).parents[3]
    calendar = _Calendar()
    universe = tmp_path / "synthetic.txt"
    universe.write_text("AAA,2000-01-03\n")
    environment = {
        **os.environ,
        "DEPLOY_LEDGER_DIR": str(tmp_path / "ledger"),
        "DEPLOY_DIR": str(tmp_path / "deploy"),
        "DEPLOY_DB": str(tmp_path / "db.sqlite"),
        "DEPLOY_CACHE": str(tmp_path / "cache"),
        "DEPLOY_UNIVERSE": str(universe),
        "DEPLOY_FACTORS_SOURCE": "synthetic",
        "OTS_CALENDAR": calendar.url,
    }
    try:
        for end in _deploy_test_ends():
            result = subprocess.run(
                [
                    "scripts/deploy-data.sh",
                    "--prices",
                    "synthetic",
                    "--filings",
                    "synthetic",
                    "--start",
                    "2020-01-01",
                    "--end",
                    str(end),
                ],
                cwd=repo,
                env=environment,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stdout + result.stderr
        entries = sorted((tmp_path / "ledger").glob("[0-9]" * 6 + "-*"))
        assert [entry.name[:6] for entry in entries] == ["000001", "000002"]
        status = json.loads((tmp_path / "deploy" / "ledger-status.json").read_text())
        assert status["forecasts_committed"] == 2
    finally:
        calendar.close()
