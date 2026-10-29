import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { Window } from "happy-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ScreenerData } from "../src/lib/screener";
import { mount } from "../src/lib/screener-client";
import Index from "../src/pages/index.astro";
import Ticker, { getStaticPaths } from "../src/pages/t/[ticker].astro";
import { clearSite, report, site } from "./support";

const REPORTS = [
  report({
    ticker: "AAA",
    revenue: 500e6,
    revenue_pct: 50,
    net_margin: 0.1,
    sector: "Technology",
  }),
  report({
    ticker: "BBB",
    revenue: 100e6,
    revenue_pct: 10,
    net_margin: 0.3,
    sector: "Financials",
  }),
  report({
    ticker: "CCC",
    revenue: 2000e6,
    revenue_pct: 90,
    net_margin: 0.05,
    sector: "Technology",
  }),
  report({
    ticker: "DDD",
    revenue: 800e6,
    revenue_pct: 70,
    net_margin: 0.2,
    sector: "Financials",
  }),
];

const DEFAULT_HEAD = [
  "Ticker",
  "Name",
  "Sector",
  "Revenue",
  "Revenue percentile",
  "Operating margin",
  "Net margin",
  "Days since newest filing",
];

// One window per load: a reload is a new page.
let win: Window;
let controls: HTMLElement;
let results: HTMLElement;

function field(name: string): HTMLInputElement {
  const input = controls.querySelector<HTMLInputElement>(`[name="${name}"]`);
  if (input === null) throw new Error(`no field ${name}`);
  return input;
}

function box(name: string, value: string): HTMLInputElement {
  const input = controls.querySelector<HTMLInputElement>(
    `[name="${name}"][value="${value}"]`,
  );
  if (input === null) throw new Error(`no box ${name}=${value}`);
  return input;
}

function type(name: string, value: string): void {
  const input = field(name);
  input.value = value;
  input.dispatchEvent(new win.Event("input", { bubbles: true }) as never);
}

function toggle(name: string, value: string, checked: boolean): void {
  const input = box(name, value);
  input.checked = checked;
  input.dispatchEvent(new win.Event("input", { bubbles: true }) as never);
}

function sortBy(key: string): void {
  const link = results.querySelector<HTMLElement>(`a[data-sort="${key}"]`);
  if (link === null) throw new Error(`no header ${key}`);
  link.click();
}

function view() {
  const heads = [...results.querySelectorAll<HTMLElement>("thead th")];
  const ordered = heads.find((th) => th.getAttribute("aria-sort") !== "none");
  const key = ordered?.querySelector("a")?.getAttribute("data-sort");
  return {
    address: win.location.search,
    count: results.querySelector("#screener-count")?.textContent,
    tickers: [...results.querySelectorAll("tbody tr")].map(
      (row) => row.querySelector("a")?.textContent,
    ),
    head: heads.map((th) => th.textContent?.replace(/ [▲▼]$/, "")),
    sorted: `${ordered?.getAttribute("aria-sort")} ${key}`,
    sectors: [
      ...controls.querySelectorAll<HTMLInputElement>('[name="sector"]:checked'),
    ].map((input) => input.value),
    bounds: Object.fromEntries(
      [
        ...controls.querySelectorAll<HTMLInputElement>(
          '[name^="min."], [name^="max."]',
        ),
      ]
        .filter((input) => input.value !== "")
        .map((input) => [input.name, input.value]),
    ),
    columns: [
      ...controls.querySelectorAll<HTMLInputElement>('[name="col"]:checked'),
    ].map((input) => input.value),
  };
}

function cells(ticker: string): Record<string, string> {
  const label = (th: Element) => th.textContent?.replace(/ [▲▼]$/, "") ?? "";
  const head = [...results.querySelectorAll("thead th")].map(label);
  const row = [...results.querySelectorAll("tbody tr")].find(
    (tr) => tr.querySelector("a")?.textContent === ticker,
  );
  if (row === undefined) throw new Error(`no row ${ticker}`);
  const text = [...row.querySelectorAll("td")].map((td) => td.textContent);
  return Object.fromEntries(head.map((name, i) => [name, text[i] ?? ""]));
}

/** Renders a page of the site as the build does and parses it. */
async function render(
  component: Parameters<AstroContainer["renderToString"]>[0],
  options?: Parameters<AstroContainer["renderToString"]>[1],
): Promise<Document> {
  const container = await AstroContainer.create();
  const html = await container.renderToString(component, options);
  return new win.DOMParser().parseFromString(html, "text/html") as never;
}

/**
 * Follows a row link to the page the build made for it and reads its figures.
 * A link with no static path is a broken link.
 */
async function openTicker(ticker: string) {
  const link = [...results.querySelectorAll<HTMLAnchorElement>("tbody a")].find(
    (a) => a.textContent === ticker,
  );
  if (link === undefined) throw new Error(`no link ${ticker}`);
  const href = link.getAttribute("href") ?? "";
  const path = getStaticPaths().find(
    ({ params }) => `/t/${encodeURIComponent(params.ticker)}` === href,
  );
  if (path === undefined) throw new Error(`no page at ${href}`);
  const page = await render(Ticker, {
    params: path.params,
    props: path.props,
  });
  const row = (metric: string) => {
    const tds = [
      ...page.querySelectorAll(`tr[data-metric="${metric}"] td`),
    ].map((td) => td.textContent);
    return { universe: tds[0], latest: tds.at(-1) };
  };
  return {
    title: page.title,
    heading: page.querySelector("#ticker-root h1")?.textContent,
    sector: page.querySelector("#identity")?.textContent?.split(".")[0],
    revenue: row("revenue"),
    net_margin: row("net_margin"),
  };
}

/**
 * What a browser does on a load: the built page arrives, its script reads the
 * embedded data and mounts the screener on the address.
 */
async function load(query: string): Promise<void> {
  win = new Window({ url: `http://localhost/${query}` });
  vi.stubGlobal("FormData", win.FormData);
  const page = await render(Index);
  win.document.body.innerHTML = page.body.innerHTML;
  controls = win.document.getElementById("screener-controls") as never;
  results = win.document.getElementById("screener-results") as never;
  const json = win.document.getElementById("screener-data") as never as Element;
  mount(
    controls,
    results,
    JSON.parse(json.textContent) as ScreenerData,
    win as never,
  );
}

beforeEach(() => {
  site({ reports: REPORTS });
});

afterEach(() => {
  clearSite();
  vi.unstubAllGlobals();
});

describe("a reader's walk through the screener and a ticker page", () => {
  it("keeps the table, the address, the form and the ticker page agreeing at every step", async () => {
    await load("");
    expect(view()).toEqual({
      address: "",
      count: "4 of 4 stocks.",
      tickers: ["AAA", "BBB", "CCC", "DDD"],
      head: DEFAULT_HEAD,
      sorted: "ascending ticker",
      sectors: [],
      bounds: {},
      columns: [
        "revenue",
        "revenue.pct",
        "op_margin",
        "net_margin",
        "filing_age",
      ],
    });

    toggle("sector", "Technology", true);
    expect(view()).toMatchObject({
      address: "?sector=Technology",
      count: "2 of 4 stocks.",
      tickers: ["AAA", "CCC"],
      sectors: ["Technology"],
    });

    const before = win.history.length;
    sortBy("revenue");
    expect(view()).toMatchObject({
      address: "?sector=Technology&sort=-revenue",
      count: "2 of 4 stocks.",
      tickers: ["CCC", "AAA"],
      sorted: "descending revenue",
      sectors: ["Technology"],
    });
    expect(win.history.length).toBe(before + 1);

    // The screener remains mounted while a row's ticker page is inspected.
    const row = cells("CCC");
    expect(row).toMatchObject({
      Revenue: "$2,000M",
      "Revenue percentile": "90",
      "Net margin": "5.0%",
    });
    expect(await openTicker("CCC")).toEqual({
      title: "CCC · index",
      heading: "CCC",
      sector: "Technology",
      revenue: { universe: "90", latest: "$2,000M" },
      net_margin: { universe: "–", latest: "5.0%" },
    });
    expect(cells("CCC")).toEqual(row);

    type("max.net_margin", "7");
    expect(view()).toMatchObject({
      address: "?sector=Technology&max.net_margin=7&sort=-revenue",
      count: "1 of 4 stocks.",
      tickers: ["CCC"],
      sorted: "descending revenue",
      bounds: { "max.net_margin": "7" },
    });
    const narrow = win.location.search;

    toggle("col", "op_margin", false);
    expect(view()).toEqual({
      address:
        "?sector=Technology&max.net_margin=7&col=revenue&col=revenue.pct&col=net_margin&col=filing_age&sort=-revenue",
      count: "1 of 4 stocks.",
      tickers: ["CCC"],
      head: DEFAULT_HEAD.filter((name) => name !== "Operating margin"),
      sorted: "descending revenue",
      sectors: ["Technology"],
      bounds: { "max.net_margin": "7" },
      columns: ["revenue", "revenue.pct", "net_margin", "filing_age"],
    });

    const shown = view();
    const figures = cells("CCC");
    await load(win.location.search);
    expect(view()).toEqual(shown);
    expect(cells("CCC")).toEqual(figures);

    type("max.net_margin", "");
    expect(view()).toMatchObject({
      address:
        "?sector=Technology&col=revenue&col=revenue.pct&col=net_margin&col=filing_age&sort=-revenue",
      count: "2 of 4 stocks.",
      tickers: ["CCC", "AAA"],
      sorted: "descending revenue",
      bounds: {},
      columns: ["revenue", "revenue.pct", "net_margin", "filing_age"],
    });

    for (const ticker of ["CCC", "AAA"]) {
      const shownRow = cells(ticker);
      const page = await openTicker(ticker);
      expect(page.revenue.latest).toBe(shownRow.Revenue);
      expect(page.revenue.universe).toBe(shownRow["Revenue percentile"]);
      expect(page.net_margin.latest).toBe(shownRow["Net margin"]);
    }

    win.history.replaceState(null, "", narrow);
    win.dispatchEvent(new win.PopStateEvent("popstate"));
    expect(view()).toEqual({
      address: narrow,
      count: "1 of 4 stocks.",
      tickers: ["CCC"],
      head: DEFAULT_HEAD,
      sorted: "descending revenue",
      sectors: ["Technology"],
      bounds: { "max.net_margin": "7" },
      columns: [
        "revenue",
        "revenue.pct",
        "op_margin",
        "net_margin",
        "filing_age",
      ],
    });
  });
});
