import json
from collections import namedtuple

import pandas as pd
import pytest

from equity_analyzer_core.store_data import (
    store_results_to_excel,
    store_results_to_json,
)


Result = namedtuple("Result", ["ticker", "score"])

ROWS = [("AAA", 1.5), ("BBB", 2.5)]

INPUTS = {
    "namedtuple": [Result(*row) for row in ROWS],
    "dict": [{"ticker": t, "score": s} for t, s in ROWS],
}


@pytest.fixture(params=INPUTS)
def results(request):
    return INPUTS[request.param]


def test_excel_writes_workbook(results, tmp_path):
    path = tmp_path / "out.xlsx"

    store_results_to_excel(results, str(path))

    df = pd.read_excel(path)
    assert list(df.columns) == ["ticker", "score"]
    assert list(df.itertuples(index=False, name=None)) == ROWS


def test_excel_appends_sheet_to_existing_workbook(results, tmp_path):
    path = tmp_path / "out.xlsx"

    store_results_to_excel(results, str(path), sheet_name="first")
    store_results_to_excel(results, str(path), sheet_name="second")

    sheets = pd.read_excel(path, sheet_name=None)
    assert list(sheets) == ["first", "second"]


def test_json_writes_results_under_key(results, tmp_path):
    path = tmp_path / "out.json"

    store_results_to_json(results, str(path), key="file.xlsx")

    data = json.loads(path.read_text())
    assert data == {
        "file.xlsx": [{"ticker": t, "score": s} for t, s in ROWS],
    }
