"""§8.6 Event / Microstructure（raw；bool 以 0/1 float 表示）。

注意股、處置股在這裡是 feature（§7.4），不是 universe 條件。
漲跌停以 ctx.limits（costs.up_limit / down_limit 單一來源）判定。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .context import FeatureContext

LOOKBACK = 21
_TOL = 1e-6


def _streak(cond: pd.DataFrame) -> pd.DataFrame:
    """連續 True 天數（含今日）；NaN 視為中斷。"""
    arr = cond.fillna(False).to_numpy()
    out = np.zeros(arr.shape, dtype=np.int32)
    run = np.zeros(arr.shape[1], dtype=np.int32)
    for i in range(arr.shape[0]):
        run = np.where(arr[i], run + 1, 0)
        out[i] = run
    return pd.DataFrame(out, index=cond.index, columns=cond.columns, dtype=float)


def build(ctx: FeatureContext, large_gap_threshold: float = 0.05) -> dict[str, pd.DataFrame]:
    p = ctx.prices
    c, o, h, l = p["close"], p["open"], p["high"], p["low"]
    up, dn = ctx.limits["up"], ctx.limits["down"]
    ret1 = c / c.shift(1) - 1
    lu = (c >= up * (1 - _TOL)).where(c.notna() & up.notna())
    ld = (c <= dn * (1 + _TOL)).where(c.notna() & dn.notna())
    gap = o / c.shift(1) - 1
    ma20 = c.rolling(20, min_periods=10).mean()
    above = (c > ma20).where(c.notna() & ma20.notna() & ctx.eligible)
    breadth = above.mean(axis=1)
    return {
        "is_attention_stock": ctx.events["attention"].astype(float),
        "is_disposition_stock": ctx.events["disposition"].astype(float),
        "limit_up_today": lu.astype(float),
        "limit_down_today": ld.astype(float),
        "limit_up_count_20d": lu.astype(float).rolling(20, min_periods=10).sum(),
        "limit_down_count_20d": ld.astype(float).rolling(20, min_periods=10).sum(),
        "large_gap": (gap.abs() >= large_gap_threshold).astype(float).where(gap.notna()),
        "consecutive_up_days": _streak(ret1 > 0),
        "consecutive_down_days": _streak(ret1 < 0),
        "dist_limit_up": (up - c) / c,
        "breadth_ma20": pd.DataFrame(np.repeat(breadth.to_numpy()[:, None], c.shape[1], axis=1),
                                     index=c.index, columns=c.columns),
    }
