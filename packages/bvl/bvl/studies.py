"""Runs each study on the panel and writes its figures and `numbers.json`.

A study's README cites the numbers file, so a rerun on a newer panel updates
the evidence and leaves the prose to be checked against it.
"""

import json

from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from bvl import disclosure, events, figures, issuers, liquidity, panel


if TYPE_CHECKING:
    from pathlib import Path

SOURCE = "Source: SMV open data API (mvnet.smv.gob.pe), BVL daily quotes."
# MSCI's review of Peru's Emerging Markets status, from its press releases.
MSCI_MARKS = {
    "2015-08-03": "MSCI consults on Frontier",
    "2015-09-30": "Peru kept, on review",
    "2016-06-14": "Peru stays Emerging",
}


def run_liquidity(db: Path, out: Path) -> dict[str, object]:
    quotes = liquidity.local_quotes(db)
    coverage = liquidity.coverage_by_year(quotes)
    amihud = liquidity.monthly_amihud(quotes)
    yearly = liquidity.illiquidity_by_year(amihud)
    monthly = liquidity.time_effects(
        np.log(amihud["amihud"]), amihud["ticker"], amihud["month"]
    )
    monthly.index = pd.PeriodIndex(monthly.index).to_timestamp()
    complete = coverage.loc[coverage.index < pd.Timestamp.today().year]

    figures.lines(
        {"local stocks that traded": complete["stocks"]},
        out / "active-stocks.png",
        title="Local stocks with at least one trade, per year",
        ylabel="stocks",
        note=SOURCE,
    )
    figures.lines(
        {
            "traded on 80% or more of days": complete["daily_share"] * 100,
            "traded on under 20% of days": complete["rare_share"] * 100,
        },
        out / "coverage.png",
        title="How often a listed local stock trades",
        ylabel="% of active stocks",
        note=SOURCE + " Coverage counts days between a stock's first and last trade.",
    )
    figures.lines(
        {
            "monthly": monthly,
            "12-month mean": monthly.rolling(12, min_periods=6).mean(),
        },
        out / "illiquidity.png",
        title="Price impact of trading (Amihud), net of each stock's own level",
        ylabel="log illiquidity vs mean month",
        note=SOURCE + " Higher: the same amount traded moves the price more.",
        span=("2015-08-03", "2016-06-14", "MSCI review "),
    )

    window = monthly.loc["2014-01":"2017-12"]
    figures.lines(
        {"monthly": window},
        out / "illiquidity-msci.png",
        title="Illiquidity around MSCI's 2015-16 review of Peru",
        ylabel="log illiquidity vs mean month",
        note=SOURCE,
        marks=MSCI_MARKS,
    )

    numbers: dict[str, object] = {
        "first_day": quotes["date"].min().date().isoformat(),
        "last_day": quotes["date"].max().date().isoformat(),
        "local_tickers": int(quotes["ticker"].nunique()),
        "stock_months": len(amihud),
        "coverage_by_year": _records(complete.round(3)),
        "illiquidity_by_year": _records(yearly.round(3).rename("effect")),
        "msci": _msci_shift(monthly),
    }
    _write_numbers(out, numbers)
    return numbers


# Quotes this long before the first event give it a full baseline.
DISCLOSURE_QUOTES_FROM = "2008-01-01"
SEED = 20261114
# (measure, statistic, label) compared between events and placebo days.
EVENT_MEASURES = (
    ("abs_car_pre5", "mean", "|abnormal return|, days -5 to -1"),
    ("abs_car_pre1", "mean", "|abnormal return|, day -1"),
    ("abs_car_event", "mean", "|abnormal return|, days 0 to 1"),
    ("signed_pre5", "mean", "days -5 to -1 return in the direction of days 0 to 1"),
    ("signed_pre1", "mean", "day -1 return in the direction of days 0 to 1"),
    ("log_volume_pre5", "mean", "log(1 + amount / baseline), days -5 to -1"),
    ("log_volume_event", "mean", "log(1 + amount / baseline), days 0 to 1"),
    ("traded_pre5", "mean", "share of days -5 to -1 with a trade"),
    ("traded_event", "mean", "share of days 0 to 1 with a trade"),
)
# Measures repeated per topic, period and decision-to-filing gap.
FOCUS = ("abs_car_event", "signed_pre5", "log_volume_pre5")
PERIODS = {"2010-14": (2010, 2014), "2015-19": (2015, 2019), "2020-26": (2020, 2026)}
GAPS = {
    "same day": (0, 0),
    "next day": (1, 1),
    "2 to 7 days": (2, 7),
    "8+ days": (8, 10_000),
}
PROFILE_SPAN = (-10, 10)
# A group with fewer events gets a count and no estimates.
MIN_EVENTS = 30


def run_disclosure(db: Path, out: Path) -> dict[str, object]:
    rng = np.random.default_rng(SEED)
    quotes = liquidity.local_quotes(db)
    quotes = quotes[quotes["date"] >= DISCLOSURE_QUOTES_FROM]
    grid = disclosure.build_grid(quotes, panel.market_log_level(quotes))
    filings = events.load(db)
    sample, filing_days = _event_sample(grid, quotes, filings)
    placebos = disclosure.placebo_days(grid, sample, filing_days, rng)

    actual = _with_signed(
        disclosure.measure(grid, sample["column"].to_numpy(), sample["day0"].to_numpy())
    ).set_index(sample.index)
    placebo = _with_signed(
        disclosure.measure(
            grid, placebos["column"].to_numpy(), placebos["day0"].to_numpy()
        )
    ).set_index(pd.Index(placebos["event"]))
    clusters = sample["day0"]

    def compare(rows: pd.Index, measures: tuple[str, ...]) -> dict[str, Any]:
        kept = placebo[placebo.index.isin(rows)]
        result: dict[str, Any] = {"events": len(rows)}
        if len(rows) < MIN_EVENTS:
            return result
        for name, statistic, _ in EVENT_MEASURES:
            if name not in measures:
                continue
            point, low, high = disclosure.excess(
                actual.loc[rows, name], kept[name], clusters.loc[rows], statistic, rng
            )
            result[name] = {
                "events": round(_stat(actual.loc[rows, name], statistic), 5),
                "placebo": round(_stat(kept[name], statistic), 5),
                "excess": round(point, 5),
                "low": round(low, 5),
                "high": round(high, 5),
            }
        return result

    every = tuple(name for name, _, _ in EVENT_MEASURES)
    overall = compare(sample.index, every)
    by_topic = {
        name: compare(
            sample.index[sample["topics"].map(lambda t, n=name: n in t)], FOCUS
        )
        for name in events.TOPICS
    }
    year = sample["filed_at"].dt.year
    by_period = {
        name: compare(sample.index[year.between(a, b)], FOCUS)
        for name, (a, b) in PERIODS.items()
    }
    gap = (sample["filed_at"].dt.normalize() - sample["agreed_on"]).dt.days
    by_gap = {
        name: compare(sample.index[gap.between(a, b)], FOCUS)
        for name, (a, b) in GAPS.items()
    }
    gap_shares = _gap_shares(gap, year)

    _disclosure_figures(grid, sample, placebos, by_topic, gap_shares, out)
    numbers: dict[str, object] = {
        "filings": len(filings),
        "filings_in_events": int(sample["filings"].sum()),
        "events": len(sample),
        "issuers": int(sample["ruc"].nunique()),
        "tickers": int(sample["ticker"].nunique()),
        "first_event": sample["filed_at"].min().date().isoformat(),
        "last_event": sample["filed_at"].max().date().isoformat(),
        "placebo_days": len(placebos),
        "measures": {name: label for name, _, label in EVENT_MEASURES},
        "overall": overall,
        "by_topic": by_topic,
        "by_period": by_period,
        "by_gap": by_gap,
        "gap_shares_by_year": _records(gap_shares.round(3)),
    }
    _write_numbers(out, numbers)
    return numbers


def _event_sample(
    grid: disclosure.Grid, quotes: pd.DataFrame, filings: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    """Clean events, one per stock and day 0, and every issuer filing day per ticker.

    Filings that share an issuer's day 0 merge into one event with every
    topic among them. A filing after the last market day is dropped.
    """
    names = quotes.sort_values("date", ascending=False).drop_duplicates(
        ["ticker", "name"]
    )[["ticker", "name"]]
    links = issuers.link(names, filings.drop_duplicates("ruc")[["ruc", "company"]])
    linked = filings.merge(links[["ticker", "ruc"]], on="ruc")
    linked["day0"] = disclosure.day_zero(linked["filed_at"], grid.days)
    linked = linked[linked["day0"] < len(grid.days)]
    picked = disclosure.most_traded(
        grid, linked.rename(columns={"filing": "event"})[["event", "ticker", "day0"]]
    ).rename(columns={"event": "filing"})
    chosen = filings.merge(picked, on="filing")
    filing_days = {
        str(ticker): np.unique(days.to_numpy())
        for ticker, days in linked.groupby("ticker")["day0"]
    }
    merged = chosen.groupby(["ticker", "day0"], as_index=False).agg(
        ruc=("ruc", "first"),
        column=("column", "first"),
        filed_at=("filed_at", "min"),
        agreed_on=("agreed_on", "min"),
        filings=("filing", "size"),
        topics=("topics", lambda t: frozenset().union(*t)),
    )
    return merged[disclosure.clean(merged)].reset_index(drop=True), filing_days


def _with_signed(measures: pd.DataFrame) -> pd.DataFrame:
    """Add |CAR|, pre-event returns signed by the day 0 to 1 reaction, and log volume.

    The amount ratio is zero on most days of a thin stock and very large on a
    few, so its log(1 + ratio) is averaged instead.

    A leak moves the price before the filing in the direction the filing then
    moves it, so the signed return is positive on average; on a placebo day it
    is the plain autocorrelation of returns.
    """
    direction = np.sign(measures["car_event"])
    return measures.assign(
        abs_car_pre5=measures["car_pre5"].abs(),
        abs_car_pre1=measures["car_pre1"].abs(),
        abs_car_event=measures["car_event"].abs(),
        signed_pre5=measures["car_pre5"] * direction,
        signed_pre1=measures["car_pre1"] * direction,
        log_volume_pre5=np.log1p(measures["volume_pre5"]),
        log_volume_event=np.log1p(measures["volume_event"]),
    )


def _stat(values: pd.Series, statistic: str) -> float:
    return float(values.median() if statistic == "median" else values.mean())


def _gap_shares(gap: pd.Series, year: pd.Series) -> pd.DataFrame:
    """Per year, the share of events in each decision-to-filing gap."""
    known = gap.notna() & (gap >= 0)
    label = pd.Series(pd.NA, index=gap.index, dtype="object")
    for name, (a, b) in GAPS.items():
        label[gap.between(a, b)] = name
    table = pd.crosstab(year[known].rename("year"), label[known], normalize="index")
    return table.reindex(columns=list(GAPS), fill_value=0.0)


def _disclosure_figures(
    grid: disclosure.Grid,
    sample: pd.DataFrame,
    placebos: pd.DataFrame,
    by_topic: dict[str, dict[str, Any]],
    gap_shares: pd.DataFrame,
    out: Path,
) -> None:
    note = SOURCE + " Events: SMV material-event filings, 2010 on."
    event_returns, event_amount, event_traded = disclosure.profile(
        grid, sample["column"].to_numpy(), sample["day0"].to_numpy(), PROFILE_SPAN
    )
    placebo_returns, placebo_amount, placebo_traded = disclosure.profile(
        grid, placebos["column"].to_numpy(), placebos["day0"].to_numpy(), PROFILE_SPAN
    )
    offsets = pd.Index(range(PROFILE_SPAN[0], PROFILE_SPAN[1] + 1), name="day")
    figures.lines(
        {
            "filing": pd.Series(
                np.nanmean(np.abs(event_returns), axis=0) * 100, offsets
            ),
            "placebo day": pd.Series(
                np.nanmean(np.abs(placebo_returns), axis=0) * 100, offsets
            ),
        },
        out / "abs-return-profile.png",
        title="Size of the daily abnormal move around a material-event filing",
        ylabel="mean |abnormal return|, %",
        note=note + " Day 0: first market day on or after the filing date.",
    )
    figures.lines(
        {
            "filing": pd.Series(np.nanmean(event_traded, axis=0) * 100, offsets),
            "placebo day": pd.Series(np.nanmean(placebo_traded, axis=0) * 100, offsets),
        },
        out / "traded-profile.png",
        title="Share of stocks that trade on each day around a filing",
        ylabel="% of events with a trade",
        note=note,
    )
    figures.lines(
        {
            "filing": pd.Series(np.nanmean(np.log1p(event_amount), axis=0), offsets),
            "placebo day": pd.Series(
                np.nanmean(np.log1p(placebo_amount), axis=0), offsets
            ),
        },
        out / "amount-profile.png",
        title="Traded amount around a filing, against the stock's own baseline",
        ylabel="mean log(1 + amount / baseline)",
        note=note + " Baseline: mean daily amount over days -250 to -30.",
    )
    signed = {
        topic: result["signed_pre5"]
        for topic, result in by_topic.items()
        if isinstance(result.get("signed_pre5"), dict)
    }
    excess = pd.Series({t: v["excess"] * 100 for t, v in signed.items()})
    figures.bars(
        excess,
        out / "pre-run-up-by-topic.png",
        title="Pre-filing move in the direction of the reaction, over placebo",
        ylabel="excess signed return, days -5 to -1, %",
        note=note + " Bars: 95% bootstrap interval clustered by day 0.",
        errors=(
            pd.Series({t: v["low"] * 100 for t, v in signed.items()}),
            pd.Series({t: v["high"] * 100 for t, v in signed.items()}),
        ),
    )
    figures.lines(
        {name: gap_shares[name] * 100 for name in ("same day", "next day")},
        out / "filing-gap.png",
        title="When the decision behind a filing was taken",
        ylabel="% of events",
        note=note + " Gap: agreement date to filing date.",
    )


def _msci_shift(monthly: pd.Series) -> dict[str, float]:
    """Mean monthly illiquidity in the year before the consultation and after."""
    before = monthly.loc["2014-08":"2015-07"].mean()
    during = monthly.loc["2015-08":"2016-06"].mean()
    after = monthly.loc["2016-07":"2017-06"].mean()
    return {
        "before_2014_08_to_2015_07": round(float(before), 3),
        "review_2015_08_to_2016_06": round(float(during), 3),
        "after_2016_07_to_2017_06": round(float(after), 3),
    }


def _records(frame: pd.DataFrame | pd.Series) -> list[dict[str, object]]:
    table = frame.to_frame() if isinstance(frame, pd.Series) else frame
    records: list[dict[str, object]] = json.loads(
        table.reset_index().to_json(orient="records")
    )
    return records


def _write_numbers(out: Path, numbers: dict[str, object]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    text = json.dumps(numbers, indent=2, ensure_ascii=False) + "\n"
    (out / "numbers.json").write_text(text, encoding="utf-8")
