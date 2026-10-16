import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

export interface Interval {
  value: number | null;
  lo: number | null;
  hi: number | null;
}

export interface EvidenceRun {
  universe: string;
  period: "development" | "holdout";
  holdout_start: string;
  first_date: string;
  last_date: string;
  dates: number;
  months: number;
  rows: number;
  independent_windows: number;
  base_rate: number;
  horizon_days: number;
  cost_bps: number;
  members: number;
  unpriced: number;
  price_source: string;
  facts_source: string | null;
  factors_source: string | null;
}

export interface Check {
  name: string;
  value: string;
  passed: boolean;
}

export interface Verdict {
  candidate: string;
  lightgbm_stays: boolean;
  decided: boolean;
  shown: boolean;
  checks: Check[];
}

export interface Portfolio {
  gross: Interval;
  net: Interval;
  sharpe: number | null;
  turnover: number;
  break_even: Interval | null;
}

export interface Ladder {
  cost_bps: number;
  net: number;
}

export interface FactorAlpha {
  annual: number;
  t_stat: number;
  r2: number;
  months: number;
  loadings: Record<string, number>;
}

export interface DeflatedSharpe {
  sharpe: number;
  benchmark: number;
  probability: number;
  trials: number;
}

export interface ModelEvidence {
  model: string;
  mean_ic: Interval;
  ic_t_stat: number | null;
  mean_auc: Interval;
  top_hit_rate: Interval;
  spread_excess: Interval;
  top: Portfolio;
  spread: Portfolio;
  ladder: Ladder[];
  alpha: FactorAlpha | null;
  deflated: DeflatedSharpe;
  selection_ic: number;
  selection_net_spread: number;
}

export interface YearEvidence {
  year: number;
  dates: number;
  months: number;
  ic: Record<string, Interval>;
}

export interface ScoreEvidence {
  score: number;
  rows: number;
  hit_rate: Interval;
  mean_excess: Interval;
}

export interface FeatureEvidence {
  feature: string;
  family: "technical" | "fundamental";
  mean_ic: Interval;
  t_stat: number | null;
  dates: number;
}

export interface Trial {
  description: string;
  as_of: string;
  months: number;
  sharpe: number;
}

export interface Evidence {
  status: string;
  run: EvidenceRun;
  verdict: Verdict;
  models: ModelEvidence[];
  by_year: YearEvidence[];
  by_score: ScoreEvidence[];
  features: FeatureEvidence[];
  feature_hurdle_t: number;
  trials: Trial[];
}

export function loadEvidence(): Evidence | null {
  const path = resolve(process.env.EVIDENCE_JSON ?? "../outputs/evidence.json");
  if (!existsSync(path)) return null;
  return JSON.parse(readFileSync(path, "utf-8")) as Evidence;
}
