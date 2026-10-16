import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { formatValue, renderTicker } from "../src/lib/render-ticker";
import type { TickerReport } from "../src/lib/tickers";

// A report of Apple built from its recorded companyfacts payload.
const apple = JSON.parse(
  readFileSync(new URL("./fixtures/AAPL.json", import.meta.url), "utf-8"),
) as TickerReport;

function series(report: TickerReport, metric: string) {
  const found = report.series.find((s) => s.metric === metric);
  if (found === undefined) throw new Error(`no ${metric}`);
  return found;
}

describe("formatValue", () => {
  it("writes whole millions of dollars as the statements do", () => {
    expect(formatValue(466_823_000_000, "USD")).toBe("$466,823M");
    expect(formatValue(-3_000_000, "USD")).toBe("−$3M");
  });

  it("writes whole thousands in thousands", () => {
    expect(formatValue(14_750_302_000, "shares")).toBe("14,750,302K");
  });

  it("writes any other amount in full so no digit is lost", () => {
    expect(formatValue(1_500_000, "USD")).toBe("$1,500K");
    expect(formatValue(1_234_567, "USD")).toBe("$1,234,567");
    expect(formatValue(1_500_000.5, "USD")).toBe("$1,500,000.50");
    expect(formatValue(12, "shares")).toBe("12");
  });

  it("writes a per-share figure with at least cents", () => {
    expect(formatValue(8.72, "USD/share")).toBe("$8.72");
    expect(formatValue(7.9, "USD/share")).toBe("$7.90");
    expect(formatValue(-0.123, "USD/share")).toBe("−$0.123");
  });

  it("writes a ratio as a percent", () => {
    expect(formatValue(0.27618604910212224, "ratio")).toBe("27.6%");
    expect(formatValue(-0.05, "ratio")).toBe("−5.0%");
  });
});

describe("renderTicker", () => {
  const html = renderTicker(apple);

  it("names the company and where it stands", () => {
    expect(html).toContain("<h1");
    expect(html).toContain("AAPL");
    expect(html).toContain("Apple Inc.");
    expect(html).toContain("Information Technology");
    expect(html).toContain("SIC 3571, Electronic Computers");
    expect(html).toContain("CIK=320193");
  });

  it("prints the latest revenue as the filings sum to", () => {
    const row =
      html.match(/<tr[^>]*data-metric="revenue".*?<\/tr>/s)?.[0] ?? "";

    expect(row).toContain("$466,823M");
  });

  it("links each figure to the filing that reported it", () => {
    const row =
      html.match(/<tr[^>]*data-metric="revenue".*?<\/tr>/s)?.[0] ?? "";
    const latest = series(apple, "revenue").points.at(-1)!;
    const main = latest.parts.find((part) => part.end === latest.end)!;

    expect(row).toContain(`href="${main.filing.url}"`);
    expect(main.filing.url).toContain(
      "/320193/000032019326000020/0000320193-26-000020-index.htm",
    );
  });

  it("lists every filed figure a value is made of, with its filing", () => {
    const detail =
      html.match(/<details[^>]*id="parts-revenue".*?<\/details>/s)?.[0] ?? "";
    const latest = series(apple, "revenue").points.at(-1)!;

    for (const part of latest.parts) {
      expect(detail).toContain(`href="${part.filing.url}"`);
    }
    expect(detail).toContain(
      "RevenueFromContractWithCustomerExcludingAssessedTax",
    );
    expect(detail).toContain("− $313,695M");
    expect(detail).toContain("$416,161M");
    expect(detail).toContain("$364,357M");
  });

  it("shows the formula of a ratio instead of linking it", () => {
    const row =
      html.match(/<tr[^>]*data-metric="net_margin".*?<\/tr>/s)?.[0] ?? "";

    expect(row).toContain("27.6%");
    expect(row).toContain('title="net income / revenue"');
    expect(row).not.toContain("<a ");
  });

  it("prints the per-share figure exactly", () => {
    const row =
      html.match(/<tr[^>]*data-metric="eps_diluted".*?<\/tr>/s)?.[0] ?? "";

    expect(row).toContain("$8.72");
    expect(row).not.toContain("8.7199");
  });

  it("gives the percentile in the universe and in the sector", () => {
    const row =
      html.match(/<tr[^>]*data-metric="revenue".*?<\/tr>/s)?.[0] ?? "";

    expect(row).toContain(">88<");
    expect(row).toContain(">64<");
  });

  it("marks a percentile that was not computed", () => {
    const row =
      html.match(/<tr[^>]*data-metric="net_income".*?<\/tr>/s)?.[0] ?? "";

    expect(row).toContain(">93<");
    expect(row).toContain(">–<");
  });

  it("orders the sections as the statements are", () => {
    const order = [
      "Income statement",
      "Cash flow",
      "Balance sheet",
      "Ratios",
    ].map((group) => html.indexOf(`>${group}</h2>`));

    expect(order.every((at) => at >= 0)).toBe(true);
    expect(order).toEqual([...order].sort((a, b) => a - b));
  });

  it("leaves a cell empty where a period has no figure", () => {
    // Interest expense has fewer periods than the rest of its section.
    expect(series(apple, "interest_expense").points.length).toBeLessThan(
      series(apple, "revenue").points.length,
    );
    const row =
      html.match(/<tr[^>]*data-metric="interest_expense".*?<\/tr>/s)?.[0] ?? "";

    expect(row).toContain('text-right">–</td>');
  });

  it("does not warn about figures from EDGAR", () => {
    expect(html).not.toContain('role="alert"');
  });

  it("warns that figures from another source mean nothing", () => {
    const warned = renderTicker({ ...apple, facts_source: "synthetic" });

    expect(warned).toContain('role="alert"');
    expect(warned).toContain("synthetic filings");
  });

  it("does not link a filing that is not on sec.gov", () => {
    const hostile: TickerReport = {
      ...apple,
      series: apple.series.map((s) => ({
        ...s,
        points: s.points.map((point) => ({
          ...point,
          parts: point.parts.map((part) => ({
            ...part,
            filing: { ...part.filing, url: "javascript:alert(1)" },
          })),
        })),
      })),
    };

    const page = renderTicker(hostile);

    expect(page).not.toContain("javascript:");
    expect(page).not.toContain("Archives/edgar");
    expect(page).toContain("10-Q for 2026 Q3, filed 2026-07-31");
  });

  it("escapes the values it prints", () => {
    const page = renderTicker({
      ...apple,
      name: "<script>x</script>",
      sector: '"><b>',
    });

    expect(page).not.toContain("<script>");
    expect(page).not.toContain("<b>");
    expect(page).toContain("&lt;script&gt;");
  });

  it("says when a company has no sector", () => {
    expect(renderTicker({ ...apple, sector: null })).toContain("No sector");
  });
});
