import type {
  Evidence,
  FactorAlpha,
  Interval,
  LedgerStatus,
  ModelEvidence,
  Portfolio,
} from "./evidence";
import { escape } from "./render";

// Every number on the page is printed from the evidence file. This module
// formats numbers and chooses words. It computes nothing from them.

const NAMES: Record<string, string> = {
  lightgbm: "LightGBM",
  ridge: "ridge",
  composite: "composite",
};

const FACTORS: Record<string, string> = {
  mkt_rf: "market",
  smb: "size",
  hml: "value",
  rmw: "profitability",
  cma: "investment",
};

const MUTED = "text-muted-foreground";

function name(model: string): string {
  return NAMES[model] ?? model;
}

function fixed(value: number, digits: number, plus = false): string {
  const text = Math.abs(value).toFixed(digits);
  if (value < 0 && Number(text) !== 0) return `−${text}`;
  return plus ? `+${text}` : text;
}

function percent(value: number, digits = 1, plus = false): string {
  return `${fixed(value * 100, digits, plus)}%`;
}

type Format = (value: number) => string;

function cell(value: number | null, format: Format): string {
  return value === null ? "–" : format(value);
}

function interval(i: Interval, format: Format): string {
  if (i.value === null) return "–";
  if (i.lo === null || i.hi === null) return format(i.value);
  return `${format(i.value)} <span class="${MUTED}">(${format(i.lo)} to ${format(i.hi)})</span>`;
}

function table(
  id: string,
  head: string[],
  rows: string[][],
  caption?: string,
): string {
  const header = head
    .map(
      (text, i) =>
        `<th class="py-2 ${i === 0 ? "pr-4 text-left" : "pl-4 text-right"} font-medium whitespace-nowrap" scope="col">${text}</th>`,
    )
    .join("");
  const body = rows
    .map(
      (row) =>
        `<tr class="border-b last:border-0">${row
          .map((text, i) =>
            i === 0
              ? `<th class="py-2 pr-4 text-left font-medium" scope="row">${text}</th>`
              : `<td class="py-2 pl-4 text-right tabular-nums">${text}</td>`,
          )
          .join("")}</tr>`,
    )
    .join("");
  const title =
    caption === undefined
      ? ""
      : `<caption class="${MUTED} pb-2 text-left text-sm">${caption}</caption>`;
  return `<div class="overflow-x-auto"><table class="mt-3 w-full text-sm" id="${id}">${title}<thead class="${MUTED} border-b"><tr>${header}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function section(id: string, heading: string, body: string): string {
  return `<section class="mt-12" id="${id}"><h2 class="text-xl font-semibold">${heading}</h2>${body}</section>`;
}

function prose(text: string): string {
  return `<p class="${MUTED} mt-2 text-sm">${text}</p>`;
}

function ledgerStatus(status: LedgerStatus): string {
  const values: [string, string][] = [
    [
      "Forecasts committed",
      status.forecasts_committed === 0
        ? "no forecasts committed"
        : String(status.forecasts_committed),
    ],
    ["Chain verified up to", status.chain_verified_up_to ?? "none"],
    ["Earliest stamp", status.earliest_stamp ?? "none"],
    ["Live record", status.live_record],
  ];
  return `<dl class="mt-6 grid gap-x-8 border-y py-3 text-sm sm:grid-cols-4" id="ledger-status">${values
    .map(
      ([label, value]) =>
        `<div><dt class="${MUTED}">${label}</dt><dd class="font-medium tabular-nums">${escape(value)}</dd></div>`,
    )
    .join("")}</dl>`;
}

function evidenceHeading(status: LedgerStatus | null): string {
  return `<h1 class="text-primary text-4xl font-bold tracking-tight">Evidence</h1>${status === null ? "" : ledgerStatus(status)}`;
}

function period(evidence: Evidence): string {
  return evidence.run.period === "holdout"
    ? "months held back from every choice"
    : "development months";
}

function run(evidence: Evidence): string {
  const { run } = evidence;
  const facts: [string, string][] = [
    ["Universe", escape(run.universe)],
    ["Months", escape(period(evidence))],
    ["From", escape(run.first_date)],
    ["To", escape(run.last_date)],
    ["Snapshot dates", escape(run.dates)],
    ["Calendar months", escape(run.months)],
    ["Stock-dates", escape(run.rows)],
    ["Independent windows, about", escape(run.independent_windows)],
    ["Return horizon, trading days", escape(run.horizon_days)],
    ["Share of stocks beating SPY", percent(run.base_rate)],
    ["One-way trading cost, basis points", escape(run.cost_bps)],
    ["Names in the universe", escape(run.members)],
    ["Names without prices", escape(run.unpriced)],
    ["Prices", escape(run.price_source)],
    ["Filings", escape(run.facts_source ?? "none")],
    ["Factors", escape(run.factors_source ?? "none")],
  ];
  const list = facts
    .map(
      ([label, value]) =>
        `<div class="border-b py-1"><dt class="${MUTED}">${label}</dt><dd class="font-medium tabular-nums">${value}</dd></div>`,
    )
    .join("");
  return `<dl class="mt-3 grid gap-x-8 text-sm sm:grid-cols-2" id="run">${list}</dl>${prose("Snapshots a few days apart share most of their outcome, so the count of independent windows, not of rows, says how much evidence there is.")}`;
}

function verdict(evidence: Evidence): string {
  const { verdict } = evidence;
  const stays = verdict.lightgbm_stays
    ? "LightGBM beat both baselines on rank IC and net spread in the development months."
    : "LightGBM did not beat both baselines on rank IC and net spread in the development months, so a baseline is the candidate.";
  const candidate = `<p class="mt-3 text-sm">Candidate: <strong>${escape(name(verdict.candidate))}</strong>. ${stays}</p>`;
  const checks = table(
    "checks",
    ["Check", "Result", "Outcome"],
    verdict.checks.map((check) => [
      escape(check.name),
      escape(check.value),
      check.passed ? "pass" : "fail",
    ]),
  );
  if (!verdict.decided)
    return `<p class="border-warning bg-warning/10 mt-3 rounded-md border px-4 py-3 text-sm" id="verdict" role="note">No verdict. These are the development months, which chose the candidate, so they cannot test it. Run the held-out months once to decide.</p>${candidate}${checks}`;
  const text = verdict.shown
    ? "The held-out months pass every check, so the score is shown."
    : "The score is not shown.";
  return `<p class="mt-3 text-base font-medium" id="verdict">${text}</p>${candidate}${checks}${prose("The candidate is chosen on the development months and tested once on the months held back. The score is shown only if all checks pass for LightGBM.")}`;
}

function models(evidence: Evidence): string {
  const rows = evidence.models.map((m) => [
    escape(name(m.model)),
    interval(m.mean_ic, (v) => fixed(v, 3, true)),
    cell(m.ic_t_stat, (v) => fixed(v, 2)),
    interval(m.mean_auc, (v) => fixed(v, 3)),
    interval(m.top_hit_rate, (v) => percent(v)),
    interval(m.spread_excess, (v) => percent(v, 1, true)),
  ]);
  return (
    table(
      "models",
      [
        "Model",
        "Mean rank IC",
        "t-statistic",
        "AUC",
        "Top-decile hit rate",
        "Top score minus bottom score",
      ],
      rows,
    ) +
    prose(
      "Rank IC is the correlation, date by date, between the model's ranking and the later return above SPY. The t-statistic is Newey-West. Intervals resample whole months. The last column is the mean excess return over the label horizon.",
    )
  );
}

function leg(p: Portfolio): string[] {
  return [
    interval(p.gross, (v) => percent(v, 1, true)),
    interval(p.net, (v) => percent(v, 1, true)),
    cell(p.sharpe, (v) => fixed(v, 2)),
    percent(p.turnover, 0),
    p.break_even === null
      ? "–"
      : interval(p.break_even, (v) => `${fixed(v * 1e4, 0)} bps`),
  ];
}

function portfolios(evidence: Evidence): string {
  const rows = evidence.models.flatMap((m: ModelEvidence) => [
    [`${escape(name(m.model))}, top decile`, ...leg(m.top)],
    [`${escape(name(m.model))}, top minus bottom`, ...leg(m.spread)],
  ]);
  return (
    table(
      "portfolios",
      [
        "Portfolio",
        "Gross a year",
        "Net a year",
        "Sharpe",
        "Turnover a month",
        "Break-even cost",
      ],
      rows,
    ) +
    prose(
      "Monthly, equal-weighted portfolios. The top-decile row is the top decile minus every scored stock. Net returns pay the one-way cost on every trade. The break-even cost is the one-way cost at which the net return reaches zero.",
    )
  );
}

function ladder(evidence: Evidence): string {
  const costs = evidence.models[0]?.ladder.map((step) => step.cost_bps) ?? [];
  const rows = evidence.models.map((m) => [
    escape(name(m.model)),
    ...m.ladder.map((step) => percent(step.net, 1, true)),
  ]);
  return (
    table(
      "ladder",
      [
        "Net top minus bottom a year, by one-way cost",
        ...costs.map((c) => `${escape(c)} bps`),
      ],
      rows,
    ) + prose("Each column charges that cost on every trade.")
  );
}

function alpha(a: FactorAlpha | null): string[] {
  if (a === null) return ["–", "–", "–", "–", "–"];
  const loadings = Object.entries(a.loadings)
    .map(
      ([factor, loading]) =>
        `${escape(FACTORS[factor] ?? factor)} ${fixed(loading, 2)}`,
    )
    .join(", ");
  return [
    percent(a.annual, 1, true),
    fixed(a.t_stat, 2),
    fixed(a.r2, 2),
    escape(a.months),
    loadings,
  ];
}

function multipleTesting(evidence: Evidence): string {
  const rows = evidence.models.map((m) => [
    escape(name(m.model)),
    fixed(m.deflated.sharpe, 2),
    fixed(m.deflated.benchmark, 2),
    fixed(m.deflated.probability, 3),
    escape(m.deflated.trials),
    ...alpha(m.alpha),
  ]);
  return (
    table(
      "deflated",
      [
        "Model",
        "Sharpe of the net spread",
        "Sharpe luck would reach",
        "Deflated Sharpe probability",
        "Specifications tried",
        "Five-factor alpha a year",
        "Alpha t-statistic",
        "R squared",
        "Alpha months",
        "Loadings",
      ],
      rows,
    ) +
    prose(
      "The deflated Sharpe probability is the chance that the net spread's Sharpe ratio beats the best one that luck alone would produce from the specifications tried, which the ledger below lists. Alpha is the net spread's return the Fama-French five factors do not explain.",
    )
  );
}

function years(evidence: Evidence): string {
  const names = evidence.models.map((m) => m.model);
  const rows = evidence.by_year.map((y) => [
    escape(y.year),
    ...names.map((model) =>
      interval(y.ic[model] ?? { value: null, lo: null, hi: null }, (v) =>
        fixed(v, 3, true),
      ),
    ),
    escape(y.dates),
    escape(y.months),
  ]);
  return (
    table(
      "years",
      ["Year", ...names.map((n) => escape(name(n))), "Dates", "Months"],
      rows,
    ) +
    prose(
      "Mean rank IC in each calendar year. A year of too few months has no interval.",
    )
  );
}

function scores(evidence: Evidence): string {
  const rows = evidence.by_score.map((b) => [
    escape(b.score),
    escape(b.rows),
    interval(b.hit_rate, (v) => percent(v)),
    interval(b.mean_excess, (v) => percent(v, 1, true)),
  ]);
  return (
    table(
      "buckets",
      ["Score", "Stock-dates", "Hit rate", "Mean excess return"],
      rows,
    ) +
    prose(
      `The share of stocks that beat SPY, and their mean excess return over the label horizon, for each LightGBM score. The base rate is ${percent(evidence.run.base_rate)}.`,
    )
  );
}

function features(evidence: Evidence): string {
  const rows = evidence.features.map((f) => [
    escape(f.feature),
    escape(f.family),
    interval(f.mean_ic, (v) => fixed(v, 3, true)),
    cell(f.t_stat, (v) => fixed(v, 2)),
    escape(f.dates),
  ]);
  return (
    table(
      "features",
      ["Feature", "Family", "Mean rank IC", "t-statistic", "Dates"],
      rows,
    ) +
    prose(
      `Each feature alone, over the same rows. A new factor needs a t-statistic of ${fixed(evidence.feature_hurdle_t, 1)} or more, up or down.`,
    )
  );
}

function trials(evidence: Evidence): string {
  const rows = evidence.trials.map((t) => [
    escape(t.description),
    escape(t.as_of),
    escape(t.months),
    fixed(t.sharpe, 2),
  ]);
  return (
    table("trials", ["Specification", "Run on", "Months", "Sharpe"], rows) +
    prose(
      "Every specification ever measured on the database this page comes from, each counted once. The more there are, the higher the bar a Sharpe ratio must clear.",
    )
  );
}

/**
 * The proof page: the stored out-of-sample result of the backtest and the
 * ledger of every specification tried. The build renders it from
 * `evidence.json`.
 */
export function renderEvidence(
  evidence: Evidence | null,
  status: LedgerStatus | null = null,
): string {
  if (evidence === null)
    return `${evidenceHeading(status)}<p class="border-warning bg-warning/10 mt-6 rounded-md border px-4 py-3 text-sm" id="no-evidence" role="note">No backtest has been stored, so there is no evidence and no score is shown. Run <code>eq backtest --final</code>, then <code>eq export</code>, and build again.</p>`;
  return `${evidenceHeading(status)}<p class="${MUTED} mt-2 text-sm">Out-of-sample results of a walk-forward backtest: each prediction was made by a model fitted only on earlier data. Every number below is read from the stored run.</p>${section("run-heading", "The run", run(evidence))}${section("verdict-heading", "Verdict", verdict(evidence))}${section("models-heading", "Models", models(evidence))}${section("portfolios-heading", "Portfolios", portfolios(evidence))}${section("ladder-heading", "Cost ladder", ladder(evidence))}${section("deflated-heading", "Multiple testing and factor alpha", multipleTesting(evidence))}${section("years-heading", "By year", years(evidence))}${section("buckets-heading", "By score", scores(evidence))}${section("features-heading", "Features", features(evidence))}${section("trials-heading", "Ledger of trials", trials(evidence))}`;
}
