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


def test_ranking_precision_recall_ndcg_ic():
    out = dfz.ranking_diagnostics(_frame(), k=2)
    # Top-2 = s0,s1：day1 target 2/2、day2 1/2 → precision 3/4
    assert out["precision_at_k"] == pytest.approx(0.75)
    # recall：day1 gate_pass 內 target = s0,s1（s4 非 gate_pass）→ 2/2；day2 = s1 → 1/1 → 平均 1.0
    assert out["recall_at_k"] == pytest.approx(1.0)
    # NDCG@2：day1 rel=[1,1] → DCG=1+1/log2(3)，IDCG 同 → 1；day2 rel=[0,1] → DCG=1/log2(3)，IDCG=1 → 0.6309；平均 0.8155
    assert out["ndcg_at_k"] == pytest.approx((1 + 1 / np.log2(3)) / 2, abs=1e-4)
    # IC：gate_pass 4 列，score 遞減；day1 ret=[.1,.1,-.05,0]、day2 ret=[-.05,.1,0,-.05]；Spearman 手算（tie 平均秩）
    assert out["ic"]["days"] == 0                                       # 每日 gate_pass < 5 列 → 略過
    assert out["diagnostic_only"] is True and out["n"] == 4 and out["days"] == 2


def test_ranking_ic_perfect_and_reverse():
    rows = [{"signal_date": "d", "stock_id": f"s{i}", "gate_pass": True, "recommendation_score": i, "target": 0.0, "stop": 0.0,
             "event_type": TIMEOUT, "return_10d": i * 0.01, "mfe_10d": 0.0, "mae_10d": 0.0, "target_first_hit_day": np.nan}
            for i in range(6)]
    df = pd.DataFrame(rows)
    assert dfz.ranking_diagnostics(df, k=3)["ic"]["mean"] == pytest.approx(1.0)
    df["return_10d"] = -df["return_10d"]
    ic = dfz.ranking_diagnostics(df, k=3)["ic"]
    assert ic["mean"] == pytest.approx(-1.0) and ic["positive_share"] == 0.0 and ic["days"] == 1


def _regime_frame(n_days=40, per_day=20, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(n_days):
        mret = (d - n_days / 2) / n_days                      # 單調：前半負、後半正
        for i in range(per_day):
            score = rng.random()
            t = rng.random() < (0.5 if score > 0.7 else 0.2)
            rows.append({"signal_date": f"2026-{1 + d // 28:02d}-{1 + d % 28:02d}", "stock_id": f"s{i}", "gate_pass": True,
                         "recommendation_score": score, "target": float(t), "stop": float((not t) and rng.random() < 0.3),
                         "event_type": TARGET if t else TIMEOUT, "return_10d": 0.1 if t else 0.0, "mfe_10d": 0.05, "mae_10d": -0.02,
                         "target_first_hit_day": 3.0 if t else np.nan,
                         "market_ret_20d": mret, "market_volatility": 0.01 + 0.02 * (d % 2), "breadth_ma20": 0.3 + 0.4 * (d % 3 == 0),
                         "mcap": float(i + 1) * 1e9, "sector_id": float(i % 3)})
    return pd.DataFrame(rows)


def test_regime_breakdown_groups_cuts_and_min_n():
    df = _regime_frame()
    out = dfz.regime_breakdown(df, k=5)
    assert set(out) == {"market", "volatility", "breadth", "mcap", "industry", "mcap_basis", "diagnostic_only", "not_used_for_policy"}
    assert out["mcap_basis"] == "current_company_profile"
    mk = out["market"]
    assert set(mk["groups"]) == {"bear", "neutral", "bull"} and len(mk["cuts"]) == 2 and mk["cuts"][0] < mk["cuts"][1]
    assert all(g["n"] >= dfz.MIN_N for g in mk["groups"].values())
    assert all(g["lift_at_5"] is not None for g in mk["groups"].values())
    assert set(out["volatility"]["groups"]) == {"low", "high"} and set(out["breadth"]["groups"]) == {"low", "high"}
    assert set(out["mcap"]["groups"]) == {"small", "mid", "large"}
    ind = out["industry"]["groups"]
    assert set(ind) <= {"0", "1", "2"} and all(g["n"] >= dfz.MIN_N for g in ind.values())
    small = dfz.regime_breakdown(df[df["signal_date"] < "2026-01-04"], k=5)
    assert all(g["lift_at_5"] is None and g["n"] < dfz.MIN_N for g in small["market"]["groups"].values())
    assert small["industry"]["groups"] == {}


def test_build_diagnostics_shape():
    out = dfz.build_diagnostics(_regime_frame(), "policy_baseline_v1", "ds_x", k=5)
    assert {"generated_at", "policy_name", "dataset_version", "k", "n_eval_rows", "n_days", "lift_at_k", "timing", "ranking", "regime"} <= set(out)
    assert out["diagnostic_only"] is True and out["not_used_for_policy"] is True
    assert set(out["lift_at_k"]) == {"1", "3", "5", "10"} and out["n_days"] == 40
