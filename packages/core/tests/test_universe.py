from pathlib import Path

import pytest

from index_core.universe import read_universe


def _file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "universe.txt"
    path.write_text(text)
    return path


def test_reads_tickers_skipping_comments_and_blank_lines(tmp_path: Path) -> None:
    path = _file(tmp_path, "# demo\nAAPL\n\n  msft  # keep\n")

    assert read_universe(path) == ["AAPL", "MSFT"]


def test_rejects_the_benchmark_a_duplicate_and_an_empty_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="benchmark"):
        read_universe(_file(tmp_path, "AAPL\nSPY\n"))
    with pytest.raises(ValueError, match="twice"):
        read_universe(_file(tmp_path, "AAPL\naapl\n"))
    with pytest.raises(ValueError, match="no tickers"):
        read_universe(_file(tmp_path, "# nothing\n"))


def test_the_demo_universe_lists_thirty_distinct_tickers() -> None:
    demo = Path(__file__).parents[3] / "universes" / "demo30.txt"

    assert len(read_universe(demo)) == 30
