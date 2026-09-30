"""§18 Recommendation Evaluation（B9）：@K 指標、coverage、fold 穩定性、交易日區塊 bootstrap、配對差。

所有 @K 統計以「日層級聚合」為基礎：每日 (n, sum_target, sum_stop, sum_timeout, sum_ret, sum_net, sum_mfe, sum_mae)，
bootstrap 以交易日區塊重抽（不做 row bootstrap），各對照組在同一組重抽日上計算 → 配對差。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

AGG_COLS = ("n", "target", "stop", "timeout", "ret10", "net10", "mfe", "mae")


def daily_aggregates(df: pd.DataFrame, mask: pd.Series, cost_rt: float) -> pd.DataFrame:
    """mask 為 True 的列 → 每日聚合（缺日補 0）。df 需含 signal_date, target, stop, event_type, return_10d, mfe_10d, mae_10d。"""
    s = df.loc[mask]
    g = pd.DataFrame({
        "n": s.groupby("signal_date").size(),
        "target": s.groupby("signal_date")["target"].sum(),
        "stop": s.groupby("signal_date")["stop"].sum(),
        "timeout": (s["event_type"] == 4).groupby(s["signal_date"]).sum(),
        "ret10": s.groupby("signal_date")["return_10d"].sum(),
        "net10": (s["return_10d"] - cost_rt).groupby(s["signal_date"]).sum(),
        "mfe": s.groupby("signal_date")["mfe_10d"].sum(),
        "mae": s.groupby("signal_date")["mae_10d"].sum(),
    })
    days = pd.Index(sorted(df["signal_date"].unique()), name="signal_date")
    return g.reindex(days).fillna(0.0)


def _rates(a: np.ndarray) -> dict:
    """a: (days, 8) 聚合陣列 → row-weighted 比率。"""
    n = a[:, 0].sum()
    if n == 0:
        return {k: np.nan for k in ("target_rate", "stop_rate", "timeout_rate", "mean_ret10", "mean_net10", "mean_mfe", "mean_mae")}
    return {"target_rate": a[:, 1].sum() / n, "stop_rate": a[:, 2].sum() / n, "timeout_rate": a[:, 3].sum() / n,
            "mean_ret10": a[:, 4].sum() / n, "mean_net10": a[:, 5].sum() / n, "mean_mfe": a[:, 6].sum() / n,
            "mean_mae": a[:, 7].sum() / n}


def point_estimates(sel: pd.DataFrame, market: pd.DataFrame, df_sel: pd.DataFrame) -> dict:
    r = _rates(sel.to_numpy()); m = _rates(market.to_numpy())
    out = {**r, "market_target_rate": m["target_rate"], "market_stop_rate": m["stop_rate"],
           "target_lift": r["target_rate"] / m["target_rate"], "stop_ratio": r["stop_rate"] / m["stop_rate"],
           "n": int(sel["n"].sum()), "days_with_rec": int((sel["n"] > 0).sum()), "days": int(len(sel)),
           "coverage": float((sel["n"] > 0).mean()), "no_trade_pct": float((sel["n"] == 0).mean()),
           "rec_per_day_mean": float(sel["n"].mean()),
           "median_mfe": float(df_sel["mfe_10d"].median()) if len(df_sel) else np.nan,
           "median_mae": float(df_sel["mae_10d"].median()) if len(df_sel) else np.nan}
    return out


def block_bootstrap(sel: pd.DataFrame, market: pd.DataFrame, ref: pd.DataFrame | None, block: int,
                    n_resamples: int, ci: float = 0.95, seed: int = 0) -> dict:
    """交易日區塊 bootstrap（circular）。回傳 net10 / target_lift / stop_ratio 的 CI，
    以及相對 ref（例如 ATR Top-K）的配對差 CI。"""
    rng = np.random.default_rng(seed)
    S, M = sel.to_numpy(dtype=float), market.to_numpy(dtype=float)
    R = ref.to_numpy(dtype=float) if ref is not None else None
    T = len(S)
    n_blocks = int(np.ceil(T / block))
    stats = {"net10": [], "target_lift": [], "stop_ratio": [], "target_rate": [], "stop_rate": []}
    if R is not None:
        stats.update({"d_net10": [], "d_target_rate": [], "d_stop_rate": []})
    for _ in range(n_resamples):
        starts = rng.integers(0, T, n_blocks)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel() % T
        idx = idx[:T]
        s, m = _rates(S[idx]), _rates(M[idx])
        stats["net10"].append(s["mean_net10"]); stats["target_rate"].append(s["target_rate"]); stats["stop_rate"].append(s["stop_rate"])
        stats["target_lift"].append(s["target_rate"] / m["target_rate"]); stats["stop_ratio"].append(s["stop_rate"] / m["stop_rate"])
        if R is not None:
            r = _rates(R[idx])
            stats["d_net10"].append(s["mean_net10"] - r["mean_net10"])
            stats["d_target_rate"].append(s["target_rate"] - r["target_rate"])
            stats["d_stop_rate"].append(s["stop_rate"] - r["stop_rate"])
    lo, hi = (1 - ci) / 2, 1 - (1 - ci) / 2
    out = {}
    for k, v in stats.items():
        v = np.array(v, dtype=float)
        out[k] = {"lo": float(np.nanquantile(v, lo)), "hi": float(np.nanquantile(v, hi)), "mean": float(np.nanmean(v))}
    out["block"] = block; out["n_resamples"] = n_resamples
    return out


def fold_stats(df: pd.DataFrame, mask: pd.Series) -> dict:
    lifts, ratios = [], []
    for _, g in df.groupby("fold"):
        m = mask.loc[g.index]
        if m.sum() == 0:
            lifts.append(np.nan); ratios.append(np.nan); continue
        lifts.append(g.loc[m, "target"].mean() / g["target"].mean())
        ratios.append(g.loc[m, "stop"].mean() / g["stop"].mean())
    l = np.array(lifts, dtype=float); r = np.array(ratios, dtype=float)
    return {"fold_lifts": [round(x, 3) for x in l], "fold_mean_lift": float(np.nanmean(l)), "worst_fold_lift": float(np.nanmin(l)),
            "positive_fold_ratio": float(np.mean(l > 1)), "fold_stop_ratios": [round(x, 3) for x in r],
            "worst_fold_stop_ratio": float(np.nanmax(r))}


def promotion_check(pe: dict, fs: dict, contract: dict) -> dict:
    c = contract["final_holdout_eligibility"]
    checks = {"target_lift_at_5": (pe["target_lift"], ">=", c["target_lift_at_5_min"]),
              "stop_ratio_at_5": (pe["stop_ratio"], "<=", c["stop_ratio_at_5_max"]),
              "worst_fold_lift_at_5": (fs["worst_fold_lift"], ">", c["worst_fold_lift_at_5_min"])}
    if c.get("coverage_min") is not None:
        checks["coverage"] = (pe["coverage"], ">=", c["coverage_min"])
    res = {}
    for k, (v, op, t) in checks.items():
        ok = {">=": v >= t, "<=": v <= t, ">": v > t}[op]
        res[k] = {"value": round(float(v), 4), "op": op, "threshold": t, "pass": bool(ok)}
    res["eligible"] = all(r["pass"] for r in res.values() if isinstance(r, dict))
    return res
