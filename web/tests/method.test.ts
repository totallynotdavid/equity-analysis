import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { escape } from "../src/lib/render";
import { LICENCES, renderMethod } from "../src/lib/render-method";
import type { TickerReport } from "../src/lib/tickers";
import { report } from "./support";

const AAPL = JSON.parse(
  readFileSync(new URL("./fixtures/AAPL.json", import.meta.url), "utf8"),
) as TickerReport;

function metricRows(html: string): string[] {
  return [...html.matchAll(/data-metric="([^"]+)"/g)].map((m) => m[1]);
}

describe("the method page", () => {
  const html = renderMethod([AAPL]);

  it("lists every metric the reports hold, once", () => {
    const metrics = [...new Set(AAPL.series.map((s) => s.metric))];

    expect(metrics.length).toBeGreaterThan(10);
    expect(metricRows(html).sort()).toEqual(metrics.sort());
  });

  it("gives each metric its group, name, unit and definition", () => {
    for (const series of AAPL.series) {
      const row = html.match(
        new RegExp(`data-metric="${series.metric}".*?</tr>`, "s"),
      )?.[0];
      expect(row, series.metric).toContain(series.group);
      expect(row, series.metric).toContain(
        series.label.replaceAll("&", "&amp;"),
      );
      expect(row, series.metric).toContain(
        `<td class="py-2 pr-4">${series.unit}</td>`,
      );
    }
  });

  it("states the formula of a derived metric and the tag of a reported one", () => {
    const derived = AAPL.series.find((s) => s.points[0]?.formula);
    const reported = AAPL.series.find(
      (s) => s.points[0] && s.points[0].formula === null,
    );
    if (derived === undefined || reported === undefined)
      throw new Error("the fixture needs both kinds");

    expect(html).toContain(derived.points[0].formula as string);
    expect(html).toContain(
      `<code>${reported.points[0].parts.find((p) => p.sign > 0)?.tag}</code>`,
    );
  });

  it("lists a metric another report holds", () => {
    const other = report({ ticker: "ZZZ", revenue: 1e9 });
    other.series[0].metric = "only_in_zzz";

    expect(metricRows(renderMethod([AAPL, other]))).toContain("only_in_zzz");
  });

  it("lists each data licence with a link and its terms", () => {
    for (const licence of LICENCES) {
      expect(html).toContain(`href="${licence.link}"`);
      expect(html).toContain(escape(licence.terms));
    }
    expect(html).toContain("CC BY-SA 4.0");
  });

  it("says so when no ticker report was published", () => {
    const empty = renderMethod([]);

    expect(empty).toContain("No ticker reports were published");
    expect(metricRows(empty)).toEqual([]);
    expect(empty).toContain('id="licences"');
  });
});
