import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Evidence } from "../src/lib/evidence";
import type { Scores } from "../src/lib/scores";
import type { Part, Point, Series, TickerReport } from "../src/lib/tickers";

const FILING = {
  accn: "0000000000-26-000001",
  form: "10-Q",
  fy: 2026,
  fp: "Q2",
  url: "https://www.sec.gov/Archives/edgar/data/1/000000000026000001/0000000000-26-000001-index.htm",
};

interface Figures {
  ticker: string;
  name?: string;
  sector?: string | null;
  /** Dollars. */
  revenue?: number | null;
  /** A ratio, 0.3 for 30%. */
  op_margin?: number | null;
  revenue_pct?: number | null;
  /** A ratio, 0.1 for 10%. */
  net_margin?: number | null;
  filed?: string;
}

function part(metric: string, tag: string, value: number, filed: string): Part {
  return {
    metric,
    sign: 1,
    tag,
    start: "2025-07-01",
    end: "2026-06-30",
    value,
    filing: { ...FILING, filed },
  };
}

function series(
  metric: string,
  label: string,
  group: string,
  unit: string,
  value: number,
  pct: number | null,
  filed: string,
  formula: string | null,
  tag: string,
): Series {
  const point: Point = {
    end: "2026-06-30",
    value,
    formula,
    parts: [part(metric, tag, value, filed)],
  };
  return {
    metric,
    group,
    label,
    unit,
    pct_universe: pct,
    pct_sector: null,
    points: [point],
  };
}

/** A ticker report with the figures a screener test cares about. */
export function report(figures: Figures): TickerReport {
  const filed = figures.filed ?? "2026-08-01";
  const all: Series[] = [];
  if (figures.revenue !== null)
    all.push(
      series(
        "revenue",
        "Revenue",
        "Income statement",
        "USD",
        figures.revenue ?? 1e9,
        figures.revenue_pct ?? null,
        filed,
        null,
        "Revenues",
      ),
    );
  if (figures.op_margin !== null)
    all.push(
      series(
        "op_margin",
        "Operating margin",
        "Ratios",
        "ratio",
        figures.op_margin ?? 0.2,
        null,
        filed,
        "operating income / revenue",
        "OperatingIncomeLoss",
      ),
    );
  if (figures.net_margin !== null)
    all.push(
      series(
        "net_margin",
        "Net margin",
        "Ratios",
        "ratio",
        figures.net_margin ?? 0.1,
        null,
        filed,
        "net income / revenue",
        "NetIncomeLoss",
      ),
    );
  return {
    ticker: figures.ticker,
    universe: "test",
    as_of: "2026-09-30",
    facts_source: "edgar",
    name: figures.name ?? `${figures.ticker} Inc.`,
    cik: 1,
    sic: "7372",
    sic_description: "Services",
    sector: figures.sector === undefined ? "Technology" : figures.sector,
    series: all,
  };
}

/**
 * The valuation and the risk that `eq run` also writes into a ticker report,
 * worked out from prices. A page must show none of it.
 */
export const PRICE_FIGURES = {
  valuation: {
    status: "ok",
    reason: null,
    model: {
      price: 123.45,
      market_value: 7.7e9,
      implied_growth: 0.0613,
      tv_share: 0.7,
    },
  },
  risk: {
    status: "ok",
    reason: null,
    window: 500,
    fit: {
      beta: { value: 1.37, se: 0.1, lo: 1.17, hi: 1.57 },
      volatility: 0.2841,
      max_drawdown: -0.3377,
    },
  },
};

export function scores(universe: string, scored: string[]): Scores {
  return {
    status: "experimental, not validated",
    as_of: "2026-09-30",
    universe,
    price_source: "tiingo",
    facts_source: "edgar",
    horizon_days: 63,
    rows: scored.map((ticker, i) => ({
      ticker,
      rank: i + 1,
      score: Math.max(1, 10 - i),
      prob: 0.6,
    })),
  };
}

/**
 * Writes the files the loaders read into a fresh directory and points the
 * environment at them. `undefined` leaves a file out, as before a first run.
 */
export function site(files: {
  scores?: Scores;
  reports?: TickerReport[];
  evidence?: Evidence | unknown;
}): void {
  const directory = mkdtempSync(join(tmpdir(), "site-"));
  const tickers = join(directory, "tickers");
  mkdirSync(tickers);
  for (const r of files.reports ?? [])
    writeFileSync(join(tickers, `${r.ticker}.json`), JSON.stringify(r));
  process.env.TICKERS_DIR = tickers;
  process.env.SCORES_JSON = join(directory, "scores.json");
  process.env.EVIDENCE_JSON = join(directory, "evidence.json");
  if (files.scores !== undefined)
    writeFileSync(process.env.SCORES_JSON, JSON.stringify(files.scores));
  if (files.evidence !== undefined)
    writeFileSync(process.env.EVIDENCE_JSON, JSON.stringify(files.evidence));
}

export function clearSite(): void {
  delete process.env.TICKERS_DIR;
  delete process.env.SCORES_JSON;
  delete process.env.EVIDENCE_JSON;
}

/** An evidence file whose verdict is decided as given. */
export function verdictOf(
  base: Evidence,
  universe: string,
  outcome: "pass" | "fail" | "development" | "baseline",
): Evidence {
  const decided = outcome !== "development";
  const passed = outcome === "pass" || outcome === "baseline";
  return {
    ...base,
    run: {
      ...base.run,
      universe,
      period: decided ? "holdout" : "development",
    },
    verdict: {
      ...base.verdict,
      candidate: outcome === "baseline" ? "ridge" : "lightgbm",
      decided,
      shown: decided && outcome === "pass",
      checks: base.verdict.checks.map((check) => ({ ...check, passed })),
    },
  };
}
