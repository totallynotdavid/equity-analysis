import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

export interface ScoreRow {
  ticker: string;
  rank: number;
  score: number;
  prob: number;
}

export interface LeftOut {
  ticker: string;
  reason: string;
}

export interface Scores {
  status: string;
  as_of: string;
  universe: string;
  price_source: string;
  facts_source: string | null;
  horizon_days: number;
  model?: {
    train_rows: number;
    holdout_rows: number;
    holdout_auc: number | null;
    holdout_auc_per_date?: number | null;
    holdout_auc_per_date_low?: number | null;
    holdout_auc_per_date_high?: number | null;
  };
  rows: ScoreRow[];
  left_out?: LeftOut[];
}

export function loadScores(): Scores | null {
  const path = resolve(process.env.SCORES_JSON ?? "../outputs/scores.json");
  if (!existsSync(path)) return null;
  return JSON.parse(readFileSync(path, "utf-8")) as Scores;
}
