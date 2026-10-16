import type { Evidence } from "./evidence";
import { escape } from "./render";
import type { Scores } from "./scores";
import type { Series, TickerReport } from "./tickers";

/**
 * One column of the screener. Every value of a column is held in the unit it
 * is shown in, rounded to the digits shown, so what a filter compares is what
 * the table prints.
 */
export interface Column {
  key: string;
  label: string;
  group: string;
  unit: string;
  prefix: string;
  suffix: string;
  decimals: number;
}

export interface Row {
  ticker: string;
  name: string;
  sector: string | null;
  values: (number | null)[];
}

export interface ScoreStatus {
  shown: boolean;
  reason: string | null;
}

export interface ScreenerData {
  asOf: string | null;
  universe: string | null;
  columns: Column[];
  sectors: string[];
  defaults: string[];
  defaultSort: string;
  rows: Row[];
  score: ScoreStatus;
}

export interface State {
  sort: string | null;
  min: Record<string, number>;
  max: Record<string, number>;
  sectors: string[];
  cols: string[];
  q: string;
}

const TEXT_KEYS = ["ticker", "name", "sector"] as const;
const TEXT_LABELS: Record<(typeof TEXT_KEYS)[number], string> = {
  ticker: "Ticker",
  name: "Name",
  sector: "Sector",
};

const DEFAULT_COLUMNS = [
  "score",
  "revenue",
  "revenue.pct",
  "op_margin",
  "net_margin",
  "net_debt",
  "filing_age",
];

interface UnitSpec {
  unit: string;
  prefix: string;
  suffix: string;
  decimals: number;
  scale: number;
}

const UNITS: Record<string, UnitSpec> = {
  USD: { unit: "$M", prefix: "$", suffix: "M", decimals: 0, scale: 1e-6 },
  shares: {
    unit: "M shares",
    prefix: "",
    suffix: "M",
    decimals: 0,
    scale: 1e-6,
  },
  "USD/share": {
    unit: "$/share",
    prefix: "$",
    suffix: "",
    decimals: 2,
    scale: 1,
  },
  ratio: { unit: "%", prefix: "", suffix: "%", decimals: 1, scale: 100 },
};

const PERCENT: UnitSpec = UNITS.ratio;

function round(value: number, decimals: number): number {
  const factor = 10 ** decimals;
  return Math.round(value * factor) / factor;
}

interface Field {
  column: Column;
  scale: number;
  value: (report: TickerReport) => number | null;
}

function column(
  key: string,
  label: string,
  group: string,
  spec: UnitSpec,
): Column {
  return {
    key,
    label,
    group,
    unit: spec.unit,
    prefix: spec.prefix,
    suffix: spec.suffix,
    decimals: spec.decimals,
  };
}

const DAY = 86_400_000;

// A series whose newest period ends more than this many days before the
// report's newest period has no current value, so its cell is empty.
const STALE_DAYS = 400;

function newestEnd(report: TickerReport): string | null {
  const ends = report.series.flatMap((s) =>
    s.points.slice(-1).map((p) => p.end),
  );
  return ends.sort().at(-1) ?? null;
}

function latest(
  report: TickerReport,
  series: Series | undefined,
): number | null {
  const point = series?.points.at(-1);
  const newest = newestEnd(report);
  if (point === undefined || newest === null) return null;
  const behind = (Date.parse(newest) - Date.parse(point.end)) / DAY;
  return behind > STALE_DAYS ? null : point.value;
}

function seriesOf(report: TickerReport, metric: string): Series | undefined {
  return report.series.find((series) => series.metric === metric);
}

function metricColumns(reports: TickerReport[]): Field[] {
  const seen = new Map<string, Series>();
  for (const report of reports)
    for (const series of report.series)
      if (!seen.has(series.metric)) seen.set(series.metric, series);
  const fields: Field[] = [];
  for (const [metric, series] of seen) {
    const spec = UNITS[series.unit];
    if (spec === undefined)
      throw new Error(`The unit ${series.unit} of ${metric} has no format`);
    fields.push({
      column: column(metric, series.label, series.group, spec),
      scale: spec.scale,
      value: (report) => latest(report, seriesOf(report, metric)),
    });
    if (
      reports.some((r) => typeof seriesOf(r, metric)?.pct_universe === "number")
    )
      fields.push({
        column: column(
          `${metric}.pct`,
          `${series.label} percentile`,
          series.group,
          {
            unit: "percentile",
            prefix: "",
            suffix: "",
            decimals: 0,
            scale: 1,
          },
        ),
        scale: 1,
        value: (report) => seriesOf(report, metric)?.pct_universe ?? null,
      });
  }
  return fields;
}

function filingAge(report: TickerReport, asOf: string): number | null {
  let newest: string | null = null;
  for (const series of report.series)
    for (const point of series.points)
      for (const part of point.parts)
        if (newest === null || part.filing.filed > newest)
          newest = part.filing.filed;
  if (newest === null) return null;
  return Math.max(0, Math.round((Date.parse(asOf) - Date.parse(newest)) / DAY));
}

function withheld(
  scores: Scores | null,
  evidence: Evidence | null,
): string | null {
  if (scores === null) return "There are no scores yet.";
  if (evidence === null)
    return "No held-out evaluation has been run, so the score is not shown.";
  if (evidence.run.universe !== scores.universe)
    return `The evaluation is of the ${evidence.run.universe} universe, not ${scores.universe}, so the score is not shown.`;
  if (!evidence.verdict.decided)
    return "The stored evaluation covers only the months that chose the model, which cannot test it, so the score is not shown.";
  if (evidence.verdict.shown) return null;
  const failed = evidence.verdict.checks.filter((c) => !c.passed).length;
  if (failed === 0)
    return `The held-out checks pass for the ${evidence.verdict.candidate} baseline, not for the model that makes the score, so the score is not shown.`;
  return `The held-out evaluation failed ${failed} of ${evidence.verdict.checks.length} checks, so the score is not shown.`;
}

export function unpaged(
  scores: Scores | null,
  reports: readonly TickerReport[],
): string[] {
  const paged = new Set(reports.map((report) => report.ticker));
  return (scores?.rows ?? [])
    .map((row) => row.ticker)
    .filter((ticker) => !paged.has(ticker));
}

/**
 * The screener's rows and columns, made from the ticker reports and the scores
 * the build reads. A row is a report, so every row has a page. The score
 * column exists only when the evidence allows the score to be shown.
 */
export function buildScreener(
  scores: Scores | null,
  reports: TickerReport[],
  evidence: Evidence | null,
): ScreenerData {
  const reason = withheld(scores, evidence);
  const asOf =
    scores?.as_of ??
    reports
      .map((report) => report.as_of)
      .sort()
      .at(-1) ??
    null;
  const byTicker = new Map(scores?.rows.map((row) => [row.ticker, row.score]));
  const fields: Field[] = [
    ...(reason === null
      ? [
          {
            column: column("score", "Score", "Score", {
              unit: "1 to 10",
              prefix: "",
              suffix: "",
              decimals: 0,
              scale: 1,
            }),
            scale: 1,
            value: (r: TickerReport) => byTicker.get(r.ticker) ?? null,
          },
        ]
      : []),
    ...metricColumns(reports),
    ...(asOf === null
      ? []
      : [
          {
            column: column(
              "filing_age",
              "Days since newest filing",
              "Filings",
              {
                unit: "days",
                prefix: "",
                suffix: "",
                decimals: 0,
                scale: 1,
              },
            ),
            scale: 1,
            value: (r: TickerReport) => filingAge(r, asOf),
          },
        ]),
  ];
  const keys = new Set(fields.map(({ column: c }) => c.key));
  const defaults = DEFAULT_COLUMNS.filter((key) => keys.has(key));
  return {
    asOf,
    universe: scores?.universe ?? reports[0]?.universe ?? null,
    columns: fields.map((e) => e.column),
    sectors: [
      ...new Set(reports.flatMap((r) => (r.sector === null ? [] : [r.sector]))),
    ].sort(),
    defaults,
    defaultSort: reason === null ? "-score" : "ticker",
    rows: reports.map((report) => ({
      ticker: report.ticker,
      name: report.name,
      sector: report.sector,
      values: fields.map(({ column: c, scale, value }) => {
        const raw = value(report);
        return raw === null ? null : round(raw * scale, c.decimals);
      }),
    })),
    score: { shown: reason === null, reason },
  };
}

function columnKeys(data: ScreenerData): Set<string> {
  return new Set(data.columns.map((c) => c.key));
}

function sortable(data: ScreenerData): Set<string> {
  return new Set([...TEXT_KEYS, ...data.columns.map((c) => c.key)]);
}

function finite(text: string): number | null {
  if (text.trim() === "") return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

/**
 * The state a query string holds. What the data cannot show is dropped: an
 * unknown column, a bound that is not a number. A choice equal to the default
 * is dropped too, so one state has one query string.
 */
export function parseState(
  query: string | URLSearchParams,
  data: ScreenerData,
): State {
  const params = new URLSearchParams(query);
  const known = columnKeys(data);
  const state: State = {
    sort: null,
    min: {},
    max: {},
    sectors: [],
    cols: [],
    q: (params.get("q") ?? "").trim(),
  };
  for (const [name, text] of params) {
    const bound = /^(min|max)\.(.+)$/.exec(name);
    if (bound === null || !known.has(bound[2])) continue;
    const value = finite(text);
    if (value !== null) state[bound[1] as "min" | "max"][bound[2]] = value;
  }
  const sort = params.get("sort");
  if (
    sort !== null &&
    sort !== data.defaultSort &&
    sortable(data).has(sort.replace(/^-/, ""))
  )
    state.sort = sort;
  state.sectors = [
    ...new Set(params.getAll("sector").filter((s) => data.sectors.includes(s))),
  ];
  const chosen = new Set(params.getAll("col"));
  const cols = data.columns.map((c) => c.key).filter((key) => chosen.has(key));
  const isDefault =
    cols.length === data.defaults.length &&
    data.defaults.every((key) => cols.includes(key));
  state.cols = isDefault ? [] : cols;
  return state;
}

/** The query string of a state, with the keys in a fixed order. */
export function serializeState(state: State): string {
  const params = new URLSearchParams();
  if (state.q !== "") params.set("q", state.q);
  for (const sector of state.sectors) params.append("sector", sector);
  for (const kind of ["min", "max"] as const)
    for (const key of Object.keys(state[kind]).sort())
      params.set(`${kind}.${key}`, String(state[kind][key]));
  for (const key of state.cols) params.append("col", key);
  if (state.sort !== null) params.set("sort", state.sort);
  return params.toString();
}

/** The rows that pass the state's filters, in the state's order. */
export function applyScreen(data: ScreenerData, state: State): Row[] {
  const index = new Map(data.columns.map((c, i) => [c.key, i]));
  const needle = state.q.toLowerCase();
  const rows = data.rows.filter((row) => {
    if (
      needle !== "" &&
      !row.ticker.toLowerCase().includes(needle) &&
      !row.name.toLowerCase().includes(needle)
    )
      return false;
    if (
      state.sectors.length > 0 &&
      (row.sector === null || !state.sectors.includes(row.sector))
    )
      return false;
    for (const kind of ["min", "max"] as const)
      for (const [key, bound] of Object.entries(state[kind])) {
        const value = row.values[index.get(key) as number];
        if (value === null) return false;
        if (kind === "min" ? value < bound : value > bound) return false;
      }
    return true;
  });
  const sort = state.sort ?? data.defaultSort;
  const descending = sort.startsWith("-");
  const key = descending ? sort.slice(1) : sort;
  const direction = descending ? -1 : 1;
  const textKey = (TEXT_KEYS as readonly string[]).includes(key);
  const at = index.get(key);
  const of = (row: Row): number | string | null =>
    textKey
      ? (row[key as (typeof TEXT_KEYS)[number]] ?? null)
      : row.values[at as number];
  return rows.sort((a, b) => {
    const x = of(a);
    const y = of(b);
    if (x === null || y === null) {
      if (x === y) return a.ticker.localeCompare(b.ticker);
      return x === null ? 1 : -1;
    }
    const order =
      typeof x === "string" ? x.localeCompare(y as string) : x - (y as number);
    return order === 0 ? a.ticker.localeCompare(b.ticker) : direction * order;
  });
}

/** The columns the state shows, in catalogue order. */
export function visibleColumns(data: ScreenerData, state: State): Column[] {
  const shown = new Set(state.cols.length > 0 ? state.cols : data.defaults);
  return data.columns.filter((c) => shown.has(c.key));
}

export function formatCell(value: number | null, c: Column): string {
  if (value === null) return "–";
  const text = Math.abs(value).toLocaleString("en-US", {
    minimumFractionDigits: c.decimals,
    maximumFractionDigits: c.decimals,
  });
  return `${value < 0 ? "−" : ""}${c.prefix}${text}${c.suffix}`;
}

function nextSort(current: string, key: string): string {
  const text = (TEXT_KEYS as readonly string[]).includes(key);
  if (current === key) return `-${key}`;
  if (current === `-${key}`) return key;
  return text ? key : `-${key}`;
}

/** The query that sorts by `key`, or reverses the sort if it already does. */
export function sortQuery(
  data: ScreenerData,
  state: State,
  key: string,
): string {
  const sort = nextSort(state.sort ?? data.defaultSort, key);
  return serializeState({
    ...state,
    sort: sort === data.defaultSort ? null : sort,
  });
}

function header(
  data: ScreenerData,
  state: State,
  key: string,
  label: string,
  right: boolean,
): string {
  const current = state.sort ?? data.defaultSort;
  const active = current === key || current === `-${key}`;
  const arrow = active ? (current.startsWith("-") ? " ▼" : " ▲") : "";
  const order = !active
    ? "none"
    : current.startsWith("-")
      ? "descending"
      : "ascending";
  return `<th class="py-2 ${right ? "pl-4 text-right" : "pr-4"} font-medium whitespace-nowrap" aria-sort="${order}"><a class="hover:underline" data-sort="${escape(key)}" href="?${escape(sortQuery(data, state, key))}">${escape(label)}${arrow}</a></th>`;
}

/**
 * The count and the table of the stocks that pass the state's filters. Each
 * header links to the query that sorts by it.
 */
export function renderResults(data: ScreenerData, state: State): string {
  const rows = applyScreen(data, state);
  const columns = visibleColumns(data, state);
  const head = [
    ...TEXT_KEYS.map((key) =>
      header(data, state, key, TEXT_LABELS[key], false),
    ),
    ...columns.map((c) => header(data, state, c.key, c.label, true)),
  ].join("");
  const index = new Map(data.columns.map((c, i) => [c.key, i]));
  const body =
    rows.length === 0
      ? `<tr><td class="text-muted-foreground py-3" colspan="${3 + columns.length}">No stock passes these filters.</td></tr>`
      : rows
          .map((row) => {
            const cells = columns
              .map(
                (c) =>
                  `<td class="py-2 pl-4 text-right tabular-nums">${escape(formatCell(row.values[index.get(c.key) as number], c))}</td>`,
              )
              .join("");
            return `<tr class="border-b last:border-0"><td class="py-2 pr-4 font-medium"><a class="underline" href="/t/${encodeURIComponent(row.ticker)}">${escape(row.ticker)}</a></td><td class="py-2 pr-4">${escape(row.name)}</td><td class="text-muted-foreground py-2 pr-4">${escape(row.sector ?? "–")}</td>${cells}</tr>`;
          })
          .join("");
  return `<p class="text-muted-foreground mt-6 text-sm" id="screener-count">${rows.length} of ${data.rows.length} stocks.</p><div class="overflow-x-auto"><table class="mt-3 w-full text-left text-sm" id="screener-table"><thead class="text-muted-foreground border-b"><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function bounds(data: ScreenerData, state: State): string {
  const active = Object.keys(state.min).length + Object.keys(state.max).length;
  const groups = new Map<string, Column[]>();
  for (const c of data.columns)
    groups.set(c.group, [...(groups.get(c.group) ?? []), c]);
  const body = [...groups]
    .map(([group, members]) => {
      const rows = members
        .map(
          (c) =>
            `<label class="flex items-center gap-2 py-1"><span class="w-56">${escape(c.label)} <span class="text-muted-foreground">${escape(c.unit)}</span></span><input class="w-24 rounded-md border px-2 py-1" type="number" step="any" name="min.${escape(c.key)}" placeholder="min" value="${escape(state.min[c.key] ?? "")}" /><input class="w-24 rounded-md border px-2 py-1" type="number" step="any" name="max.${escape(c.key)}" placeholder="max" value="${escape(state.max[c.key] ?? "")}" /></label>`,
        )
        .join("");
      return `<div class="mt-3"><p class="font-medium">${escape(group)}</p>${rows}</div>`;
    })
    .join("");
  return `<details class="mt-3 text-sm" id="filters"${active > 0 ? " open" : ""}><summary class="cursor-pointer">Filters${active > 0 ? ` (${active} set)` : ""}</summary>${body}</details>`;
}

function columnPicker(data: ScreenerData, state: State): string {
  const shown = new Set(visibleColumns(data, state).map((c) => c.key));
  const boxes = data.columns
    .map(
      (c) =>
        `<label class="flex items-center gap-2"><input type="checkbox" name="col" value="${escape(c.key)}"${shown.has(c.key) ? " checked" : ""} />${escape(c.label)}</label>`,
    )
    .join("");
  return `<details class="mt-3 text-sm" id="columns"><summary class="cursor-pointer">Columns</summary><div class="mt-2 grid gap-1 sm:grid-cols-2">${boxes}</div></details>`;
}

/**
 * The form. Its fields are named like the query string, so submitting it
 * without a script gives the URL that holds the same state.
 */
export function renderControls(data: ScreenerData, state: State): string {
  const sectors = data.sectors
    .map(
      (sector) =>
        `<label class="flex items-center gap-2"><input type="checkbox" name="sector" value="${escape(sector)}"${state.sectors.includes(sector) ? " checked" : ""} />${escape(sector)}</label>`,
    )
    .join("");
  const sort = `<input type="hidden" name="sort" value="${escape(state.sort ?? "")}" />`;
  return `<form class="mt-6" id="screener-form" method="get" action=""><label class="block text-sm font-medium">Ticker or name<input class="mt-1 block w-full max-w-sm rounded-md border px-2 py-1" type="search" name="q" value="${escape(state.q)}" /></label><fieldset class="mt-3 text-sm"><legend class="font-medium">Sector</legend><div class="mt-1 grid gap-1 sm:grid-cols-2">${sectors}</div></fieldset>${bounds(data, state)}${columnPicker(data, state)}${sort}<button class="mt-4 rounded-md border px-3 py-1 text-sm" type="submit">Apply</button></form>`;
}

export function renderScoreNote(data: ScreenerData): string {
  if (data.score.reason === null) return "";
  return `<p class="border-warning bg-warning/10 text-foreground mt-4 rounded-md border px-4 py-3 text-sm" id="score-note">${escape(data.score.reason)} <a class="underline" href="/evidence">See the evidence</a>.</p>`;
}
