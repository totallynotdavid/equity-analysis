import { describe, expect, it } from "vitest";
import type { Evidence } from "../src/lib/evidence";
import {
  applyScreen,
  buildScreener,
  formatCell,
  parseState,
  renderControls,
  renderResults,
  serializeState,
  type ScreenerData,
} from "../src/lib/screener";
import held from "./fixtures/evidence.json";
import development from "./fixtures/evidence-development.json";
import { PRICE_FIGURES, report, scores, verdictOf } from "./support";

const HELD = held as unknown as Evidence;
const DEVELOPMENT = development as unknown as Evidence;

const REPORTS = [
  report({
    ticker: "AAA",
    name: "Alpha Systems",
    sector: "Technology",
    revenue: 500e6,
    revenue_pct: 90,
    op_margin: 0.3,
    net_margin: 0.12,
    filed: "2026-09-01",
  }),
  report({
    ticker: "BBB",
    name: "Beta Bank",
    sector: "Financials",
    revenue: 100e6,
    revenue_pct: 40,
    op_margin: 0.1,
    net_margin: 0.08,
    filed: "2026-06-01",
  }),
  report({
    ticker: "CCC",
    name: "Gamma Care",
    sector: "Health Care",
    revenue: 2000e6,
    revenue_pct: 99,
    op_margin: null,
    net_margin: null,
  }),
  report({
    ticker: "DDD",
    name: "Delta Energy",
    sector: "Technology",
    revenue: 300e6,
    revenue_pct: 70,
    op_margin: 0.2,
    net_margin: 0.15,
  }),
  report({
    ticker: "EEE",
    name: "Epsilon Unscored",
    sector: null,
    revenue: 50e6,
    op_margin: 0.05,
  }),
];

const SCORES = scores("demo30", ["AAA", "BBB", "CCC", "DDD"]);
const SHOWN = verdictOf(HELD, "demo30", "pass");

function build(evidence: Evidence | null = SHOWN): ScreenerData {
  return buildScreener(SCORES, REPORTS, evidence);
}

function value(data: ScreenerData, ticker: string, key: string) {
  const row = data.rows.find((r) => r.ticker === ticker);
  return row?.values[data.columns.findIndex((c) => c.key === key)];
}

function order(data: ScreenerData, query: string): string[] {
  return applyScreen(data, parseState(query, data)).map((r) => r.ticker);
}

function tickersIn(html: string): string[] {
  return [...html.matchAll(/href="\/t\/([^"]+)"/g)].map((m) => m[1]);
}

/** The query that the header link of `key` carries. */
function headerQuery(html: string, key: string): string {
  const link = new RegExp(`data-sort="${key}" href="\\?([^"]*)"`).exec(html);
  if (link === null) throw new Error(`no header link for ${key}`);
  return link[1].replaceAll("&amp;", "&");
}

describe("the screener data", () => {
  it("has a row for every report, so every row has a page", () => {
    expect(build().rows.map((r) => r.ticker)).toEqual([
      "AAA",
      "BBB",
      "CCC",
      "DDD",
      "EEE",
    ]);
  });

  it("holds each figure in the unit it is shown in", () => {
    const data = build();

    expect(value(data, "AAA", "revenue")).toBe(500);
    expect(value(data, "AAA", "op_margin")).toBe(30);
    expect(value(data, "AAA", "revenue.pct")).toBe(90);
    expect(value(data, "AAA", "net_margin")).toBe(12);
  });

  it("shows nothing worked out from prices, whatever the reports carry", () => {
    const priced = REPORTS.map((r) => ({ ...r, ...PRICE_FIGURES }));

    const data = buildScreener(SCORES, priced, SHOWN);

    expect(data.columns.map((c) => c.key)).toEqual(
      build().columns.map((c) => c.key),
    );
    for (const key of ["beta", "volatility", "max_drawdown", "implied_growth"])
      expect(data.columns.map((c) => c.key)).not.toContain(key);
  });

  it("measures the age of the newest filing from the date of the scores", () => {
    const data = build();

    expect(value(data, "AAA", "filing_age")).toBe(29);
    expect(value(data, "BBB", "filing_age")).toBe(121);
  });

  it("leaves a figure a report lacks empty", () => {
    const data = build();

    expect(value(data, "CCC", "op_margin")).toBeNull();
    expect(value(data, "CCC", "net_margin")).toBeNull();
  });

  it("drops a figure the report stopped reporting long ago", () => {
    const stale = report({ ticker: "OLD", revenue: 1e9 });
    stale.series.push({
      ...stale.series[0],
      metric: "interest_expense",
      label: "Interest expense",
      points: [{ ...stale.series[0].points[0], end: "2023-09-30" }],
    });

    const data = buildScreener(null, [stale], null);

    expect(value(data, "OLD", "interest_expense")).toBeNull();
    expect(value(data, "OLD", "revenue")).toBe(1000);
  });

  it("refuses a unit it has no format for", () => {
    const odd = report({ ticker: "ODD" });
    odd.series[0].unit = "barrels";

    expect(() => buildScreener(null, [odd], null)).toThrow(/barrels/);
  });
});

describe("when the score is shown", () => {
  const reason = (evidence: Evidence | null) => build(evidence).score.reason;

  it("shows it only for a held-out run that passed", () => {
    const data = build(SHOWN);

    expect(data.score).toEqual({ shown: true, reason: null });
    expect(data.columns[0].key).toBe("score");
    expect(value(data, "AAA", "score")).toBe(10);
    expect(data.defaultSort).toBe("-score");
  });

  it("shows no score before any evaluation", () => {
    const data = build(null);

    expect(data.columns.map((c) => c.key)).not.toContain("score");
    expect(reason(null)).toMatch(/No held-out evaluation/);
    expect(data.defaultSort).toBe("ticker");
  });

  it("shows no score from the months that chose the model", () => {
    expect(reason(verdictOf(DEVELOPMENT, "demo30", "development"))).toMatch(
      /cannot test it/,
    );
  });

  it("names how many checks a failed run missed", () => {
    expect(reason(verdictOf(HELD, "demo30", "fail"))).toMatch(
      /failed 3 of 3 checks/,
    );
  });

  it("shows no score when only a baseline passed", () => {
    expect(reason(verdictOf(HELD, "demo30", "baseline"))).toMatch(
      /ridge baseline/,
    );
  });

  it("shows no score from the evaluation of another universe", () => {
    expect(reason(verdictOf(HELD, "sp500", "pass"))).toMatch(/sp500/);
  });
});

describe("filtering", () => {
  const data = build();

  it("keeps the rows between a minimum and a maximum", () => {
    expect(order(data, "min.revenue=250")).toEqual(["AAA", "CCC", "DDD"]);
    expect(order(data, "max.revenue=300")).toEqual(["BBB", "DDD", "EEE"]);
    expect(order(data, "min.revenue=100&max.revenue=500")).toEqual([
      "AAA",
      "BBB",
      "DDD",
    ]);
  });

  it("includes a bound that sits on a value", () => {
    expect(order(data, "min.revenue=500&max.revenue=500")).toEqual(["AAA"]);
  });

  it("drops a row that has no value to compare", () => {
    expect(order(data, "min.op_margin=0")).not.toContain("CCC");
    expect(order(data, "max.net_margin=100")).toEqual([
      "AAA",
      "BBB",
      "DDD",
      "EEE",
    ]);
  });

  it("filters by the percentile among the universe", () => {
    expect(order(data, "min.revenue.pct=80")).toEqual(["AAA", "CCC"]);
  });

  it("filters by the age of the newest filing", () => {
    expect(order(data, "max.filing_age=30")).toEqual(["AAA"]);
  });

  it("filters by sector, any of those chosen", () => {
    expect(order(data, "sector=Technology")).toEqual(["AAA", "DDD"]);
    expect(order(data, "sector=Technology&sector=Financials")).toEqual([
      "AAA",
      "BBB",
      "DDD",
    ]);
  });

  it("matches a ticker or a name, whatever the case", () => {
    expect(order(data, "q=gamma")).toEqual(["CCC"]);
    expect(order(data, "q=bb")).toEqual(["BBB"]);
  });

  it("combines every filter", () => {
    expect(order(data, "sector=Technology&min.revenue=400&q=sys")).toEqual([
      "AAA",
    ]);
  });

  it("can leave nothing, and says so", () => {
    const state = parseState("min.revenue=1e9", data);

    expect(applyScreen(data, state)).toEqual([]);
    expect(renderResults(data, state)).toContain("No stock passes");
    expect(renderResults(data, state)).toContain("0 of 5 stocks");
  });
});

describe("sorting", () => {
  const data = build();

  it("sorts by score, highest first, until told otherwise", () => {
    expect(order(data, "")).toEqual(["AAA", "BBB", "CCC", "DDD", "EEE"]);
  });

  it("sorts a figure either way", () => {
    expect(order(data, "sort=-revenue")).toEqual([
      "CCC",
      "AAA",
      "DDD",
      "BBB",
      "EEE",
    ]);
    expect(order(data, "sort=revenue")).toEqual([
      "EEE",
      "BBB",
      "DDD",
      "AAA",
      "CCC",
    ]);
  });

  it("puts a missing figure last in either direction", () => {
    expect(order(data, "sort=-op_margin").at(-1)).toBe("CCC");
    expect(order(data, "sort=op_margin").at(-1)).toBe("CCC");
    expect(order(data, "sort=-net_margin").at(-1)).toBe("CCC");
  });

  it("sorts text in alphabetical order", () => {
    expect(order(data, "sort=name")).toEqual([
      "AAA",
      "BBB",
      "DDD",
      "EEE",
      "CCC",
    ]);
    expect(order(data, "sort=-ticker")).toEqual([
      "EEE",
      "DDD",
      "CCC",
      "BBB",
      "AAA",
    ]);
  });

  it("breaks a tie by ticker", () => {
    const tied = buildScreener(
      null,
      ["ZZZ", "AAA", "MMM"].map((ticker) => report({ ticker, revenue: 1e9 })),
      null,
    );

    expect(order(tied, "sort=-revenue")).toEqual(["AAA", "MMM", "ZZZ"]);
  });

  it("sorts what the filters keep", () => {
    expect(order(data, "sector=Technology&sort=revenue")).toEqual([
      "DDD",
      "AAA",
    ]);
  });

  it("follows a header link to the sorted table", () => {
    const state = parseState("min.revenue=100", data);
    const first = renderResults(data, state);

    const byRevenue = parseState(headerQuery(first, "revenue"), data);

    expect(byRevenue.sort).toBe("-revenue");
    expect(byRevenue.min).toEqual({ revenue: 100 });
    expect(tickersIn(renderResults(data, byRevenue))).toEqual([
      "CCC",
      "AAA",
      "DDD",
      "BBB",
    ]);
  });

  it("reverses a sort when its header is followed again", () => {
    const down = parseState("sort=-revenue", data);

    const up = parseState(
      headerQuery(renderResults(data, down), "revenue"),
      data,
    );

    expect(up.sort).toBe("revenue");
    expect(tickersIn(renderResults(data, up))).toEqual([
      "EEE",
      "BBB",
      "DDD",
      "AAA",
      "CCC",
    ]);
  });

  it("starts a text column ascending and a figure descending", () => {
    const html = renderResults(data, parseState("", data));

    expect(headerQuery(html, "name")).toBe("sort=name");
    expect(headerQuery(html, "revenue")).toBe("sort=-revenue");
  });

  it("returns to the default sort by a header link without a sort", () => {
    const html = renderResults(data, parseState("sort=-revenue", data));

    expect(headerQuery(html, "score")).toBe("");
  });

  it("marks the sorted column", () => {
    const html = renderResults(data, parseState("sort=revenue", data));

    expect(html).toMatch(/aria-sort="ascending"><a[^>]*data-sort="revenue"/);
    expect(html).toContain("Revenue ▲");
  });
});

describe("the address", () => {
  const data = build();

  it("gives the same state back after a round trip", () => {
    const queries = [
      "",
      "sort=-revenue",
      "q=alpha&sector=Technology&sector=Financials",
      "min.revenue=100&max.revenue=900.5&min.net_margin=-0.5",
      "col=revenue&col=net_margin&sort=name",
      "min.revenue.pct=50&max.filing_age=90&sort=filing_age",
    ];

    for (const query of queries) {
      const state = parseState(query, data);

      expect(parseState(serializeState(state), data)).toEqual(state);
    }
  });

  it("writes one query for one state, whatever order it was read in", () => {
    const a = parseState(
      "max.net_margin=2&min.revenue=100&q=a&sort=name",
      data,
    );
    const b = parseState(
      "sort=name&q=a&min.revenue=100&max.net_margin=2",
      data,
    );

    expect(serializeState(a)).toBe(serializeState(b));
    expect(serializeState(a)).toBe(
      "q=a&min.revenue=100&max.net_margin=2&sort=name",
    );
  });

  it("drops what the data cannot show", () => {
    const state = parseState(
      "min.nonsense=1&max.revenue=abc&min.net_margin=&sort=nonsense&col=nonsense&sector=Mars",
      data,
    );

    expect(serializeState(state)).toBe("");
  });

  it("never lets a query key reach the prototype of a bound", () => {
    const state = parseState("min.__proto__=1&max.constructor=2", data);

    expect(Object.getPrototypeOf(state.min)).toBeNull();
    expect(Object.getPrototypeOf(state.max)).toBeNull();
    expect(({} as Record<string, unknown>).__proto__).toBe(Object.prototype);
    expect(serializeState(state)).toBe("");
  });

  it("drops a choice equal to the default", () => {
    expect(serializeState(parseState("sort=-score", data))).toBe("");
    expect(
      serializeState(
        parseState(data.defaults.map((c) => `col=${c}`).join("&"), data),
      ),
    ).toBe("");
  });

  it("holds the columns shown, and shows only those", () => {
    const state = parseState("col=revenue&col=net_margin", data);

    const html = renderResults(data, state);

    expect(html).toContain("Revenue");
    expect(html).toContain("Net margin");
    expect(html).not.toContain("Operating margin");
  });

  it("keeps the filters in every header link", () => {
    const html = renderResults(data, parseState("min.revenue=100&q=a", data));

    expect(headerQuery(html, "net_margin")).toBe(
      "q=a&min.revenue=100&sort=-net_margin",
    );
  });
});

describe("the controls", () => {
  const data = build();

  it("show the state in fields named like the address", () => {
    const state = parseState(
      "q=alpha&sector=Technology&min.revenue=250&max.net_margin=2&col=revenue",
      data,
    );

    const html = renderControls(data, state);

    expect(html).toMatch(/name="q" value="alpha"/);
    expect(html).toMatch(/name="sector" value="Technology" checked/);
    expect(html).not.toMatch(/name="sector" value="Financials" checked/);
    expect(html).toMatch(/name="min.revenue" placeholder="min" value="250"/);
    expect(html).toMatch(/name="max.net_margin" placeholder="max" value="2"/);
    expect(html).toMatch(/name="col" value="revenue" checked/);
    expect(html).not.toMatch(/name="col" value="net_margin" checked/);
  });

  it("open the filters when some are set", () => {
    expect(renderControls(data, parseState("min.revenue=250", data))).toContain(
      'id="filters" open',
    );
    expect(renderControls(data, parseState("", data))).not.toContain(
      'id="filters" open',
    );
  });

  it("check the default columns when none are chosen", () => {
    const html = renderControls(data, parseState("", data));

    for (const key of data.defaults)
      expect(html).toContain(`name="col" value="${key}" checked`);
  });

  it("escape what the reader typed", () => {
    const html = renderControls(data, parseState(`q="><script>`, data));

    expect(html).not.toContain("<script>");
    expect(html).toContain("&quot;&gt;&lt;script&gt;");
  });
});

describe("the table", () => {
  const data = build();

  it("links every ticker to its page and escapes the names", () => {
    const hostile = buildScreener(
      null,
      [report({ ticker: "BRK-B", name: "<b>Berkshire</b>" })],
      null,
    );

    const html = renderResults(hostile, parseState("", hostile));

    expect(html).toContain('<a class="underline" href="/t/BRK-B">BRK-B</a>');
    expect(html).toContain("&lt;b&gt;Berkshire&lt;/b&gt;");
    expect(html).not.toContain("<b>");
  });

  it("prints a figure as its column formats it", () => {
    const html = renderResults(data, parseState("", data));

    expect(html).toContain("$500M");
    expect(html).toContain("30.0%");
    expect(html).toContain("12.0%");
    expect(html).toContain(">–<");
  });

  it("counts the rows that pass out of all of them", () => {
    expect(
      renderResults(data, parseState("sector=Technology", data)),
    ).toContain("2 of 5 stocks");
  });

  it("formats by the digits and the sign of the unit", () => {
    const revenue = data.columns.find((c) => c.key === "revenue")!;
    const margin = data.columns.find((c) => c.key === "net_margin")!;

    expect(formatCell(1234567, revenue)).toBe("$1,234,567M");
    expect(formatCell(-12, revenue)).toBe("−$12M");
    expect(formatCell(12.34, margin)).toBe("12.3%");
    expect(formatCell(null, margin)).toBe("–");
  });
});
