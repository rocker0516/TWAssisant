"""B9：policy 套用（vn gate / ranking / NO_TRADE）、日聚合、區塊 bootstrap、promotion check。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.mlentry.config import load_yaml
from app.mlentry.evaluation import policy_metrics as pm
from app.mlentry.recommendation.policy import PolicyConfig, apply_policy, load_policy, vn_percentile


def _df(n_days=60, n=80, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(n_days):
        atr = rng.uniform(0.01, 0.08, n)
        edge = rng.normal(size=n)
        p_t = np.clip(0.05 + 3 * atr + 0.06 * edge, 0.01, 0.9)
        p_s = np.clip(0.10 + 5 * atr - 0.06 * edge, 0.01, 0.95)
        tgt = (rng.uniform(size=n) < p_t).astype(float)
        stp = ((rng.uniform(size=n) < p_s) & (tgt == 0)).astype(float)
        rows.append(pd.DataFrame({"signal_date": f"d{d:03d}", "fold": f"dev_{d % 2:02d}", "atr_pct": atr,
                                  "p_target_10d": p_t, "p_stop_10d": p_s, "target": tgt, "stop": stp,
                                  "event_type": np.where(tgt == 1, 1, np.where(stp == 1, 2, 4)),
                                  "return_10d": np.where(tgt == 1, 0.1, np.where(stp == 1, -0.05, rng.normal(0, 0.02, n))),
                                  "mfe_10d": atr * 2, "mae_10d": -atr}))
    return pd.concat(rows, ignore_index=True)


def test_policy_yaml_and_version():
    cfg = load_policy("policy_baseline_v1")
    assert cfg.name == "policy_baseline_v1" and cfg.k_max == 5 and cfg.cost_rt == pytest.approx(0.00585)
    assert cfg.gate["theta_alpha_pct"] == 0.95 and cfg.gate["theta_risk_pct"] == 0.20
    assert cfg.version.startswith("p_")
    other = PolicyConfig("x", {**cfg.raw, "ranking": {**cfg.raw["ranking"], "k_max": 3}})
    assert other.version != cfg.version
    contract = load_yaml("promotion")
    assert contract["final_holdout_eligibility"]["target_lift_at_5_min"] == 1.5
    assert contract["bootstrap"]["method"] == "trading_day_block"


def test_vn_percentile_is_within_day_and_bucket():
    df = _df(n_days=5, n=100)
    p = vn_percentile(df, "p_target_10d", "atr_pct", 10)
    assert p.between(0, 1).all()
    # 每個 (日, 桶) 內最大者為 1
    vol_r = df.groupby("signal_date")["atr_pct"].rank(pct=True, method="first")
    b = np.clip((vol_r * 10).astype(int), 0, 9)
    assert (p.groupby([df["signal_date"], b]).max() == 1.0).all()


def test_apply_policy_outputs_and_no_trade():
    df = _df()
    cfg = load_policy("policy_baseline_v1")
    rows, day = apply_policy(df, cfg)
    assert set(rows.columns) >= {"p_target_vn", "p_stop_vn", "gate_pass", "gate_failure_reason", "recommendation_score", "rank", "recommended"}
    assert rows["recommended"].sum() <= 5 * df["signal_date"].nunique()
    assert (rows.loc[rows["recommended"], "p_target_vn"] >= 0.95).all() and (rows.loc[rows["recommended"], "p_stop_vn"] <= 0.20).all()
    assert (rows.loc[~rows["gate_pass"], "gate_failure_reason"] > 0).all()
    assert len(day) == df["signal_date"].nunique() and {"qualified_count", "recommendation_count", "no_trade", "no_trade_reason"} <= set(day.columns)
    # 極緊 gate → NO_TRADE 日出現且理由正確
    tight = PolicyConfig("t", {**cfg.raw, "gate": {**cfg.raw["gate"], "theta_alpha_pct": 0.999, "theta_risk_pct": 0.001}})
    _, day2 = apply_policy(df, tight)
    assert day2["no_trade"].any() and (day2.loc[day2["no_trade"], "no_trade_reason"] == "POLICY_NO_CANDIDATE").all()


def test_daily_aggregates_bootstrap_and_promotion():
    df = _df(n_days=120)
    cfg = load_policy("policy_baseline_v1")
    rows, _ = apply_policy(df, cfg)
    df = pd.concat([df, rows], axis=1)
    market = pm.daily_aggregates(df, pd.Series(True, index=df.index), cfg.cost_rt)
    pol = pm.daily_aggregates(df, df["recommended"], cfg.cost_rt)
    assert len(market) == len(pol) == 120 and market["n"].sum() == len(df)
    pe = pm.point_estimates(pol, market, df.loc[df["recommended"]])
    assert pe["n"] == int(df["recommended"].sum()) and 0 < pe["coverage"] <= 1
    assert pe["mean_net10"] == pytest.approx(pe["mean_ret10"] - cfg.cost_rt)
    boot = pm.block_bootstrap(pol, market, None, block=20, n_resamples=200)
    assert boot["net10"]["lo"] <= boot["net10"]["mean"] <= boot["net10"]["hi"]
    assert "d_net10" not in boot
    boot2 = pm.block_bootstrap(pol, market, market, block=10, n_resamples=100)
    assert "d_net10" in boot2
    fs = pm.fold_stats(df, df["recommended"])
    assert len(fs["fold_lifts"]) == 2 and fs["worst_fold_lift"] == pytest.approx(min(fs["fold_lifts"]), abs=1e-3)
    contract = load_yaml("promotion")
    chk = pm.promotion_check(pe, fs, contract)
    assert set(chk) >= {"target_lift_at_5", "stop_ratio_at_5", "worst_fold_lift_at_5", "eligible"}
    contract2 = {"final_holdout_eligibility": {**contract["final_holdout_eligibility"], "coverage_min": 0.5}}
    assert "coverage" in pm.promotion_check(pe, fs, contract2)
