import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

export interface Filing {
  accn: string;
  form: string;
  fy: number;
  fp: string;
  filed: string;
  url: string;
}

export interface Part {
  metric: string;
  sign: 1 | -1;
  tag: string;
  start: string | null;
  end: string;
  value: number;
  filing: Filing;
}

export interface Point {
  end: string;
  value: number;
  formula: string | null;
  parts: Part[];
}

export interface Series {
  metric: string;
  group: string;
  label: string;
  unit: string;
  pct_universe: number | null;
  pct_sector: number | null;
  points: Point[];
}

export interface GridRow {
  wacc: number;
  implied_growth: number | null;
  values: number[];
}

export interface DcfModel {
  price: number;
  shares: number;
  market_value: number;
  net_debt: number;
  enterprise_value: number;
  fcf: number;
  fcf0: number;
  beta_raw: number;
  beta: number;
  risk_free: number;
  equity_risk_premium: number;
  cost_of_equity: number;
  cost_of_debt: number;
  tax_rate: number;
  wacc: number;
  terminal_growth: number;
  years: number;
  implied_growth: number;
  tv_share: number;
  growths: number[];
  rows: GridRow[];
}

export type Valuation =
  | { status: "ok"; reason: null; model: DcfModel }
  | { status: "not_meaningful"; reason: string; model: null };

export interface TickerReport {
  ticker: string;
  universe: string;
  as_of: string;
  facts_source: string;
  name: string;
  cik: number;
  sic: string;
  sic_description: string;
  sector: string | null;
  series: Series[];
  valuation?: Valuation | null;
}

/**
 * Loads the ticker reports that `eq run` or `eq export` wrote. It returns no
 * reports before the first run. Set TICKERS_DIR to read another directory.
 * The build runs from `web/`.
 */
export function loadTickers(): TickerReport[] {
  const directory = resolve(process.env.TICKERS_DIR ?? "../outputs/tickers");
  if (!existsSync(directory)) return [];
  return readdirSync(directory)
    .filter((name) => name.endsWith(".json"))
    .sort()
    .map(
      (name) =>
        JSON.parse(
          readFileSync(join(directory, name), "utf-8"),
        ) as TickerReport,
    );
}
