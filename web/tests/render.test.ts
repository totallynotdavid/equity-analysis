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

const NO_PAGES = new Set<string>();

describe("renderScores", () => {
  it("escapes the values it prints", () => {
    const html = renderScores(scores, new Set(["<b>X</b>"]));

    expect(html).toContain("&lt;b&gt;X&lt;/b&gt;");
    expect(html).not.toContain("<b>");
  });

  it("links a ticker to its page when the build made one", () => {
    const html = renderScores(
      { ...scores, rows: [{ ticker: "BRK-B", rank: 1, score: 10, prob: 0.6 }] },
      new Set(["BRK-B"]),
    );

    expect(html).toContain('<a class="underline" href="/t/BRK-B">BRK-B</a>');
  });

  it("leaves a ticker without a page as plain text", () => {
    const html = renderScores(
      {
        ...scores,
        rows: [
          { ticker: "AAPL", rank: 1, score: 10, prob: 0.6 },
          { ticker: "MSFT", rank: 2, score: 9, prob: 0.5 },
        ],
      },
      new Set(["AAPL"]),
    );

    expect(html).toContain('href="/t/AAPL"');
    expect(html).not.toContain("/t/MSFT");
    expect(html).toContain("MSFT");
  });

  it("names the tickers that were left out and why", () => {
    const html = renderScores(
      {
        ...scores,
        left_out: [
          { ticker: "AAA", reason: "no prices" },
          { ticker: "BBB", reason: "no filings" },
        ],
      },
      NO_PAGES,
    );

    expect(html).toContain(
      "Left out of the scores: AAA (no prices), BBB (no filings).",
    );
  });

  it("shortens a long list of left-out tickers", () => {
    const left_out = Array.from({ length: 12 }, (_, i) => ({
      ticker: `T${i}`,
      reason: "no filings",
    }));

    const html = renderScores({ ...scores, left_out }, NO_PAGES);

    expect(html).toContain("T9 (no filings) and 2 more.");
    expect(html).not.toContain("T10");
  });

  it("says nothing about left-out tickers when none were", () => {
    expect(renderScores(scores, NO_PAGES)).not.toContain("Left out");
    expect(renderScores({ ...scores, left_out: [] }, NO_PAGES)).not.toContain(
      "Left out",
    );
  });

  it("says there are no scores when given none", () => {
    expect(renderScores(null, NO_PAGES)).toContain("No scores yet");
  });
});
