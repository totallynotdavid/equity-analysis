import {
  parseState,
  renderControls,
  renderResults,
  serializeState,
  type ScreenerData,
  type State,
} from "./screener";

export function mount(
  controls: HTMLElement,
  results: HTMLElement,
  data: ScreenerData,
  win: Window = window,
): void {
  let state = parseState(win.location.search, data);

  const paintResults = () => {
    results.innerHTML = renderResults(data, state);
  };
  const paintAll = () => {
    controls.innerHTML = renderControls(data, state);
    paintResults();
  };
  const go = (next: State, push: boolean) => {
    state = next;
    const query = serializeState(state);
    const url = `${win.location.pathname}${query === "" ? "" : `?${query}`}`;
    if (push) win.history.pushState(null, "", url);
    else win.history.replaceState(null, "", url);
    paintResults();
  };
  const formState = (form: HTMLFormElement) =>
    parseState(
      new URLSearchParams(
        [...new FormData(form)].map(([name, value]) => [name, String(value)]),
      ),
      data,
    );

  controls.addEventListener("input", (event) => {
    const form = (event.target as Element).closest("form");
    if (form !== null) go(formState(form), false);
  });
  controls.addEventListener("submit", (event) => {
    event.preventDefault();
    go(formState(event.target as HTMLFormElement), false);
  });
  results.addEventListener("click", (event) => {
    const link = (event.target as Element).closest<HTMLAnchorElement>(
      "a[data-sort]",
    );
    if (link === null) return;
    event.preventDefault();
    go({ ...state, sort: parseState(link.search, data).sort }, true);
    const field = controls.querySelector<HTMLInputElement>('[name="sort"]');
    if (field !== null) field.value = state.sort ?? "";
  });
  win.addEventListener("popstate", () => {
    state = parseState(win.location.search, data);
    paintAll();
  });

  paintAll();
}
