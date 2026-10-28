import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { afterEach, describe, expect, it } from "vitest";
import type { Evidence } from "../src/lib/evidence";
import Evidence_ from "../src/pages/evidence.astro";
import Index from "../src/pages/index.astro";
import Method from "../src/pages/method.astro";
import held from "./fixtures/evidence.json";
import legacy from "./fixtures/legacy-scores.json";
import { clearSite, report, scores, site, verdictOf } from "./support";

const HELD = held as unknown as Evidence;
const WARNING = 'role="alert"';

async function render(
  component: Parameters<AstroContainer["renderToString"]>[0],
) {
  const container = await AstroContainer.create();
  return container.renderToString(component);
}

function tickersOf(html: string): string[] {
  return [...html.matchAll(/href="\/t\/([^"]+)"/g)].map((m) => m[1]);
}

function embedded(html: string) {
  const json = /<script[^>]*id="screener-data"[^>]*>(.*?)<\/script>/s.exec(
    html,
  );
  if (json === null) throw new Error("no embedded screener data");
  return JSON.parse(json[1]);
}

afterEach(clearSite);

describe("the synthetic-data warning", () => {
  async function withSources(sources: {
    price_source?: string;
    facts_source?: string | null;
  }) {
    site({
      scores: { ...scores("demo30", ["MA"]), ...sources } as never,
      reports: [report({ ticker: "MA" })],
    });
    return render(Index);
  }

  it("is absent when both sources are real", async () => {
    const html = await withSources({
      price_source: "tiingo",
      facts_source: "edgar",
    });

    expect(html).not.toContain(WARNING);
  });

  it("names synthetic filings behind real prices", async () => {
    const html = await withSources({
      price_source: "tiingo",
      facts_source: "synthetic",
    });

    expect(html).toContain(WARNING);
    expect(html).toContain("synthetic filings");
    expect(html).not.toContain("synthetic prices");
  });

  it("names synthetic prices behind real filings", async () => {
    const html = await withSources({
      price_source: "synthetic",
      facts_source: "edgar",
    });

    expect(html).toContain(WARNING);
    expect(html).toContain("synthetic prices");
    expect(html).not.toContain("synthetic filings");
  });

  it("names both when both are synthetic", async () => {
    const html = await withSources({
      price_source: "synthetic",
      facts_source: "synthetic",
    });

    expect(html).toContain("synthetic prices and synthetic filings");
  });

  it("is absent for real prices when no filings were stored", async () => {
    const html = await withSources({
      price_source: "tiingo",
      facts_source: null,
    });

    expect(html).not.toContain(WARNING);
  });

  it("warns for scores that record no sources", async () => {
    site({
      scores: {
        ...scores("demo30", ["MA"]),
        price_source: undefined,
        facts_source: undefined,
      } as never,
      reports: [report({ ticker: "MA" })],
    });

    const html = await render(Index);

    expect(html).toContain(WARNING);
    expect(html).toContain("prices of unknown source");
  });
});

describe("the screener page", () => {
  const reports = [
    report({ ticker: "AAA", revenue: 500e6 }),
    report({ ticker: "BBB", revenue: 100e6 }),
    report({ ticker: "CCC", revenue: 2000e6 }),
  ];

  it("links every stock to its page", async () => {
    site({ scores: scores("demo30", ["AAA", "BBB"]), reports });

    const html = await render(Index);

    expect(tickersOf(html).sort()).toEqual(["AAA", "BBB", "CCC"]);
  });

  it("shows the per-date holdout diagnostic and its interval", async () => {
    site({ scores: scores("demo30", ["AAA"]), reports });

    const html = await render(Index);

    expect(html).toContain('id="model-note"');
    expect(html).toContain("pooled holdout AUC 0.480");
    expect(html).toContain("Mean per-date holdout AUC 0.470");
    expect(html).toContain("95% block-bootstrap interval 0.400–0.560");
  });

  it("labels the unchanged deploy artifact as pooled and omits new metrics", async () => {
    site({ scores: legacy as never, reports });

    const html = await render(Index);

    expect(html).toContain("pooled holdout AUC 0.432");
    expect(html).not.toContain("Mean per-date holdout AUC");
    expect(html).not.toContain("no interval");
  });

  it("carries its data to the browser, with markup made inert", async () => {
    site({
      scores: scores("demo30", ["AAA"]),
      reports: [report({ ticker: "AAA", name: "</script><b>x" })],
    });

    const html = await render(Index);

    expect(html).not.toContain("</script><b>");
    expect(embedded(html).rows[0].name).toBe("</script><b>x");
  });

  it("shows no score until a held-out run passed, and says why", async () => {
    site({
      scores: scores("demo30", ["AAA"]),
      reports,
      evidence: HELD,
    });

    const html = await render(Index);

    expect(html).toContain('id="score-note"');
    expect(html).toContain('href="/evidence"');
    expect(html).not.toContain('data-sort="score"');
    expect(embedded(html).score.shown).toBe(false);
  });

  it("shows the score, sorted first, once the checks pass", async () => {
    site({
      scores: scores("demo30", ["AAA", "BBB"]),
      reports,
      evidence: verdictOf(HELD, "demo30", "pass"),
    });

    const html = await render(Index);

    expect(html).toContain('data-sort="score"');
    expect(tickersOf(html)).toEqual(["AAA", "BBB", "CCC"]);
    expect(embedded(html).score.shown).toBe(true);
  });

  it("does not show a score from the evidence of another universe", async () => {
    site({
      scores: scores("demo30", ["AAA"]),
      reports,
      evidence: verdictOf(HELD, "sp500", "pass"),
    });

    expect(await render(Index)).not.toContain('data-sort="score"');
  });

  it("asks for a run when no report was published", async () => {
    site({ scores: scores("demo30", ["AAA"]), reports: [] });

    const html = await render(Index);

    expect(html).toContain("No ticker reports yet");
    expect(tickersOf(html)).toEqual([]);
  });

  it("builds before the first run", async () => {
    site({});

    expect(await render(Index)).toContain("No ticker reports yet");
  });
});

describe("the evidence page", () => {
  it("is rendered from the stored run", async () => {
    site({ evidence: HELD });

    const html = await render(Evidence_);

    expect(html).toContain("<title>Evidence · index</title>");
    expect(html).toContain('id="evidence-root"');
    expect(html).toContain(HELD.verdict.checks[0].name);
    expect(html).toContain(HELD.trials[0].description);
  });

  it("says no backtest was stored when there is no file", async () => {
    site({});

    const html = await render(Evidence_);

    expect(html).toContain('id="no-evidence"');
  });
});

describe("the method page", () => {
  it("lists the metrics of the published reports and the licences", async () => {
    site({ reports: [report({ ticker: "AAA" })] });

    const html = await render(Method);

    expect(html).toContain('data-metric="revenue"');
    expect(html).toContain('id="licences"');
    expect(html).toContain("Tiingo");
  });
});

describe("the navigation", () => {
  it("leads to all three pages from each", async () => {
    site({});

    for (const page of [Index, Evidence_, Method]) {
      const html = await render(page);
      for (const href of ["/", "/evidence", "/method"])
        expect(html).toContain(`href="${href}"`);
    }
  });
});
