import { copyFileSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { afterEach, describe, expect, it } from "vitest";
import Index from "../src/pages/index.astro";
import Page, { getStaticPaths } from "../src/pages/t/[ticker].astro";
import { loadTickers } from "../src/lib/tickers";

const FIXTURE = new URL("./fixtures/AAPL.json", import.meta.url);

function scoresFile(tickers: string[]): string {
  const file = join(mkdtempSync(join(tmpdir(), "scores-")), "scores.json");
  writeFileSync(
    file,
    JSON.stringify({
      status: "experimental, not validated",
      as_of: "2026-09-30",
      universe: "demo30",
      price_source: "tiingo",
      facts_source: "edgar",
      horizon_days: 63,
      rows: tickers.map((ticker, i) => ({
        ticker,
        rank: i + 1,
        score: 10 - i,
        prob: 0.6,
      })),
    }),
  );
  return file;
}

function tickersDir(): string {
  const directory = mkdtempSync(join(tmpdir(), "tickers-"));
  copyFileSync(FIXTURE, join(directory, "AAPL.json"));
  return directory;
}

afterEach(() => {
  delete process.env.TICKERS_DIR;
  delete process.env.SCORES_JSON;
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

    const paths = await getStaticPaths({} as never);

    expect(paths.map((path) => path.params.ticker)).toEqual(["AAPL"]);
  });

  it("shows the fundamentals of the ticker with links to its filings", async () => {
    process.env.TICKERS_DIR = tickersDir();
    const [path] = await getStaticPaths({} as never);

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

describe("the links of the index", () => {
  async function index(tickers: string[]): Promise<string> {
    process.env.SCORES_JSON = scoresFile(tickers);
    const container = await AstroContainer.create();
    return container.renderToString(Index);
  }

  it("lead only to tickers the build made a page for", async () => {
    process.env.TICKERS_DIR = tickersDir();
    const paths = await getStaticPaths({} as never);

    const html = await index(["AAPL", "MSFT"]);

    const linked = [...html.matchAll(/href="\/t\/([^"]+)"/g)].map((m) => m[1]);
    expect(linked).toEqual(paths.map((path) => path.params.ticker));
    expect(html).toContain("MSFT");
  });

  it("do not exist when no reports were published", async () => {
    process.env.TICKERS_DIR = join(tickersDir(), "missing");

    const html = await index(["AAPL", "MSFT"]);

    expect(html).not.toContain('href="/t/');
    expect(html).toContain("AAPL");
    expect(html).toContain("MSFT");
  });
});
