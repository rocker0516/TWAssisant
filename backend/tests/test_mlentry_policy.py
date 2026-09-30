"""B6–B8：ATR 條件式診斷、聯合 Gate 百分位、四種 ranking 與 Dynamic Top-K。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.mlentry.evaluation import volatility_control as vc
from app.mlentry.recommendation.gate import GATE_FAIL, GateConfig, apply_gate
from app.mlentry.recommendation.ranking import RankingConfig, rank_and_select, score


def _frame(n_days=40, n=50, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(n_days):
        atr = rng.uniform(0.01, 0.08, n)
        edge = rng.normal(size=n)                          # 波動度以外的方向訊號
        p_t = np.clip(0.05 + 3 * atr + 0.05 * edge, 0.01, 0.9)
        p_s = np.clip(0.10 + 5 * atr - 0.05 * edge, 0.01, 0.95)
        tgt = (rng.uniform(size=n) < p_t).astype(float)
        stp = ((rng.uniform(size=n) < p_s) & (tgt == 0)).astype(float)
        rows.append(pd.DataFrame({"signal_date": f"d{d:03d}", "fold": f"dev_{d % 2:02d}", "atr_pct": atr,
                                  "p_target_10d": p_t, "p_stop_10d": p_s, "p_target_5d": p_t * 0.6,
                                  "p_target_3d": p_t * 0.3, "p_stop_5d": p_s * 0.7, "pred_mfe_10d": atr * 2,
                                  "target": tgt, "stop": stp, "event_type": np.where(tgt == 1, 1, np.where(stp == 1, 2, 4)),
                                  "return_10d": rng.normal(0, 0.05, n), "mfe_10d": atr * 2, "mae_10d": -atr}))
    return pd.concat(rows, ignore_index=True)


def test_gate_percentile_joint_and_reasons():
    df = _frame()
    ok, fail = apply_gate(df, GateConfig(0.90, 0.40))
    assert ok.sum() > 0 and ok.sum() < 0.1 * len(df)
    top = df.groupby("signal_date")["p_target_10d"].rank(pct=True) >= 0.90
    assert (top[ok]).all()
    assert (fail[~ok] > 0).all() and ((fail & GATE_FAIL["ALPHA"]) > 0).sum() > 0
    ok2, fail2 = apply_gate(df.assign(p_executable=0.5), GateConfig(0.90, 0.40, theta_exec=0.9))
    assert ok2.sum() == 0 and ((fail2 & GATE_FAIL["EXECUTION"]) > 0).all()
    assert GateConfig(0.9, 0.4).version != GateConfig(0.9, 0.5).version


def test_ranking_methods_and_dynamic_topk():
    df = _frame()
    ok, _ = apply_gate(df, GateConfig(0.80, 0.60))
    for m in ("A", "B", "C", "D"):
        r = rank_and_select(df, ok, RankingConfig(m, k_max=3))
        per_day = r.loc[r["recommended"]].groupby(df["signal_date"]).size()
        assert per_day.max() <= 3 and r["rank"].notna().sum() == ok.sum()
        assert r.loc[r["recommended"]].index.isin(df.index[ok]).all()
        assert (r.loc[r["recommended"], "rank"] <= 3).all()
    sc = score(df, RankingConfig("C"))
    assert sc.between(-1, 1).all()
    # 不足 K 檔就少列：把 gate 收到極緊
    ok3, _ = apply_gate(df, GateConfig(0.99, 0.05))
    r3 = rank_and_select(df, ok3, RankingConfig("B", k_max=5))
    assert r3["recommended"].sum() == ok3.sum() < 5 * df["signal_date"].nunique()


def test_atr_conditional_lift_detects_direction_beyond_volatility():
    df = _frame(n_days=200, n=100)
    ct = vc.atr_conditional_lift(df, "p_target_10d", "target", top=True)
    assert ct.iloc[-1]["atr_decile"] == "all" and ct.iloc[-1]["ratio"] > 1.0
    cs = vc.atr_conditional_lift(df, "p_stop_10d", "stop", top=False)
    assert cs.iloc[-1]["ratio"] < 1.0
    noise = df.assign(p_target_10d=np.random.default_rng(1).uniform(size=len(df)))
    cn = vc.atr_conditional_lift(noise, "p_target_10d", "target", top=True)
    assert abs(cn.iloc[-1]["ratio"] - 1.0) < 0.15


def test_benchmark_table_deltas_vs_atr():
    df = _frame()
    sels = {"ATR Top-K": vc.daily_topk(df, "atr_pct", 5),
            "rank B": rank_and_select(df, pd.Series(True, index=df.index), RankingConfig("B", k_max=5))["recommended"]}
    t = vc.benchmark_table(df, sels)
    assert t.loc["ATR Top-K", "d_target_rate_vs_atr"] == 0.0
    assert {"target_lift", "stop_ratio", "worst_fold_lift", "positive_fold_ratio", "d_mean_ret10_vs_atr"} <= set(t.columns)
    assert t.loc["rank B", "n"] == 5 * df["signal_date"].nunique()
