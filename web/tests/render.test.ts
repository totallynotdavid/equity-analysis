import { describe, expect, it } from "vitest";
import { escape, leftOutNote } from "../src/lib/render";
import type { Scores } from "../src/lib/scores";

const scores: Scores = {
  status: "experimental, not validated",
  as_of: "2026-09-30",
  universe: "demo30",
  price_source: "tiingo",
  facts_source: "edgar",
  horizon_days: 63,
  model: {
    train_rows: 10,
    holdout_rows: 4,
    holdout_auc: 0.48,
    holdout_auc_per_date: 0.47,
    holdout_auc_per_date_low: 0.4,
    holdout_auc_per_date_high: 0.56,
  },
  rows: [{ ticker: "AAPL", rank: 1, score: 10, prob: 0.6 }],
};

describe("escape", () => {
  it("makes markup and quotes inert", () => {
    expect(escape(`<b a="x">'&</b>`)).toBe(
      "&lt;b a=&quot;x&quot;&gt;&#39;&amp;&lt;/b&gt;",
    );
  });
});

describe("leftOutNote", () => {
  it("names the tickers that were left out and why", () => {
    const html = leftOutNote({
      ...scores,
      left_out: [
        { ticker: "AAA", reason: "no prices" },
        { ticker: "BBB", reason: "no filings" },
      ],
    });

    expect(html).toContain(
      "Left out of the scores: AAA (no prices), BBB (no filings).",
    );
  });

  it("escapes the names it prints", () => {
    const html = leftOutNote({
      ...scores,
      left_out: [{ ticker: "<b>X</b>", reason: "no prices" }],
    });

    expect(html).toContain("&lt;b&gt;X&lt;/b&gt;");
    expect(html).not.toContain("<b>");
  });

  it("shortens a long list of left-out tickers", () => {
    const left_out = Array.from({ length: 12 }, (_, i) => ({
      ticker: `T${i}`,
      reason: "no filings",
    }));

    const html = leftOutNote({ ...scores, left_out });

    expect(html).toContain("T9 (no filings) and 2 more.");
    expect(html).not.toContain("T10");
  });

  it("says nothing when none were left out", () => {
    expect(leftOutNote(scores)).toBe("");
    expect(leftOutNote({ ...scores, left_out: [] })).toBe("");
  });
});
