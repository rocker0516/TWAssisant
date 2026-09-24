"""20 交易日超額報酬標籤：KPI = 20日內累積超額曾達 +X%。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def build_excess_labels(prices: pd.DataFrame, market: pd.Series, horizon: int = 20) -> pd.DataFrame:
    market = market.sort_index()
    rows: list[pd.DataFrame] = []
    for sid, g in prices.groupby("stock_id", sort=False):
        g = g.sort_values("date").reset_index(drop=True)
        mkt = market.reindex(g["date"]).to_numpy(dtype=float)
        px = g["close"].to_numpy(dtype=float)
        n = len(g)
        if n <= horizon:
            continue
        m = n - horizon  # 有完整未來窗的天數
        exc = np.full(m, -np.inf)
        for k in range(1, horizon + 1):
            # 第 T 天、往後第 k 根的累積超額（%）
            e = (px[k:k + m] / px[:m] - mkt[k:k + m] / mkt[:m]) * 100
            exc = np.maximum(exc, e)
        rows.append(pd.DataFrame({"stock_id": sid, "date": g["date"].iloc[:m], "exc_mfe20": exc}))
    if not rows:
        return pd.DataFrame(columns=["stock_id", "date", "exc_mfe20"])
    return pd.concat(rows, ignore_index=True)


def scan_thresholds(labels: pd.DataFrame, grid: tuple = (6, 8, 10, 12)) -> dict:
    base = {int(x): float((labels["exc_mfe20"] >= x).mean()) for x in grid}
    min_dist = min(abs(base[int(x)] - 0.05) for x in grid)
    candidates = [x for x in grid if abs(base[int(x)] - 0.05) == min_dist]
    # 優先選基率非零的候選，並在其中選最大值
    non_zero = [x for x in candidates if base[int(x)] > 0]
    chosen = max(non_zero) if non_zero else min(candidates)
    return {"base_rates": base, "chosen_x": int(chosen)}
