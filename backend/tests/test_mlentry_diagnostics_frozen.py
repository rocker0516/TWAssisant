"""§18 唯讀 Frozen diagnostics：Lift@K（row/day-weighted）、timing、ranking、regime；全部 diagnostic only。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.mlentry.evaluation import diagnostics_frozen as dfz

TARGET, STOP, TIMEOUT = 1, 2, 4


def _frame():
    """2 天 × 6 檔。score 由高到低 s0..s5；gate_pass 前 4 檔。
    day1: target 於 s0,s1,s4；stop 於 s2。 day2: target 於 s1；stop 於 s0,s3。"""
    rows = []
    spec = {"2026-01-01": {"target": {"s0", "s1", "s4"}, "stop": {"s2"}, "hit": {"s0": 2, "s1": 7, "s4": 3}},
            "2026-01-02": {"target": {"s1"}, "stop": {"s0", "s3"}, "hit": {"s1": 4}}}
    for d, sp in spec.items():
        for i in range(6):
            sid = f"s{i}"
            t, s = sid in sp["target"], sid in sp["stop"]
            rows.append({"signal_date": d, "stock_id": sid, "gate_pass": i < 4, "recommendation_score": 1.0 - i * 0.1,
                         "target": float(t), "stop": float(s), "event_type": TARGET if t else (STOP if s else TIMEOUT),
                         "return_10d": 0.1 if t else (-0.05 if s else 0.0), "mfe_10d": 0.1, "mae_10d": -0.02,
                         "target_first_hit_day": sp["hit"].get(sid, np.nan)})
    return pd.DataFrame(rows)


def test_topk_mask_uses_gate_pass_and_score_order():
    df = _frame()
    m = dfz.topk_mask(df, 2)
    assert set(df.loc[m, "stock_id"]) == {"s0", "s1"} and int(m.sum()) == 4
    df.loc[0, "recommendation_score"] = np.nan                      # day1 s0 無分數 → 不進 Top-K
    m2 = dfz.topk_mask(df, 2)
    assert set(df.loc[m2 & (df["signal_date"] == "2026-01-01"), "stock_id"]) == {"s1", "s2"}


def test_lift_at_k_row_and_day_weighted():
    out = dfz.lift_at_k(_frame(), ks=(1, 2))
    assert set(out) == {"1", "2"} and out["1"]["diagnostic_only"] is True and out["1"]["not_used_for_policy"] is True
    rw = out["2"]["row_weighted"]
    # Top-2 = s0,s1 兩天共 4 列：target 3/4；市場基率 = 全 12 列 target 4/12
    assert rw["target_rate"] == pytest.approx(0.75) and rw["market_target_rate"] == pytest.approx(4 / 12)
    assert rw["target_lift"] == pytest.approx(0.75 / (4 / 12)) and rw["n"] == 4 and rw["days_with_rec"] == 2
    assert rw["stop_rate"] == pytest.approx(0.25) and rw["market_stop_rate"] == pytest.approx(3 / 12)
    dw = out["2"]["day_weighted"]
    # day1 Top-2 target 2/2, market 3/6；day2 Top-2 target 1/2, market 1/6 → 平均 0.75 vs (0.5+0.1667)/2
    assert dw["target_rate"] == pytest.approx(0.75) and dw["market_target_rate"] == pytest.approx((3 / 6 + 1 / 6) / 2)
    assert dw["target_lift"] == pytest.approx(0.75 / ((3 / 6 + 1 / 6) / 2))


def test_lift_at_k_empty_gate_days_and_k_larger_than_pool():
    df = _frame()
    df.loc[df["signal_date"] == "2026-01-02", "gate_pass"] = False
    out = dfz.lift_at_k(df, ks=(10,))
    rw = out["10"]["row_weighted"]
    assert rw["n"] == 4 and rw["days_with_rec"] == 1                   # 只有 day1 的 4 個 gate_pass
    assert out["10"]["day_weighted"]["days_with_rec"] == 1


def test_timing_top5():
    out = dfz.timing(_frame(), k=5)
    # Top-4（gate_pass 只有 4）×2 天 = 8 列；target: day1 s0(2),s1(7)；day2 s1(4) → 3 個
    assert out["n"] == 8 and out["n_target"] == 3
    assert out["p_target_le_3d"] == pytest.approx(1 / 8) and out["p_target_le_5d"] == pytest.approx(2 / 8)
    assert out["p_target_le_10d"] == pytest.approx(3 / 8) and out["median_time_to_target"] == pytest.approx(4.0)
    assert out["diagnostic_only"] is True


def test_timing_no_targets_is_null_median():
    df = _frame(); df["target"] = 0.0; df["event_type"] = TIMEOUT; df["target_first_hit_day"] = np.nan
    out = dfz.timing(df, k=5)
    assert out["n_target"] == 0 and out["median_time_to_target"] is None and out["p_target_le_10d"] == 0.0
