"""The plain-text report that `eq coverage` prints."""

from typing import TYPE_CHECKING

from index_core.sources.edgar import CONCEPTS


if TYPE_CHECKING:
    from index_core.pipeline import Coverage


def render(result: Coverage, universe: str, minimum: int) -> str:
    counts = result.concepts
    short = counts[counts < minimum].sort_values(kind="stable")
    source = result.facts_source or "no"
    lines = [
        f"Fundamentals coverage, {universe} universe, {source} filings, "
        f"as of {result.as_of}",
        f"{len(counts) - len(short)} of {len(counts)} names have at least "
        f"{minimum} of {len(CONCEPTS)} concepts with a fresh filed value.",
    ]
    if short.empty:
        lines.append("No name has fewer.")
    else:
        lines.append(f"{len(short)} with fewer (a missing value stays missing):")
        lines.extend(f"  {ticker}: {count}" for ticker, count in short.items())
    return "\n".join(lines) + "\n"
