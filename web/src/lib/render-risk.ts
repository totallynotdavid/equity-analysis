import type { Estimate, Risk, RiskFit } from "./tickers";

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

function signed(text: string, negative: boolean): string {
  return negative ? `−${text}` : text;
}

function percent(value: number, digits = 1): string {
  return signed(`${Math.abs(value * 100).toFixed(digits)}%`, value < 0);
}

function number(value: number): string {
  return signed(Math.abs(value).toFixed(2), value < 0 && value <= -0.005);
}

const FACTORS: Record<string, { label: string; meaning: string }> = {
  mkt_rf: {
    label: "Market",
    meaning: "Mkt-RF: the return of the market above the risk-free rate",
  },
  smb: {
    label: "Size",
    meaning: "SMB: small stocks minus big stocks",
  },
  hml: {
    label: "Value",
    meaning: "HML: high book-to-market stocks minus low",
  },
  rmw: {
    label: "Profitability",
    meaning: "RMW: robust operating profitability minus weak",
  },
  cma: {
    label: "Investment",
    meaning: "CMA: conservative investment minus aggressive",
  },
};

function excludesZero(estimate: Estimate): boolean {
  return estimate.lo > 0 || estimate.hi < 0;
}

function row(
  name: string,
  meaning: string,
  key: string,
  estimate: Estimate,
  format: (value: number) => string,
): string {
  const strong = excludesZero(estimate);
  return `<tr class="border-b last:border-0" data-factor="${escape(key)}" data-excludes-zero="${strong}"><th class="py-2 pr-4 text-left font-medium" scope="row" title="${escape(meaning)}">${escape(name)}</th><td class="py-2 pl-4 text-right tabular-nums${strong ? " font-semibold" : ""}">${escape(format(estimate.value))}</td><td class="py-2 pl-4 text-right tabular-nums">${escape(format(estimate.lo))} to ${escape(format(estimate.hi))}</td></tr>`;
}

function loadings(fit: RiskFit): string {
  const rows = [
    row(
      "Alpha, a year",
      "The part of the return the five factors do not explain, over a year",
      "alpha",
      fit.alpha,
      (value) => percent(value),
    ),
    ...fit.loadings.map((loading) => {
      const known = FACTORS[loading.factor] ?? {
        label: loading.factor,
        meaning: loading.factor,
      };
      return row(known.label, known.meaning, loading.factor, loading, number);
    }),
  ].join("");
  return `<div class="overflow-x-auto"><table class="mt-3 w-full text-sm" id="risk-loadings"><thead class="text-muted-foreground border-b"><tr><th class="py-2 pr-4 text-left font-medium">Factor</th><th class="py-2 pl-4 text-right font-medium">Estimate</th><th class="py-2 pl-4 text-right font-medium">95% interval</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

/** Render the risk summary, factor intervals, or an unavailable estimate's reason. */
export function renderRisk(risk: Risk | null | undefined): string {
  if (risk === null || risk === undefined) return "";
  const title = `<h2 class="text-xl font-semibold">Risk and factor exposure</h2>`;
  if (risk.status !== "ok") {
    const label =
      risk.status === "insufficient_history"
        ? "Insufficient history"
        : "Not estimable";
    return `<section class="mt-10" id="risk">${title}<p class="mt-3 text-sm" data-status="${risk.status}">${label}: ${escape(risk.reason)}.</p></section>`;
  }
  const fit = risk.fit;
  const summary = `Over the ${escape(risk.window)} trading days from ${escape(fit.start)} to ${escape(fit.end)}, the stock's daily returns had a volatility of <strong>${escape(percent(fit.volatility))}</strong> a year. It fell at most <strong>${escape(percent(-fit.max_drawdown))}</strong> from a peak. Its beta against SPY was <strong>${escape(number(fit.beta.value))}</strong>, with a 95% interval of ${escape(number(fit.beta.lo))} to ${escape(number(fit.beta.hi))}.`;
  const limits = `The window ends on the last day of the Fama-French file, which trails the report's date. The table regresses the daily return above the risk-free rate on the five factors. An estimate in bold has an interval that excludes zero. The intervals use Newey-West standard errors, which allow for changing volatility and for correlation between days, and are the estimate plus or minus 1.96 of them. A loading describes the past window, not the future.`;
  const r2 = `<p class="mt-3 text-sm">The five factors explain <strong data-r2>${escape(fit.r2.toFixed(2))}</strong> of the variance of the stock's daily return (R²). The rest belongs to the company alone.</p>`;
  return `<section class="mt-10" id="risk">${title}<p class="mt-3" data-status="ok">${summary}</p><p class="text-muted-foreground mt-2 text-sm">${limits}</p>${loadings(fit)}${r2}</section>`;
}
