import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { afterEach, describe, expect, it } from "vitest";
import Page from "../src/pages/index.astro";

const WARNING = 'role="alert"';

async function render(sources: {
  price_source?: string;
  facts_source?: string | null;
}): Promise<string> {
  const file = join(mkdtempSync(join(tmpdir(), "scores-")), "scores.json");
  writeFileSync(
    file,
    JSON.stringify({
      status: "experimental, not validated",
      as_of: "2026-09-30",
      universe: "demo30",
      ...sources,
      horizon_days: 63,
      rows: [{ ticker: "MA", rank: 1, score: 10, prob: 0.6 }],
    }),
  );
  process.env.SCORES_JSON = file;
  const container = await AstroContainer.create();
  return container.renderToString(Page);
}

afterEach(() => {
  delete process.env.SCORES_JSON;
});

describe("the synthetic-data warning", () => {
  it("is absent when both sources are real", async () => {
    const html = await render({
      price_source: "tiingo",
      facts_source: "edgar",
    });

    expect(html).not.toContain(WARNING);
  });

  it("names synthetic filings behind real prices", async () => {
    const html = await render({
      price_source: "tiingo",
      facts_source: "synthetic",
    });

    expect(html).toContain(WARNING);
    expect(html).toContain("synthetic filings");
    expect(html).not.toContain("synthetic prices");
  });

  it("names synthetic prices behind real filings", async () => {
    const html = await render({
      price_source: "synthetic",
      facts_source: "edgar",
    });

    expect(html).toContain(WARNING);
    expect(html).toContain("synthetic prices");
    expect(html).not.toContain("synthetic filings");
  });

  it("names both when both are synthetic", async () => {
    const html = await render({
      price_source: "synthetic",
      facts_source: "synthetic",
    });

    expect(html).toContain("synthetic prices and synthetic filings");
  });

  it("is absent for real prices when no filings were stored", async () => {
    const html = await render({ price_source: "tiingo", facts_source: null });

    expect(html).not.toContain(WARNING);
  });

  it("warns for scores that record no sources", async () => {
    const html = await render({});

    expect(html).toContain(WARNING);
    expect(html).toContain("prices of unknown source");
  });
});
