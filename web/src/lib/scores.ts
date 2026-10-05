import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

export interface ScoreRow {
  ticker: string;
  rank: number;
  score: number;
  prob: number;
}

export interface Scores {
  status: string;
  as_of: string;
  universe: string;
  source: string;
  horizon_days: number;
  rows: ScoreRow[];
}

/**
 * The scores that `eq run` or `eq export` wrote, or null before the first run.
 * Set SCORES_JSON to read another file. The build runs from `web/`.
 */
export function loadScores(): Scores | null {
  const path = resolve(process.env.SCORES_JSON ?? "../outputs/scores.json");
  if (!existsSync(path)) return null;
  return JSON.parse(readFileSync(path, "utf-8")) as Scores;
}
