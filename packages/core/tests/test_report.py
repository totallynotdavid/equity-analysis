from pathlib import Path

from index_core.report import ScoresReport


def test_published_score_artifacts_remain_valid() -> None:
    root = Path(__file__).parents[3]
    report = ScoresReport.model_validate_json(
        (root / "deploy" / "scores.json").read_text()
    )
    assert report.model.holdout_auc == 0.43235278012453454
    assert report.model.holdout_auc_per_date is None
    assert report.model.holdout_auc_per_date_low is None
    assert report.model.holdout_auc_per_date_high is None
