import type { Scores } from "./scores";

const ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

export function escape(value: string | number): string {
  return String(value).replace(/[&<>"']/g, (char) => ESCAPES[char]);
}

// A null facts_source means no filings were stored, so none can be unreal.
// A file without the fields is not trusted either.
function unrealSources(scores: Scores): string[] {
  return [
    { kind: "prices", source: scores.price_source, real: "tiingo" },
    { kind: "filings", source: scores.facts_source, real: "edgar" },
  ]
    .filter(({ source, real }) => source !== real && source !== null)
    .map(({ kind, source }) =>
      source === "synthetic"
        ? `synthetic ${kind}`
        : `${kind} of unknown source`,
    );
}

export function sourcesWarning(scores: Scores): string {
  const unreal = unrealSources(scores);
  if (unreal.length === 0) return "";
  return `<p class="border-error bg-error/10 text-foreground mt-4 rounded-md border px-4 py-3 text-sm font-medium" role="alert">These scores use ${escape(unreal.join(" and "))}, not market data. They mean nothing.</p>`;
}

const LEFT_OUT_SHOWN = 10;

export function leftOutNote(scores: Scores): string {
  const items = scores.left_out ?? [];
  if (items.length === 0) return "";
  const shown = items
    .slice(0, LEFT_OUT_SHOWN)
    .map(({ ticker, reason }) => `${escape(ticker)} (${escape(reason)})`)
    .join(", ");
  const more =
    items.length > LEFT_OUT_SHOWN
      ? ` and ${items.length - LEFT_OUT_SHOWN} more`
      : "";
  return `<p class="text-muted-foreground mt-2 text-sm" id="left-out">Left out of the scores: ${shown}${more}.</p>`;
}

function auc(value: number | null): string {
  return value === null ? "not available" : value.toFixed(3);
}

export function modelNote(scores: Scores): string {
  if (scores.model === undefined) return "";
  const pooled = auc(scores.model.holdout_auc ?? null);
  const value = scores.model.holdout_auc_per_date;
  const low = scores.model.holdout_auc_per_date_low;
  const high = scores.model.holdout_auc_per_date_high;
  const perDate =
    typeof value === "number" &&
    typeof low === "number" &&
    typeof high === "number"
      ? ` Mean per-date holdout AUC ${auc(value)} (95% block-bootstrap interval ${auc(low)}–${auc(high)}).`
      : "";
  return (
    `<p class="text-muted-foreground mt-2 text-sm" id="model-note">` +
    `Latest fit: pooled holdout AUC ${pooled}.${perDate} ` +
    "AUC 0.5 is chance; this is an experimental diagnostic, not validation." +
    "</p>"
  );
}
