"""Level 1 Prediction Ledger 純函式測試：排名、成熟回填、觀測窗未到不處理。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.research.level1 import ledger as lg


def test_rank_scores_orientation_and_stability():
    s = pd.Series({"2330": 0.9, "2317": 0.7, "1101": 0.7, "3008": 0.1})
    out = lg.rank_scores(s)
    assert out.loc["2330", "rank"] == 1
    assert out.loc["2330", "pct_rank"] == pytest.approx(1.0)
    assert out.loc["3008", "rank"] == 4
    # 同分以股號序穩定切割（1101 < 2317）
    assert out.loc["1101", "rank"] == 2
    assert out.loc["2317", "rank"] == 3
    assert (out["universe_size"] == 4).all()


def test_rank_scores_drops_nan():
    s = pd.Series({"2330": 0.9, "2317": np.nan})
    out = lg.rank_scores(s)
    assert list(out.index) == ["2330"]
    assert out.loc["2330", "universe_size"] == 1


def _close():
    dates = [f"2025-01-0{i}" for i in range(1, 7)]
    close = pd.DataFrame(index=pd.Index(dates, name="date"), data={
        "AAAA": [10.0, 10.0, 10.0, 11.0, 12.0, 13.0],
        "BBBB": [20.0, 20.0, 20.0, 19.0, 18.0, 17.0],
        "CCCC": [30.0, 30.0, 30.0, np.nan, np.nan, np.nan],  # 下市
    })
    return close


def test_compute_actuals_matured_only():
    close = _close()
    preds = pd.DataFrame({
        "prediction_date": ["2025-01-02"] * 3 + ["2025-01-05"] * 2,
        "stock_id": ["AAAA", "BBBB", "CCCC", "AAAA", "BBBB"],
        "pct_rank": [1.0, 0.5, 0.75, 1.0, 0.5],
    })
    out = lg.compute_actuals(preds, close, horizon=2)
    # 01-05 的 t+2 = 01-07 不存在 → 未成熟；01-02 的 CCCC 無終值 → 剔除
    assert set(zip(out.prediction_date, out.stock_id)) == {
        ("2025-01-02", "AAAA"), ("2025-01-02", "BBBB")}
    a = out.set_index("stock_id")
    assert a.loc["AAAA", "actual_return"] == pytest.approx(11.0 / 10.0 - 1)
    assert a.loc["AAAA", "actual_pct"] == pytest.approx(1.0)   # 兩檔中較強
    assert a.loc["BBBB", "actual_pct"] == pytest.approx(0.5)
    assert a.loc["AAAA", "rank_error"] == pytest.approx(0.0)
    assert a.loc["BBBB", "rank_error"] == pytest.approx(0.0)


def test_compute_actuals_empty_when_nothing_matured():
    close = _close()
    preds = pd.DataFrame({
        "prediction_date": ["2025-01-06"], "stock_id": ["AAAA"], "pct_rank": [1.0]})
    out = lg.compute_actuals(preds, close, horizon=5)
    assert out.empty
