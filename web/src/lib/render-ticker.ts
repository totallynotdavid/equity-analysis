import { renderValuation } from "./render-valuation";
import type { Part, Point, Series, TickerReport } from "./tickers";

const ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

function escape(value: string | number): string {
  return String(value).replace(/[&<>"']/g, (char) => ESCAPES[char]);
}

// A file that names another site is not linked.
const FILING_PREFIX = "https://www.sec.gov/Archives/edgar/data/";

// Each group shows this many period columns.
const PERIODS_SHOWN = 8;

const GROUPED = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

function grouped(value: number): string {
  return GROUPED.format(value);
}

/**
 * A figure is formatted as exactly as a filing prints it. Dollars and shares that are whole
 * millions or thousands are written in them, as the statements are; the rest
 * are written in full, so the digits shown are the digits filed.
 */
export function formatValue(value: number, unit: string): string {
  const sign = value < 0 ? "−" : "";
  const size = Math.abs(value);
  if (unit === "ratio") return `${sign}${(size * 100).toFixed(1)}%`;
  if (unit === "USD/share") {
    const text = String(size);
    const decimals = text.includes(".") ? text.split(".")[1].length : 0;
    return `${sign}$${size.toFixed(Math.max(2, decimals))}`;
  }
  const prefix = unit === "USD" ? "$" : "";
  if (Number.isInteger(size) && size >= 1e6 && size % 1e6 === 0)
    return `${sign}${prefix}${grouped(size / 1e6)}M`;
  if (Number.isInteger(size) && size >= 1e3 && size % 1e3 === 0)
    return `${sign}${prefix}${grouped(size / 1e3)}K`;
  const digits = String(size).includes(".") ? 2 : 0;
  return `${sign}${prefix}${size.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: 20,
  })}`;
}

function filingLink(part: Part, text: string): string {
  const { filing } = part;
  if (!filing.url.startsWith(FILING_PREFIX)) return escape(text);
  return `<a class="underline" href="${escape(filing.url)}" rel="noreferrer">${escape(text)}</a>`;
}

function partUnit(series: Series): string {
  return series.unit === "ratio" ? "USD" : series.unit;
}

function period(part: Part): string {
  return part.start === null
    ? `at ${part.end}`
    : `${part.start} to ${part.end}`;
}

/** A single-filing point links to the part that ends the period. */
function mainPart(point: Point): Part {
  const added = point.parts.filter((part) => part.sign > 0);
  return added.reduce((a, b) => (b.end > a.end ? b : a), added[0]);
}

function cell(series: Series, point: Point | undefined): string {
  if (point === undefined)
    return `<td class="text-muted-foreground py-2 pl-4 text-right">–</td>`;
  const value = formatValue(point.value, series.unit);
  if (point.formula !== null)
    return `<td class="py-2 pl-4 text-right tabular-nums" title="${escape(point.formula)}">${escape(value)}</td>`;
  const part = mainPart(point);
  const title = `${part.tag}, ${part.filing.form} filed ${part.filing.filed}`;
  return `<td class="py-2 pl-4 text-right tabular-nums" title="${escape(title)}">${filingLink(part, value)}</td>`;
}

function percentile(value: number | null): string {
  return value === null
    ? `<td class="text-muted-foreground py-2 pl-4 text-right">–</td>`
    : `<td class="py-2 pl-4 text-right tabular-nums">${escape(Math.round(value))}</td>`;
}

function partLine(series: Series, part: Part, first: boolean): string {
  const sign = part.sign > 0 ? (first ? "" : "+ ") : "− ";
  const what = isDerived(series)
    ? `${escape(part.metric.replaceAll("_", " "))} `
    : "";
  const filing = `${part.filing.form} for ${part.filing.fy} ${part.filing.fp}, filed ${part.filing.filed}`;
  return `<li>${sign}${what}${escape(formatValue(part.value, partUnit(series)))} <span class="text-muted-foreground">${escape(part.tag)}, ${escape(period(part))},</span> ${filingLink(part, filing)}</li>`;
}

function isDerived(series: Series): boolean {
  return series.points.some((point) => point.formula !== null);
}

const DAY = 86_400_000;

function pointLabel(point: Point): string {
  const first = point.parts[0];
  if (first.start === null) return `At ${point.end}`;
  const days = (Date.parse(first.end) - Date.parse(first.start)) / DAY;
  if (point.parts.length > 1 || days > 350)
    return `Twelve months to ${point.end}`;
  return `${first.start} to ${point.end}`;
}

function pointDetail(series: Series, point: Point): string {
  const label = pointLabel(point);
  const formula =
    point.formula === null
      ? ""
      : ` <span class="text-muted-foreground">${escape(point.formula)}</span>`;
  const parts = point.parts
    .map((part, i) => partLine(series, part, i === 0))
    .join("");
  return `<li class="mt-2"><span class="font-medium">${escape(label)}: ${escape(formatValue(point.value, series.unit))}</span>${formula}<ul class="ml-4 list-none">${parts}</ul></li>`;
}

function details(series: Series): string {
  const points = [...series.points]
    .reverse()
    .map((point) => pointDetail(series, point))
    .join("");
  return `<details class="mt-1 text-sm" id="parts-${escape(series.metric)}"><summary class="text-muted-foreground cursor-pointer">${escape(series.label)}: the filed figures</summary><ul class="list-none pb-2">${points}</ul></details>`;
}

function section(
  group: string,
  members: Series[],
  report: TickerReport,
): string {
  const ends = [
    ...new Set(members.flatMap((s) => s.points.map((point) => point.end))),
  ]
    .sort()
    .slice(-PERIODS_SHOWN);
  const head = ends
    .map(
      (end) =>
        `<th class="py-2 pl-4 text-right font-medium whitespace-nowrap">${escape(end)}</th>`,
    )
    .join("");
  const rows = members
    .map((series) => {
      const byEnd = new Map(series.points.map((point) => [point.end, point]));
      const cells = ends.map((end) => cell(series, byEnd.get(end))).join("");
      return `<tr class="border-b last:border-0" data-metric="${escape(series.metric)}"><th class="py-2 pr-4 text-left font-medium" scope="row">${escape(series.label)}</th>${percentile(series.pct_universe)}${percentile(series.pct_sector)}${cells}</tr>`;
    })
    .join("");
  const notes = members.map(details).join("");
  const id = group.toLowerCase().replaceAll(/[^a-z]+/g, "-");
  return `<section class="mt-10" id="${escape(id)}"><h2 class="text-xl font-semibold">${escape(group)}</h2><div class="overflow-x-auto"><table class="mt-3 w-full text-sm"><thead class="text-muted-foreground border-b"><tr><th class="py-2 pr-4 text-left font-medium">Metric</th><th class="py-2 pl-4 text-right font-medium" title="Percent of the ${escape(report.universe)} universe at or below the latest value">Universe</th><th class="py-2 pl-4 text-right font-medium" title="Percent of the sector at or below the latest value">Sector</th>${head}</tr></thead><tbody>${rows}</tbody></table></div>${notes}</section>`;
}

/**
 * The page of one ticker: its fundamentals, each figure linked to the filing
 * it was reported in. The build renders it from the report file.
 */
export function renderTicker(report: TickerReport): string {
  const warning =
    report.facts_source === "edgar"
      ? ""
      : `<p class="border-error bg-error/10 text-foreground mt-4 rounded-md border px-4 py-3 text-sm font-medium" role="alert">These figures are from ${escape(report.facts_source)} filings, not real ones. They mean nothing.</p>`;
  const groups = new Map<string, Series[]>();
  for (const series of report.series)
    groups.set(series.group, [...(groups.get(series.group) ?? []), series]);
  const sections = [...groups]
    .map(([group, members]) => section(group, members, report))
    .join("");
  const sector = report.sector === null ? "No sector" : report.sector;
  const company = `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${report.cik}`;
  return `<h1 class="text-primary text-4xl font-bold tracking-tight">${escape(report.ticker)}</h1><p class="mt-1 text-lg">${escape(report.name)}</p>${warning}<p class="text-muted-foreground mt-3 text-sm" id="identity">${escape(sector)}. SIC ${escape(report.sic)}, ${escape(report.sic_description)}. <a class="underline" href="${escape(company)}" rel="noreferrer">All filings on EDGAR</a>.</p><p class="text-muted-foreground mt-2 text-sm">As of ${escape(report.as_of)}, from the filings made before it. Income statement and cash flow figures are for the twelve months to the date, balance sheet figures are at the date, and diluted shares are the weighted average of the longest period reported to it. A figure links to the filing that first reported it. Universe and sector give the percent of the ${escape(report.universe)} universe, and of its stocks in the same sector today, at or below the latest value.</p>${sections}${renderValuation(report.valuation)}`;
}
