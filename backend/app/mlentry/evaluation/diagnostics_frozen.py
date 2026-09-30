"""§18 唯讀 evaluation diagnostics（Spec B）——只算 Frozen dev OOF，描述性，不得回頭改 Gate／Feature／Policy。

每個輸出區塊附 DIAG_FLAGS；樣本 n < MIN_N 的格子為 None。輸入 df 已由腳本合併好（OOF＋policy per_row＋dev outcomes＋
regime 特徵＋sector／mcap）。本檔不讀檔、不寫檔、不讀 holdout。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .policy_metrics import daily_aggregates
from .volatility_control import daily_topk

DIAG_FLAGS = {"diagnostic_only": True, "not_used_for_policy": True}
MIN_N = 30
HORIZONS = (3, 5, 10)


def _f(x) -> float | None:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(x) else x


def topk_mask(df: pd.DataFrame, k: int) -> pd.Series:
    """gate_pass 且 recommendation_score 非 NaN 的列，依分數每日 Top-K。"""
    pool = df["gate_pass"].astype(bool) & df["recommendation_score"].notna()
    sub = df.loc[pool, ["signal_date", "recommendation_score"]]
    m = pd.Series(False, index=df.index)
    if len(sub):
        m.loc[sub.index] = daily_topk(sub, "recommendation_score", k).to_numpy()
    return m


def _weighted_block(df: pd.DataFrame, mask: pd.Series) -> dict:
    sel, mkt = daily_aggregates(df, mask, 0.0), daily_aggregates(df, pd.Series(True, index=df.index), 0.0)
    n = float(sel["n"].sum())
    rw = {"target_rate": None, "market_target_rate": _f(mkt["target"].sum() / mkt["n"].sum()),
          "target_lift": None, "stop_rate": None, "market_stop_rate": _f(mkt["stop"].sum() / mkt["n"].sum()),
          "stop_ratio": None, "n": int(n), "days_with_rec": int((sel["n"] > 0).sum())}
    if n > 0:
        rw["target_rate"] = _f(sel["target"].sum() / n); rw["stop_rate"] = _f(sel["stop"].sum() / n)
        rw["target_lift"] = _f(rw["target_rate"] / rw["market_target_rate"]) if rw["market_target_rate"] else None
        rw["stop_ratio"] = _f(rw["stop_rate"] / rw["market_stop_rate"]) if rw["market_stop_rate"] else None
    days = sel.index[sel["n"] > 0]
    dw = {"target_rate": None, "market_target_rate": None, "target_lift": None, "stop_rate": None,
          "market_stop_rate": None, "stop_ratio": None, "n": int(n), "days_with_rec": int(len(days))}
    if len(days):
        s, m = sel.loc[days], mkt.loc[days]
        tr, mtr = (s["target"] / s["n"]).mean(), (m["target"] / m["n"]).mean()
        sr, msr = (s["stop"] / s["n"]).mean(), (m["stop"] / m["n"]).mean()
        dw.update({"target_rate": _f(tr), "market_target_rate": _f(mtr), "target_lift": _f(tr / mtr) if mtr else None,
                   "stop_rate": _f(sr), "market_stop_rate": _f(msr), "stop_ratio": _f(sr / msr) if msr else None})
    return {"row_weighted": rw, "day_weighted": dw}


def lift_at_k(df: pd.DataFrame, ks: tuple[int, ...] = (1, 3, 5, 10)) -> dict[str, dict]:
    return {str(k): {**_weighted_block(df, topk_mask(df, k)), **DIAG_FLAGS} for k in ks}


def timing(df: pd.DataFrame, k: int = 5) -> dict:
    rec = df.loc[topk_mask(df, k)]
    n = int(len(rec))
    hit = rec.loc[rec["target"] == 1, "target_first_hit_day"].astype(float)
    hit = hit[np.isfinite(hit)]
    out = {"k": k, "n": n, "n_target": int(len(hit)),
           "median_time_to_target": _f(hit.median()) if len(hit) else None, **DIAG_FLAGS}
    for h in HORIZONS:
        out[f"p_target_le_{h}d"] = _f((hit <= h).sum() / n) if n else None
    return out


def ranking_diagnostics(df: pd.DataFrame, k: int = 5) -> dict:
    """Precision@K（＝Top-K target rate）、Recall@K（每日）、NDCG@K（binary relevance）、每日 rank IC（gate_pass ≥ 5 列）。"""
    pool = df.loc[df["gate_pass"].astype(bool) & df["recommendation_score"].notna()]
    top = topk_mask(df, k)
    precision = _f(df.loc[top, "target"].mean()) if top.any() else None
    recalls, ndcgs, ics = [], [], []
    for _, g in pool.groupby("signal_date"):
        g = g.sort_values("recommendation_score", ascending=False)
        rel = g["target"].to_numpy(dtype=float)
        n_t = int(rel.sum())
        if n_t > 0:
            recalls.append(rel[:k].sum() / n_t)
            disc = 1 / np.log2(np.arange(2, min(k, len(rel)) + 2))
            dcg = float((rel[:k] * disc[: len(rel[:k])]).sum())
            idcg = float(disc[: min(k, n_t)].sum())
            ndcgs.append(dcg / idcg if idcg > 0 else 0.0)
        gg = g.dropna(subset=["return_10d", "recommendation_score"])
        if len(gg) >= 5:
            r = gg["recommendation_score"].rank().corr(gg["return_10d"].rank())
            if np.isfinite(r):
                ics.append(float(r))
    ic = np.array(ics, dtype=float)
    return {"k": k, "n": int(top.sum()), "days": int(pool["signal_date"].nunique()),
            "precision_at_k": precision,
            "recall_at_k": _f(np.mean(recalls)) if recalls else None,
            "ndcg_at_k": _f(np.mean(ndcgs)) if ndcgs else None,
            "ic": {"mean": _f(ic.mean()) if len(ic) else None, "std": _f(ic.std(ddof=0)) if len(ic) else None,
                   "positive_share": _f((ic > 0).mean()) if len(ic) else None, "days": int(len(ic))},
            **DIAG_FLAGS}


def _group_cell(df: pd.DataFrame, gmask: pd.Series, top: pd.Series) -> dict:
    sel = gmask & top
    n = int(sel.sum())
    cell = {"lift_at_5": None, "stop_ratio_at_5": None, "n": n, "days": int(df.loc[gmask, "signal_date"].nunique())}
    if n < MIN_N:
        return cell
    g = df.loc[gmask]
    bt, bs = g["target"].mean(), g["stop"].mean()
    s = df.loc[sel]
    cell["lift_at_5"] = _f(s["target"].mean() / bt) if bt > 0 else None
    cell["stop_ratio_at_5"] = _f(s["stop"].mean() / bs) if bs > 0 else None
    return cell


def _quantile_groups(x: pd.Series, labels: tuple[str, ...]) -> tuple[pd.Series, list[float]]:
    """依 dev 分位切 len(labels) 組；cuts 為內部切點（len(labels)-1 個）。NaN → 無組。"""
    qs = np.linspace(0, 1, len(labels) + 1)[1:-1]
    cuts = [float(v) for v in np.nanquantile(x.to_numpy(dtype=float), qs)]
    if any(b <= a for a, b in zip(cuts, cuts[1:])):
        # 切點重合（大量 tie／常數欄）＝分組退化：不硬分，全部無組（各格 n=0 → None），cuts 仍照實輸出
        return pd.Series([None] * len(x), index=x.index, dtype=object), cuts
    bins = [-np.inf, *cuts, np.inf]
    grp = pd.cut(x, bins=bins, labels=list(labels), include_lowest=True).astype(object)
    return grp, cuts


def regime_breakdown(df: pd.DataFrame, k: int = 5) -> dict:
    top = topk_mask(df, k)
    out: dict = {"mcap_basis": "current_company_profile", **DIAG_FLAGS}
    specs = (("market", "market_ret_20d", ("bear", "neutral", "bull")),
             ("volatility", "market_volatility", ("low", "high")),
             ("breadth", "breadth_ma20", ("low", "high")),
             ("mcap", "mcap", ("small", "mid", "large")))
    for key, col, labels in specs:
        if col not in df.columns or df[col].notna().sum() == 0:
            out[key] = {"groups": {lab: {"lift_at_5": None, "stop_ratio_at_5": None, "n": 0, "days": 0} for lab in labels},
                        "cuts": None, "column": col}
            continue
        grp, cuts = _quantile_groups(df[col], labels)
        out[key] = {"groups": {lab: _group_cell(df, grp == lab, top) for lab in labels}, "cuts": cuts, "column": col}
    ind: dict = {}
    if "sector_id" in df.columns:
        for sid, gmask in ((s, df["sector_id"] == s) for s in sorted(df["sector_id"].dropna().unique())):
            cell = _group_cell(df, gmask, top)
            if cell["n"] >= MIN_N:
                ind[str(int(sid))] = cell
    out["industry"] = {"groups": dict(sorted(ind.items(), key=lambda kv: -kv[1]["n"])), "cuts": None, "column": "sector_id"}
    return out


def build_diagnostics(df: pd.DataFrame, policy_name: str, dataset_version: str, k: int = 5) -> dict:
    from datetime import datetime, timezone
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "policy_name": policy_name,
            "dataset_version": dataset_version, "k": k, "n_eval_rows": int(len(df)), "n_days": int(df["signal_date"].nunique()),
            "lift_at_k": lift_at_k(df), "timing": timing(df, k), "ranking": ranking_diagnostics(df, k),
            "regime": regime_breakdown(df, k), **DIAG_FLAGS}
