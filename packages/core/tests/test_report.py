import json

from index_core.report import ScoresReport


def test_scores_published_before_the_per_date_metrics_remain_valid() -> None:
    legacy = {
        "status": "experimental, not validated",
        "as_of": "2026-10-21",
        "universe": "demo30",
        "price_source": "tiingo",
        "facts_source": "edgar",
        "horizon_days": 63,
        "model": {
            "train_rows": 5340,
            "holdout_rows": 1440,
            "holdout_auc": 0.43235278012453454,
        },
        "rows": [{"ticker": "UNH", "rank": 1, "score": 10, "prob": 0.674}],
    }

    report = ScoresReport.model_validate_json(json.dumps(legacy))

    assert report.model.holdout_auc == 0.43235278012453454
    assert report.model.holdout_auc_per_date is None
    assert report.model.holdout_auc_per_date_low is None
    assert report.model.holdout_auc_per_date_high is None
