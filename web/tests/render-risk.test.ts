import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { renderRisk } from "../src/lib/render-risk";
import { renderTicker } from "../src/lib/render-ticker";
import type { Risk, TickerReport } from "../src/lib/tickers";

// Risks that `index_core.risk.assess` worked out for a stock built from the
// loadings 1.2, 0.5, -0.3, 0.4 and -0.2 and an alpha of 5% a year: one over a
// full window of 756 days, one with 100 days.
const fixtures = JSON.parse(
  readFileSync(new URL("./fixtures/risk.json", import.meta.url), "utf-8"),
) as { ok: Risk; short: Risk };
const apple = JSON.parse(
  readFileSync(new URL("./fixtures/AAPL.json", import.meta.url), "utf-8"),
) as TickerReport;

describe("renderRisk", () => {
  const html = renderRisk(fixtures.ok);

  it("states the window and the three summary figures", () => {
    expect(html).toContain("756 trading days from 2021-02-01 to 2023-12-25");
    expect(html).toContain("<strong>23.8%</strong> a year");
    expect(html).toContain("<strong>22.1%</strong> from a peak");
    expect(html).toContain("<strong>1.21</strong>");
    expect(html).toContain("1.15 to 1.27");
  });

  it("shows the R² of the factor regression", () => {
    expect(html).toContain("<strong data-r2>0.71</strong>");
  });

  it("prints a row with a 95% interval for alpha and each of the five factors", () => {
    const factors = [...html.matchAll(/data-factor="(\w+)"/g)].map((m) => m[1]);

    expect(factors).toEqual(["alpha", "mkt_rf", "smb", "hml", "rmw", "cma"]);
    expect(html).toContain("95% interval");
    expect(html).toContain("0.38 to 0.61");
    expect(html).toContain("−0.40 to −0.18");
  });

  it("writes a negative loading with a minus sign and no double sign", () => {
    expect(html).toContain(">−0.29<");
    expect(html).not.toContain("−-");
  });

  it("writes alpha as a percent a year with its interval", () => {
    expect(html).toContain(">5.6%<");
    expect(html).toContain("−9.2% to 20.3%");
  });

  it("marks only the estimates whose interval excludes zero", () => {
    const flags = Object.fromEntries(
      [...html.matchAll(/data-factor="(\w+)" data-excludes-zero="(\w+)"/g)].map(
        (m) => [m[1], m[2]],
      ),
    );

    expect(flags).toEqual({
      alpha: "false",
      mkt_rf: "true",
      smb: "true",
      hml: "true",
      rmw: "true",
      cma: "true",
    });
  });

  it("says the window ends with the factor file and what an interval is", () => {
    expect(html).toContain("Newey-West");
    expect(html).toContain("trails the report's date");
  });

  it("says insufficient history, with the days there are, below the window", () => {
    const short = renderRisk(fixtures.short);

    expect(short).toContain("Insufficient history: 100 days have both");
    expect(short).toContain("the window is 756");
    expect(short).toContain('data-status="insufficient_history"');
    expect(short).not.toContain("risk-loadings");
  });

  it("says what could not be estimated", () => {
    const refused: Risk = {
      status: "not_estimable",
      reason: "the stock's return does not vary",
      window: 756,
      fit: null,
    };

    expect(renderRisk(refused)).toContain(
      "Not estimable: the stock&#39;s return does not vary.",
    );
  });

  it("shows nothing where the report has no risk", () => {
    expect(renderRisk(null)).toBe("");
    expect(renderRisk(undefined)).toBe("");
  });

  it("escapes a reason it prints", () => {
    const hostile: Risk = {
      status: "insufficient_history",
      reason: "<script>x</script>",
      window: 756,
      fit: null,
    };

    expect(renderRisk(hostile)).not.toContain("<script>");
  });
});

describe("renderTicker with a risk", () => {
  it("puts the risk after the statements and the valuation", () => {
    const page = renderTicker({ ...apple, risk: fixtures.ok });

    expect(page.indexOf('id="risk"')).toBeGreaterThan(
      page.indexOf(">Ratios</h2>"),
    );
  });

  it("is unchanged for a report from before risks", () => {
    expect(renderTicker(apple)).not.toContain('id="risk"');
  });
});
