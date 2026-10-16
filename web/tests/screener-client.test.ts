// @vitest-environment happy-dom
import { beforeEach, describe, expect, it } from "vitest";
import { buildScreener, type ScreenerData } from "../src/lib/screener";
import { mount } from "../src/lib/screener-client";
import { report } from "./support";

const REPORTS = [
  report({ ticker: "AAA", revenue: 500e6, sector: "Technology" }),
  report({ ticker: "BBB", revenue: 100e6, sector: "Financials" }),
  report({ ticker: "CCC", revenue: 2000e6, sector: "Technology" }),
];

let data: ScreenerData;
let controls: HTMLElement;
let results: HTMLElement;

function rows(): string[] {
  return [...results.querySelectorAll("tbody tr")].map(
    (row) => row.querySelector("a")?.textContent ?? "",
  );
}

function type(name: string, value: string): void {
  const field = controls.querySelector<HTMLInputElement>(`[name="${name}"]`);
  if (field === null) throw new Error(`no field ${name}`);
  field.value = value;
  field.dispatchEvent(new Event("input", { bubbles: true }));
}

function header(key: string): HTMLElement {
  const link = results.querySelector<HTMLElement>(`a[data-sort="${key}"]`);
  if (link === null) throw new Error(`no header ${key}`);
  return link;
}

function open(query: string): void {
  window.history.replaceState(null, "", `/${query}`);
  mount(controls, results, data);
}

beforeEach(() => {
  data = buildScreener(null, REPORTS, null);
  document.body.innerHTML = '<div id="controls"></div><div id="results"></div>';
  controls = document.getElementById("controls") as HTMLElement;
  results = document.getElementById("results") as HTMLElement;
  window.history.replaceState(null, "", "/");
});

describe("the screener in the browser", () => {
  it("paints the table the address asks for", () => {
    open("?min.revenue=200&sort=-revenue");

    expect(rows()).toEqual(["CCC", "AAA"]);
    expect(
      controls.querySelector<HTMLInputElement>('[name="min.revenue"]')?.value,
    ).toBe("200");
  });

  it("filters as the reader types and writes the filter to the address", () => {
    open("");

    type("min.revenue", "200");

    expect(rows()).toEqual(["AAA", "CCC"]);
    expect(window.location.search).toBe("?min.revenue=200");
  });

  it("clears the address when the filter is emptied", () => {
    open("?min.revenue=200");

    type("min.revenue", "");

    expect(rows()).toEqual(["AAA", "BBB", "CCC"]);
    expect(window.location.search).toBe("");
  });

  it("does not add a history entry for each key typed", () => {
    open("");
    const before = window.history.length;

    type("q", "a");
    type("q", "aa");

    expect(window.history.length).toBe(before);
  });

  it("sorts by a heading without leaving the page, keeping the filter", () => {
    open("?min.revenue=200");
    const before = window.history.length;

    header("revenue").click();

    expect(rows()).toEqual(["CCC", "AAA"]);
    expect(window.location.search).toBe("?min.revenue=200&sort=-revenue");
    expect(window.history.length).toBe(before + 1);
  });

  it("carries the sort into the next filter", () => {
    open("");
    header("revenue").click();

    type("min.revenue", "200");

    expect(rows()).toEqual(["CCC", "AAA"]);
    expect(window.location.search).toBe("?min.revenue=200&sort=-revenue");
  });

  it("reverses the sort on a second click", () => {
    open("");

    header("revenue").click();
    header("revenue").click();

    expect(rows()).toEqual(["BBB", "AAA", "CCC"]);
    expect(window.location.search).toBe("?sort=revenue");
  });

  it("returns to an earlier table when the reader goes back", () => {
    open("");
    header("revenue").click();
    expect(rows()).toEqual(["CCC", "AAA", "BBB"]);

    window.history.replaceState(null, "", "/?min.revenue=200");
    window.dispatchEvent(new PopStateEvent("popstate"));

    expect(rows()).toEqual(["AAA", "CCC"]);
    expect(
      controls.querySelector<HTMLInputElement>('[name="min.revenue"]')?.value,
    ).toBe("200");
  });

  it("filters by sector from the checkboxes", () => {
    open("");

    const box = controls.querySelector<HTMLInputElement>(
      '[name="sector"][value="Financials"]',
    ) as HTMLInputElement;
    box.checked = true;
    box.dispatchEvent(new Event("input", { bubbles: true }));

    expect(rows()).toEqual(["BBB"]);
    expect(window.location.search).toBe("?sector=Financials");
  });

  it("shows only the columns chosen", () => {
    open("?col=revenue");

    expect(
      [...results.querySelectorAll("thead th")].map((th) => th.textContent),
    ).toEqual(["Ticker ▲", "Name", "Sector", "Revenue"]);
  });
});
