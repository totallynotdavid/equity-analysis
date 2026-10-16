import type { DcfModel, Valuation } from "./tickers";

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

function percent(value: number, digits = 1): string {
  const text = Math.abs(value * 100).toFixed(digits);
  return `${value < 0 ? "−" : ""}${text}%`;
}

function perShare(value: number): string {
  return `$${value.toFixed(2)}`;
}

/** Format large dollar amounts with B or M suffixes. */
function compact(value: number): string {
  const sign = value < 0 ? "−" : "";
  const size = Math.abs(value);
  if (size >= 1e9) return `${sign}$${(size / 1e9).toFixed(1)}B`;
  if (size >= 1e6) return `${sign}$${(size / 1e6).toFixed(1)}M`;
  return `${sign}$${size.toFixed(0)}`;
}

function gridTable(model: DcfModel): string {
  const head = model.growths
    .map(
      (growth) =>
        `<th class="py-2 pl-4 text-right font-medium">${escape(percent(growth, 0))}</th>`,
    )
    .join("");
  const rows = model.rows
    .map((row) => {
      const cells = row.values
        .map((value) => {
          const above = value >= model.price;
          return `<td class="py-2 pl-4 text-right tabular-nums${above ? " font-semibold" : ""}" data-above-price="${above}">${escape(perShare(value))}</td>`;
        })
        .join("");
      const implied =
        row.implied_growth === null ? "–" : percent(row.implied_growth);
      return `<tr class="border-b last:border-0"><th class="py-2 pr-4 text-left font-medium" scope="row">${escape(percent(row.wacc, 2))}</th>${cells}<td class="py-2 pl-4 text-right tabular-nums" data-implied-growth>${escape(implied)}</td></tr>`;
    })
    .join("");
  return `<div class="overflow-x-auto"><table class="mt-3 w-full text-sm" id="valuation-grid"><thead class="text-muted-foreground border-b"><tr><th class="py-2 pr-4 text-left font-medium">Discount rate</th>${head}<th class="py-2 pl-4 text-right font-medium" title="The growth that makes the model worth the market price at this discount rate">Implied</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function inputs(model: DcfModel): string {
  const items: [string, string][] = [
    ["Price", perShare(model.price)],
    ["Market value", compact(model.market_value)],
    ["Net debt", compact(model.net_debt)],
    ["Enterprise value", compact(model.enterprise_value)],
    ["Free cash flow, twelve months", compact(model.fcf)],
    ["Plus after-tax interest, before financing", compact(model.fcf0)],
    [
      "Beta against SPY",
      `${model.beta_raw.toFixed(2)}, ${model.beta.toFixed(2)} after shrinking toward 1`,
    ],
    ["Risk-free rate", percent(model.risk_free, 2)],
    ["Equity risk premium, assumed", percent(model.equity_risk_premium, 2)],
    ["Cost of equity", percent(model.cost_of_equity, 2)],
    ["Cost of debt, before tax", percent(model.cost_of_debt, 2)],
    ["Tax rate", percent(model.tax_rate, 1)],
    ["Terminal growth", percent(model.terminal_growth, 2)],
  ];
  const rows = items
    .map(
      ([name, value]) =>
        `<div class="flex justify-between gap-6 border-b py-1 last:border-0"><dt class="text-muted-foreground">${escape(name)}</dt><dd class="tabular-nums">${escape(value)}</dd></div>`,
    )
    .join("");
  return `<details class="mt-4 text-sm"><summary class="text-muted-foreground cursor-pointer">The inputs</summary><dl class="mt-2 max-w-md">${rows}</dl></details>`;
}

/**
 * The valuation section of a ticker page: what the price implies, the model's
 * value per share across discount rates and growth, and the inputs. It states
 * no fair value. Nothing is shown for a report with no valuation.
 */
export function renderValuation(
  valuation: Valuation | null | undefined,
): string {
  if (valuation === null || valuation === undefined) return "";
  const title = `<h2 class="text-xl font-semibold">Valuation</h2>`;
  if (valuation.status === "not_meaningful")
    return `<section class="mt-10" id="valuation">${title}<p class="mt-3 text-sm" data-status="not_meaningful">Not meaningful: ${escape(valuation.reason)}.</p></section>`;
  const model = valuation.model;
  const summary = `At ${escape(perShare(model.price))} a share, the price implies free cash flow growth of <strong>${escape(percent(model.implied_growth))}</strong> a year for ${escape(model.years)} years, then ${escape(percent(model.terminal_growth))} for ever, discounted at ${escape(percent(model.wacc, 2))}. The terminal value is ${escape(percent(model.tv_share, 0))} of the enterprise value.`;
  const limits = `This is not a fair value. A discounted cash flow depends on its assumptions, and the grid shows by how much: it gives the model's value of a share for each discount rate and each growth of free cash flow over the ${escape(model.years)} years. Values at or above the price are in bold. The last column is the growth that makes the model worth the price at that discount rate. The equity risk premium is an assumption, and free cash flow does not deduct stock compensation.`;
  return `<section class="mt-10" id="valuation">${title}<p class="mt-3" data-status="ok">${summary}</p><p class="text-muted-foreground mt-2 text-sm">${limits}</p>${gridTable(model)}${inputs(model)}</section>`;
}
