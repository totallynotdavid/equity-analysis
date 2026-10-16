import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { renderTicker } from "../src/lib/render-ticker";
import { renderValuation } from "../src/lib/render-valuation";
import type { TickerReport, Valuation } from "../src/lib/tickers";

// Valuations that `index_core.valuation.valuate` worked out for a company with
// a price of $50, free cash flow of $400 and a beta of 1.2.
const fixtures = JSON.parse(
  readFileSync(new URL("./fixtures/valuation.json", import.meta.url), "utf-8"),
) as { ok: Valuation; negative: Valuation };
const apple = JSON.parse(
  readFileSync(new URL("./fixtures/AAPL.json", import.meta.url), "utf-8"),
) as TickerReport;

describe("renderValuation", () => {
  const html = renderValuation(fixtures.ok);

  it("says what growth the price implies, to a tenth of a percent", () => {
    // The fixture solves to 0.0534% a year.
    expect(html).toContain("<strong>0.1%</strong>");
    expect(html).toContain("$50.00 a share");
    expect(html).toContain("10 years");
    expect(html).toContain("2.5% for ever");
    expect(html).toContain("9.08%");
  });

  it("gives the share of the value that is terminal", () => {
    expect(html).toMatch(/The terminal value is \d+% of the enterprise value/);
  });

  it("states no fair value and says why", () => {
    expect(html).toContain("This is not a fair value");
    expect(html).not.toMatch(/fair value of|target price|undervalued/i);
  });

  it("prints a row for every discount rate and a column for every growth", () => {
    const model = fixtures.ok.model!;
    const rows = html.match(/<tr class="border-b last:border-0">/g) ?? [];
    const head = html.match(/<thead.*?<\/thead>/s)?.[0] ?? "";

    expect(rows).toHaveLength(model.rows.length);
    expect(head.match(/<th /g)).toHaveLength(model.growths.length + 2);
    expect(head).toContain("−5%");
    expect(head).toContain("25%");
  });

  it("marks the cells at or above the price", () => {
    const model = fixtures.ok.model!;
    const above = model.rows
      .flatMap((row) => row.values)
      .filter((value) => value >= model.price).length;

    expect(html.match(/data-above-price="true"/g) ?? []).toHaveLength(above);
    expect(above).toBeGreaterThan(0);
    expect(above).toBeLessThan(model.rows.length * model.growths.length);
  });

  it("lists the discount rates from low to high with a falling value", () => {
    const rates = [...html.matchAll(/scope="row">([\d.]+)%</g)].map((m) =>
      Number(m[1]),
    );

    expect(rates).toEqual([...rates].sort((a, b) => a - b));
    expect(rates).toHaveLength(fixtures.ok.model!.rows.length);
  });

  it("shows the inputs it assumed", () => {
    expect(html).toContain("Equity risk premium, assumed");
    expect(html).toContain("1.20, 1.13 after shrinking toward 1");
    expect(html).toContain("$5000");
  });

  it("says a negative cash flow is not meaningful and shows no grid", () => {
    const refused = renderValuation(fixtures.negative);

    expect(refused).toContain(
      "Not meaningful: free cash flow is not positive.",
    );
    expect(refused).not.toContain("valuation-grid");
  });

  it("shows nothing where the report has no valuation", () => {
    expect(renderValuation(null)).toBe("");
    expect(renderValuation(undefined)).toBe("");
  });

  it("escapes a reason it prints", () => {
    const hostile: Valuation = {
      status: "not_meaningful",
      reason: "<script>x</script>",
      model: null,
    };

    expect(renderValuation(hostile)).not.toContain("<script>");
  });
});

describe("renderTicker with a valuation", () => {
  it("puts the valuation after the statements", () => {
    const page = renderTicker({ ...apple, valuation: fixtures.ok });

    expect(page.indexOf('id="valuation"')).toBeGreaterThan(
      page.indexOf(">Ratios</h2>"),
    );
  });

  it("is unchanged for a report from before valuations", () => {
    expect(renderTicker(apple)).not.toContain('id="valuation"');
  });
});
