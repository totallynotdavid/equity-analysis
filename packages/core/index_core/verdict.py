"""The rule that decides whether a score may be shown. It is fixed here, before
any real result is read, and `eq backtest` prints it beside the numbers.

A score is shown only if the held-out months of the candidate model satisfy all
three:

1. The 95% interval of the net decile-spread return excludes zero.
2. The Newey-West t-statistic of the mean rank IC is above `IC_T_MIN`.
3. The Deflated Sharpe probability of the net spread is above `DSR_MIN`.

The candidate is chosen on the development months, never on the months the
checks test. It is LightGBM only if it beats both baselines there, with a higher
mean rank IC and a higher net spread. Otherwise it is the baseline with the
higher net spread. A development backtest has no other months, so it chooses
and tests on the same ones and prints no verdict.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Mapping

    from index_core.pipeline import ModelResult

IC_T_MIN = 2.0
DSR_MIN = 0.95
BASELINES = ("ridge", "composite")


@dataclass(frozen=True)
class Check:
    name: str
    value: str
    passed: bool


@dataclass(frozen=True)
class Verdict:
    candidate: str
    lightgbm_stays: bool
    checks: list[Check]
    # Each model's mean rank IC and net spread on the months that chose the
    # candidate.
    basis: dict[str, tuple[float, float]]

    @property
    def show_score(self) -> bool:
        return all(check.passed for check in self.checks)


def decide(
    selection: Mapping[str, ModelResult], tested: Mapping[str, ModelResult]
) -> Verdict:
    """Pick the candidate on the `selection` months, then test it on the `tested`
    months."""
    lightgbm = selection["lightgbm"]
    stays = all(
        lightgbm.evaluation.mean_ic.value > selection[name].evaluation.mean_ic.value
        and lightgbm.spread.net.value > selection[name].spread.net.value
        for name in BASELINES
    )
    candidate = (
        "lightgbm"
        if stays
        else max(BASELINES, key=lambda name: selection[name].spread.net.value)
    )
    result = tested[candidate]
    interval = result.spread.net
    return Verdict(
        candidate=candidate,
        lightgbm_stays=stays,
        checks=[
            Check(
                "net decile-spread interval excludes zero",
                f"[{interval.low:+.1%}, {interval.high:+.1%}] a year",
                interval.low > 0,
            ),
            Check(
                f"rank IC t-statistic above {IC_T_MIN:g}",
                f"{result.evaluation.ic_t_stat:.2f}",
                result.evaluation.ic_t_stat > IC_T_MIN,
            ),
            Check(
                f"Deflated Sharpe probability above {DSR_MIN:g}",
                f"{result.deflated.probability:.3f}",
                result.deflated.probability > DSR_MIN,
            ),
        ],
        basis={
            name: (model.evaluation.mean_ic.value, model.spread.net.value)
            for name, model in selection.items()
        },
    )
