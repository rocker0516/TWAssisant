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
