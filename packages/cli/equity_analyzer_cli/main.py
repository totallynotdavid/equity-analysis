import argparse

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="eq",
        description="Rank US stocks with an open-source score.",
    )


def main(argv: Sequence[str] | None = None) -> None:
    build_parser().parse_args(argv)


if __name__ == "__main__":
    main()
