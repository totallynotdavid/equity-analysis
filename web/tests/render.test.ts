import { describe, expect, it } from "vitest";
import { escape, leftOutNote, sourcesWarning } from "../src/lib/render";
import type { Scores } from "../src/lib/scores";

const scores: Scores = {
  status: "experimental, not validated",
  as_of: "2026-09-30",
  universe: "demo30",
  price_source: "tiingo",
  facts_source: "edgar",
  horizon_days: 63,
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

describe("sourcesWarning", () => {
  it("is empty when both sources are real", () => {
    expect(sourcesWarning(scores)).toBe("");
  });

  it("is empty for real prices when no filings were stored", () => {
    expect(sourcesWarning({ ...scores, facts_source: null })).toBe("");
  });

  it("names synthetic filings behind real prices", () => {
    const html = sourcesWarning({ ...scores, facts_source: "synthetic" });

    expect(html).toContain('role="alert"');
    expect(html).toContain("synthetic filings");
    expect(html).not.toContain("synthetic prices");
  });

  it("names both when both are synthetic", () => {
    const html = sourcesWarning({
      ...scores,
      price_source: "synthetic",
      facts_source: "synthetic",
    });

    expect(html).toContain("synthetic prices and synthetic filings");
  });

  it("warns for scores that record no sources", () => {
    const html = sourcesWarning({
      ...scores,
      price_source: undefined,
      facts_source: undefined,
    } as unknown as Scores);

    expect(html).toContain("prices of unknown source");
  });
});
