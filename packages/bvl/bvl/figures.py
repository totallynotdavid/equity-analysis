"""Draws the study figures as PNG files with matplotlib's file-only backend."""

from datetime import date
from typing import TYPE_CHECKING

import matplotlib as mpl
import matplotlib.dates as mdates


mpl.use("Agg")

import matplotlib.pyplot as plt


if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    import pandas as pd

    from matplotlib.axes import Axes
    from matplotlib.figure import Figure

INK = "#1f2933"
ACCENT = "#c2410c"
MUTED = "#9aa5b1"


def _day(iso: str) -> float:
    """An ISO date as a matplotlib date number, for marks on a date axis."""
    return float(mdates.date2num(date.fromisoformat(iso)))  # type: ignore[no-untyped-call]


def _axes(title: str, ylabel: str) -> tuple[Figure, Axes]:
    figure, axes = plt.subplots(figsize=(9, 4.5), dpi=150)
    axes.set_title(title, loc="left", fontsize=12, color=INK)
    axes.set_ylabel(ylabel, color=INK)
    axes.spines[["top", "right"]].set_visible(False)
    axes.grid(axis="y", color="#e4e7eb", linewidth=0.8)
    return figure, axes


def _save(figure: Figure, path: Path, note: str) -> None:
    figure.text(0.01, 0.01, note, fontsize=7, color=MUTED)
    figure.tight_layout(rect=(0, 0.03, 1, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path)
    plt.close(figure)


def lines(
    series: Mapping[str, pd.Series],
    path: Path,
    *,
    title: str,
    ylabel: str,
    note: str,
    marks: Mapping[str, str] | None = None,
    span: tuple[str, str, str] | None = None,
) -> None:
    """One line per series over a shared x axis.

    `marks` maps a date to a label drawn as a vertical line; `span` is a
    (start, end, label) period drawn as a shaded band.
    """
    figure, axes = _axes(title, ylabel)
    colours = [INK, ACCENT, MUTED]
    for (label, values), colour in zip(series.items(), colours, strict=False):
        axes.plot(values.index, values.to_numpy(), label=label, color=colour, lw=1.4)
    if span is not None:
        start, end, label = span
        left = _day(start)
        axes.axvspan(left, _day(end), color=ACCENT, alpha=0.12, lw=0)
        axes.annotate(
            label,
            (left, 1.0),
            xycoords=("data", "axes fraction"),
            va="top",
            ha="right",
            fontsize=7,
            color=ACCENT,
        )
    for when, label in (marks or {}).items():
        day = _day(when)
        axes.axvline(day, color=ACCENT, lw=0.8, ls="--")
        axes.annotate(
            label,
            (day, 1.0),
            xycoords=("data", "axes fraction"),
            rotation=90,
            va="top",
            ha="right",
            fontsize=7,
            color=ACCENT,
        )
    if len(series) > 1:
        axes.legend(frameon=False, fontsize=8)
    _save(figure, path, note)


def bars(
    values: pd.Series,
    path: Path,
    *,
    title: str,
    ylabel: str,
    note: str,
    errors: tuple[pd.Series, pd.Series] | None = None,
) -> None:
    """Vertical bars by label, with optional low and high error bounds."""
    figure, axes = _axes(title, ylabel)
    positions = range(len(values))
    yerr = None
    if errors is not None:
        low, high = errors
        yerr = [values - low, high - values]
    axes.bar(positions, values.to_numpy(), color=INK, yerr=yerr, ecolor=ACCENT)
    axes.set_xticks(list(positions), [str(i) for i in values.index], fontsize=8)
    axes.axhline(0, color=MUTED, lw=0.8)
    _save(figure, path, note)
