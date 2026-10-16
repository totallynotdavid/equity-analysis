import { describe, expect, it } from "vitest";
import type { Evidence } from "../src/lib/evidence";
import { renderEvidence } from "../src/lib/render-evidence";
import held from "./fixtures/evidence.json";
import development from "./fixtures/evidence-development.json";
import { verdictOf } from "./support";

const HELD = held as unknown as Evidence;
const DEVELOPMENT = development as unknown as Evidence;

const SECTIONS = [
  "run",
  "verdict",
  "models",
  "portfolios",
  "ladder",
  "deflated",
  "years",
  "buckets",
  "features",
  "trials",
];

/** The text a reader sees: no tags, no entities. */
function text(html: string): string {
  return html
    .replace(/<[^>]*>/g, " ")
    .replaceAll("&amp;", "&")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&#39;", "'");
}

function numbersIn(value: unknown, into: number[] = []): number[] {
  if (typeof value === "number") into.push(value);
  else if (Array.isArray(value)) for (const v of value) numbersIn(v, into);
  else if (value !== null && typeof value === "object")
    for (const v of Object.values(value)) numbersIn(v, into);
  return into;
}

function stringsIn(value: unknown, into: string[] = []): string[] {
  if (typeof value === "string") into.push(value);
  else if (Array.isArray(value)) for (const v of value) stringsIn(v, into);
  else if (value !== null && typeof value === "object")
    for (const v of Object.values(value)) stringsIn(v, into);
  return into;
}

const TOKEN = /\d[\d,]*(?:\.\d+)?/g;

/** Every number printed that no figure of the evidence accounts for. */
function unexplained(html: string, evidence: Evidence): string[] {
  const figures = numbersIn(evidence);
  const words = new Set(
    stringsIn(evidence).flatMap((s) => s.match(TOKEN) ?? []),
  );
  return (text(html).match(TOKEN) ?? []).filter((token) => {
    if (words.has(token)) return false;
    const printed = Number(token.replaceAll(",", ""));
    const digits = token.split(".")[1]?.length ?? 0;
    return !figures.some((figure) =>
      [1, 100, 1e4].some(
        (scale) =>
          Math.abs(Math.abs(figure) * scale - printed) <=
          0.5 * 10 ** -digits + 1e-9,
      ),
    );
  });
}

/** The same run with every fractional figure moved by `step`. */
function shifted(value: unknown, step: number): unknown {
  if (typeof value === "number")
    return Number.isInteger(value) ? value : value + step;
  if (Array.isArray(value)) return value.map((v) => shifted(v, step));
  if (value !== null && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value).map(([k, v]) => [k, shifted(v, step)]),
    );
  return value;
}

describe("the evidence page", () => {
  const html = renderEvidence(HELD);

  it("has a section for each part of the stored run", () => {
    for (const id of SECTIONS) expect(html).toContain(`id="${id}"`);
  });

  it("lists the models, the checks and the ledger from the file", () => {
    for (const model of HELD.models)
      expect(html).toContain(
        model.model === "lightgbm" ? "LightGBM" : model.model,
      );
    for (const check of HELD.verdict.checks) expect(html).toContain(check.name);
    for (const trial of HELD.trials) expect(html).toContain(trial.description);
    expect(HELD.trials.length).toBeGreaterThan(0);
  });

  it("has a row for each year, bucket and feature of the file", () => {
    // One heading row, then a row for each entry.
    const rows = (id: string) =>
      html.match(new RegExp(`id="${id}".*?</table>`, "s"))?.[0].split("<tr")
        .length;

    expect(rows("years")).toBe(HELD.by_year.length + 2);
    expect(rows("buckets")).toBe(HELD.by_score.length + 2);
    expect(rows("features")).toBe(HELD.features.length + 2);
    expect(rows("trials")).toBe(HELD.trials.length + 2);
  });

  it("states that the held-out months decided and the score is not shown", () => {
    expect(HELD.verdict.decided).toBe(true);
    expect(html).toContain("The score is not shown.");
    expect(html).not.toContain("so the score is shown");
  });

  it("states that the score is shown when every check passed", () => {
    const shown = renderEvidence(verdictOf(HELD, "demo30", "pass"));

    expect(shown).toContain("pass every check, so the score is shown.");
    expect(shown).not.toContain("The score is not shown.");
  });

  it("says a failing check failed", () => {
    const failed = renderEvidence(verdictOf(HELD, "demo30", "fail"));

    expect(failed).toContain(">fail<");
    expect(failed).not.toContain(">pass<");
  });

  it("prints no number the stored run does not hold", () => {
    expect(unexplained(html, HELD)).toEqual([]);
  });

  it("follows the file: change the figures and the page changes with them", () => {
    const moved = shifted(HELD, 0.01234) as Evidence;

    const other = renderEvidence(moved);

    expect(other).not.toBe(html);
    expect(unexplained(other, moved)).toEqual([]);
    // The moved page must not match the original figures.
    expect(unexplained(other, HELD).length).toBeGreaterThan(0);
  });

  it("escapes what the file holds", () => {
    const hostile = {
      ...HELD,
      trials: [{ ...HELD.trials[0], description: "<img src=x onerror=1>" }],
    };

    const out = renderEvidence(hostile);

    expect(out).not.toContain("<img");
    expect(out).toContain("&lt;img");
  });
});

describe("a run on the development months", () => {
  const html = renderEvidence(DEVELOPMENT);

  it("gives no verdict, because these months chose the candidate", () => {
    expect(DEVELOPMENT.verdict.decided).toBe(false);
    expect(html).toContain("No verdict.");
    expect(html).not.toContain("The score is not shown.");
    expect(html).not.toContain("so the score is shown");
  });

  it("still prints only numbers the run holds", () => {
    expect(unexplained(html, DEVELOPMENT)).toEqual([]);
  });
});

describe("without a stored backtest", () => {
  const html = renderEvidence(null);

  it("says there is no evidence and how to make some", () => {
    expect(html).toContain('id="no-evidence"');
    expect(html).toContain("eq backtest --final");
  });

  it("shows none of the sections", () => {
    for (const id of SECTIONS) expect(html).not.toContain(`id="${id}"`);
  });
});
