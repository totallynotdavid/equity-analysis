import type { Scores } from "./scores";

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

/**
 * The part of the page that depends on the scores. The build renders it from
 * the scores file and the browser renders it again from the API, so both go
 * through this one function.
 */
export function renderScores(scores: Scores | null): string {
  if (scores === null) {
    return `<p class="text-muted-foreground mt-8 text-sm">No scores yet. Run <code>eq run</code> and build again.</p>`;
  }
  const unreal = unrealSources(scores);
  const warning =
    unreal.length > 0
      ? `<p class="border-error bg-error/10 text-foreground mt-4 rounded-md border px-4 py-3 text-sm font-medium" role="alert">These scores use ${escape(unreal.join(" and "))}, not market data. They mean nothing.</p>`
      : "";
  const rows = scores.rows
    .map(
      (row) =>
        `<tr class="border-b last:border-0"><td class="py-2 pr-4 tabular-nums">${escape(row.rank)}</td><td class="py-2 pr-4 font-medium">${escape(row.ticker)}</td><td class="py-2 text-right tabular-nums">${escape(row.score)}</td></tr>`,
    )
    .join("");
  return `${warning}<p class="text-muted-foreground mt-8 text-sm">As of ${escape(scores.as_of)}. Score 10 is the top tenth of ${scores.rows.length} stocks by estimated chance of beating the S&amp;P 500 over the next ${escape(scores.horizon_days)} trading days.</p><table class="mt-3 w-full text-left text-sm" id="scores"><thead class="text-muted-foreground border-b"><tr><th class="py-2 pr-4 font-medium">Rank</th><th class="py-2 pr-4 font-medium">Ticker</th><th class="py-2 text-right font-medium">Score</th></tr></thead><tbody>${rows}</tbody></table>`;
}
