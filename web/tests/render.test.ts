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

  it("says there are no scores when given none", () => {
    expect(renderScores(null)).toContain("No scores yet");
  });
});
