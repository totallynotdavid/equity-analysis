import { copyFileSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { afterEach, describe, expect, it } from "vitest";
import Page, { getStaticPaths } from "../src/pages/t/[ticker].astro";
import { loadTickers } from "../src/lib/tickers";
import { PRICE_FIGURES, clearSite, report, scores, site } from "./support";

const FIXTURE = new URL("./fixtures/AAPL.json", import.meta.url);

function tickersDir(): string {
  const directory = mkdtempSync(join(tmpdir(), "tickers-"));
  copyFileSync(FIXTURE, join(directory, "AAPL.json"));
  return directory;
}

afterEach(() => {
  delete process.env.TICKERS_DIR;
  clearSite();
});

describe("loadTickers", () => {
  it("reads every report in the directory, in ticker order", () => {
    const directory = tickersDir();
    copyFileSync(FIXTURE, join(directory, "AAA.json"));
    writeFileSync(join(directory, "notes.txt"), "not a report");
    process.env.TICKERS_DIR = directory;

    expect(loadTickers().map((report) => report.ticker)).toEqual([
      "AAPL",
      "AAPL",
    ]);
  });

  it("finds no reports before the first run", () => {
    process.env.TICKERS_DIR = join(tickersDir(), "missing");

    expect(loadTickers()).toEqual([]);
  });
});

describe("the ticker page", () => {
  it("has one page per report", async () => {
    process.env.TICKERS_DIR = tickersDir();

    const paths = getStaticPaths();

    expect(paths.map((path) => path.params.ticker)).toEqual(["AAPL"]);
  });

  it("shows the fundamentals of the ticker with links to its filings", async () => {
    process.env.TICKERS_DIR = tickersDir();
    const [path] = getStaticPaths();

    const container = await AstroContainer.create();
    const html = await container.renderToString(Page, {
      params: path.params,
      props: path.props,
    });

    expect(html).toContain("<title>AAPL · index</title>");
    expect(html).toContain("Apple Inc.");
    expect(html).toContain("$466,823M");
    expect(html).toContain(
      'href="https://www.sec.gov/Archives/edgar/data/320193/000032019326000020/0000320193-26-000020-index.htm"',
    );
    expect(html).toContain("Nothing here is investment advice");
  });
});

describe("a report that carries figures worked out from prices", () => {
  it("shows none of them, nor the price", async () => {
    const priced = { ...report({ ticker: "AAA" }), ...PRICE_FIGURES };
    site({ scores: scores("demo30", ["AAA"]), reports: [priced] });
    const [path] = getStaticPaths();

    const container = await AstroContainer.create();
    const html = await container.renderToString(Page, {
      params: path.params,
      props: path.props,
    });

    expect(html).toContain("Revenue");
    for (const shown of ["123.45", "1.37", "28.4%", "6.1%"])
      expect(html).not.toContain(shown);
    expect(html).not.toMatch(/valuation|beta|drawdown|volatility/i);
  });
});

describe("the build of the ticker pages", () => {
  const reports = ["AAA", "BBB", "CCC"].map((ticker) => report({ ticker }));

  it("makes a page for every scored ticker", async () => {
    site({ scores: scores("demo30", ["AAA", "BBB", "CCC"]), reports });

    const paths = getStaticPaths();

    expect(paths.map((path) => path.params.ticker)).toEqual([
      "AAA",
      "BBB",
      "CCC",
    ]);
  });

  it("makes a page for an unscored ticker too, so the screener can link it", async () => {
    site({ scores: scores("demo30", ["AAA"]), reports });

    const paths = getStaticPaths();

    expect(paths).toHaveLength(3);
  });

  it("fails the build when a scored ticker has no report", () => {
    site({ scores: scores("demo30", ["AAA", "ZZZ", "YYY"]), reports });

    expect(() => getStaticPaths()).toThrow(
      /2 scored tickers have no ticker report.*ZZZ, YYY.*eq export/,
    );
  });

  it("builds when there are no scores to check against", async () => {
    site({ reports });

    expect(getStaticPaths()).toHaveLength(3);
  });
});
