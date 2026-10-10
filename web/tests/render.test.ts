import { describe, expect, it } from "vitest";
import { renderScores } from "../src/lib/render";
import type { Scores } from "../src/lib/scores";

const scores: Scores = {
  status: "experimental, not validated",
  as_of: "2026-09-30",
  universe: "demo30",
  price_source: "tiingo",
  facts_source: "edgar",
  horizon_days: 63,
  rows: [{ ticker: "<b>X</b>", rank: 1, score: 10, prob: 0.6 }],
};

describe("renderScores", () => {
  it("escapes the values it prints", () => {
    const html = renderScores(scores);

    expect(html).toContain("&lt;b&gt;X&lt;/b&gt;");
    expect(html).not.toContain("<b>");
  });

  it("names the tickers that were left out and why", () => {
    const html = renderScores({
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

  it("shortens a long list of left-out tickers", () => {
    const left_out = Array.from({ length: 12 }, (_, i) => ({
      ticker: `T${i}`,
      reason: "no filings",
    }));

    const html = renderScores({ ...scores, left_out });

    expect(html).toContain("T9 (no filings) and 2 more.");
    expect(html).not.toContain("T10");
  });

  it("says nothing about left-out tickers when none were", () => {
    expect(renderScores(scores)).not.toContain("Left out");
    expect(renderScores({ ...scores, left_out: [] })).not.toContain("Left out");
  });

  it("says there are no scores when given none", () => {
    expect(renderScores(null)).toContain("No scores yet");
  });
});
