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
        lines.extend(
            f"  {ticker}: {count} ({_reason(result, str(ticker))})"
            for ticker, count in short.items()
        )
    lines.extend(_concepts(result))
    lines.extend(_tags(result))
    return "\n".join(lines) + "\n"


def _reason(result: Coverage, ticker: str) -> str:
    if ticker in result.no_company:
        return "no company at the filings source"
    absent = [str(c) for c, there in result.present.loc[ticker].items() if not there]
    return "missing " + ", ".join(absent)


def _concepts(result: Coverage) -> list[str]:
    members = len(result.present)
    lines = [f"Names with a fresh value, by concept (of {members}):"]
    lines.extend(
        f"  {concept}: {int(count)}" for concept, count in result.present.sum().items()
    )
    return lines


def _tags(result: Coverage) -> list[str]:
    """Every tag with the names that report it. A tag no name reports freshly is
    dead: it is listed as such, not left out."""
    dead = result.tags[result.tags["fresh"] == 0]
    lines = [
        "Names reporting each tag, fresh now and ever filed (a concept takes the "
        "first tag that has the period):"
    ]
    for concept, group in result.tags.groupby("concept", sort=False):
        lines.append(f"  {concept}")
        lines.extend(
            f"    {row.tag}: {row.fresh} fresh, {row.ever} ever"
            + ("  DEAD" if row.fresh == 0 else "")
            for row in group.itertuples()
        )
    lines.append(f"{len(dead)} dead tags of {len(result.tags)}.")
    return lines
