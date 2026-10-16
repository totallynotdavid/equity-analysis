import { escape } from "./render";
import type { Series, TickerReport } from "./tickers";

interface Licence {
  source: string;
  link: string;
  use: string;
  terms: string;
}

/**
 * The data the site rests on and the terms it holds each under. Each entry is
 * the provider's own wording, shortened, with the page it comes from.
 */
export const LICENCES: Licence[] = [
  {
    source: "Tiingo",
    link: "https://www.tiingo.com/about/pricing",
    use: "Daily prices, which the scores and their evaluation are computed from.",
    terms:
      "The data is licensed for internal use only; it may not be displayed or shared with another person or organisation. The site shows the scores and the evaluation of them. It shows no price and no figure of a stock that is computed from prices.",
  },
  {
    source: "SEC EDGAR",
    link: "https://www.sec.gov/privacy#dissemination",
    use: "Company facts from XBRL filings, which every fundamental figure comes from. Each figure on a ticker page links to the filing that reported it.",
    terms:
      "Information presented on sec.gov is public information and may be copied or further distributed. The SEC asks that it be cited as its source.",
  },
  {
    source: "Kenneth R. French Data Library",
    link: "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html",
    use: "The Fama-French five factors and the risk-free rate, which the factor alpha of the evaluation uses.",
    terms:
      "Copyright Eugene F. Fama and Kenneth R. French. The library states no licence beyond the copyright, so the site cites it and does not republish its files.",
  },
  {
    source: "Wikipedia, List of S&P 500 companies",
    link: "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    use: "Which stocks are in the universe, since when, and each one's sector.",
    terms:
      "Licensed CC BY-SA 4.0: use needs attribution and any adapted version must be shared alike.",
  },
];

function tagsOf(reports: TickerReport[], metric: string): string[] {
  const tags = new Set<string>();
  for (const report of reports)
    for (const series of report.series)
      if (series.metric === metric)
        for (const point of series.points)
          for (const part of point.parts)
            if (part.sign > 0 && part.tag !== "") tags.add(part.tag);
  return [...tags].sort();
}

function formulaOf(reports: TickerReport[], metric: string): string | null {
  for (const report of reports)
    for (const series of report.series)
      if (series.metric === metric)
        for (const point of series.points)
          if (point.formula !== null) return point.formula;
  return null;
}

function metrics(reports: TickerReport[]): string {
  const first = new Map<string, Series>();
  for (const report of reports)
    for (const series of report.series)
      if (!first.has(series.metric)) first.set(series.metric, series);
  if (first.size === 0)
    return `<p class="text-muted-foreground mt-3 text-sm">No ticker reports were published, so there are no metrics to list.</p>`;
  const rows = [...first]
    .map(([metric, series]) => {
      const formula = formulaOf(reports, metric);
      const definition =
        formula !== null
          ? `${escape(formula)}`
          : `<span class="text-muted-foreground">Reported as</span> ${tagsOf(
              reports,
              metric,
            )
              .map((tag) => `<code>${escape(tag)}</code>`)
              .join(", ")}`;
      return `<tr class="border-b last:border-0" data-metric="${escape(metric)}"><td class="text-muted-foreground py-2 pr-4">${escape(series.group)}</td><th class="py-2 pr-4 text-left font-medium" scope="row">${escape(series.label)}</th><td class="py-2 pr-4"><code>${escape(metric)}</code></td><td class="py-2 pr-4">${escape(series.unit)}</td><td class="py-2">${definition}</td></tr>`;
    })
    .join("");
  return `<div class="overflow-x-auto"><table class="mt-3 w-full text-left text-sm" id="metrics"><thead class="text-muted-foreground border-b"><tr><th class="py-2 pr-4 font-medium">Group</th><th class="py-2 pr-4 font-medium">Metric</th><th class="py-2 pr-4 font-medium">Name</th><th class="py-2 pr-4 font-medium">Unit</th><th class="py-2 font-medium">Definition</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function licences(): string {
  const rows = LICENCES.map(
    (licence) =>
      `<tr class="border-b align-top last:border-0"><th class="py-2 pr-4 text-left font-medium" scope="row"><a class="underline" href="${escape(licence.link)}" rel="noreferrer">${escape(licence.source)}</a></th><td class="py-2 pr-4">${escape(licence.use)}</td><td class="py-2">${escape(licence.terms)}</td></tr>`,
  ).join("");
  return `<div class="overflow-x-auto"><table class="mt-3 w-full text-left text-sm" id="licences"><thead class="text-muted-foreground border-b"><tr><th class="py-2 pr-4 font-medium">Source</th><th class="py-2 pr-4 font-medium">Used for</th><th class="py-2 font-medium">Terms</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function section(id: string, heading: string, body: string): string {
  return `<section class="mt-12" id="${id}"><h2 class="text-xl font-semibold">${heading}</h2>${body}</section>`;
}

function prose(text: string): string {
  return `<p class="mt-3 text-sm">${text}</p>`;
}

/**
 * The method page: how the numbers are made, the definition of every metric
 * the ticker reports, and the licence of each data source. The metrics are read
 * from the reports, so the page lists what the site shows.
 */
export function renderMethod(reports: TickerReport[]): string {
  return `<h1 class="text-primary text-4xl font-bold tracking-tight">Method</h1>${section(
    "score",
    "The score",
    prose(
      'A gradient-boosted model, fitted only on filings and prices that were public at the time, estimates each stock\'s chance of beating the S&amp;P 500 over the label horizon. The stocks are ranked and cut into ten equal groups, and the score is the group, the top one being the best. The score is shown only if the model passes the held-out tests on the <a class="underline" href="/evidence">evidence page</a>: a net return whose interval excludes zero, a significant rank correlation, and a Deflated Sharpe probability that survives the number of specifications tried.',
    ),
  )}${section(
    "metrics-heading",
    "Metrics",
    prose(
      "Each fundamental figure is read from the XBRL filings of the company. A figure is the twelve months to its date for the income statement and cash flow, and the balance at its date for the balance sheet. A derived metric names the formula it applies to those figures. A percentile is the share of the universe at or below the value, and is missing where too few peers report it. Every figure on a ticker page links to the filing that reported it.",
    ) + metrics(reports),
  )}${section(
    "licences-heading",
    "Data and licences",
    prose(
      "The site uses these sources. Each is listed with what it is used for and the terms it is held under.",
    ) + licences(),
  )}`;
}
