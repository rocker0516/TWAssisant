"""§8.1 Price / Return（raw；只向後看）。"""

from __future__ import annotations

import pandas as pd

from .context import FeatureContext

LOOKBACK = 20


def build(ctx: FeatureContext) -> dict[str, pd.DataFrame]:
    p = ctx.prices
    c, o, h, l = p["close"], p["open"], p["high"], p["low"]
    prev_close = c.shift(1)
    rng = (h - l)
    out = {f"ret_{k}d": c / c.shift(k) - 1 for k in (1, 3, 5, 10, 20)}
    out["gap_open"] = o / prev_close - 1
    out["close_location"] = ((c - l) / rng).where(rng > 0, 0.5)
    hi20 = h.rolling(20, min_periods=10).max()
    lo20 = l.rolling(20, min_periods=10).min()
    out["distance_from_20d_high"] = c / hi20 - 1
    out["distance_from_20d_low"] = c / lo20 - 1
    return out
