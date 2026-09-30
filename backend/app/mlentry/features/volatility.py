"""§8.3 Volatility / Path（raw）。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .context import FeatureContext

LOOKBACK = 21


def _true_range(h, l, prev_close) -> pd.DataFrame:
    tr = np.maximum.reduce([(h - l).to_numpy(), (h - prev_close).abs().to_numpy(),
                            (l - prev_close).abs().to_numpy()])
    return pd.DataFrame(tr, index=h.index, columns=h.columns)


def _max_drawdown(c: pd.DataFrame, w: int) -> pd.DataFrame:
    """窗內 close 相對其滾動高點的最深回撤（負值）。"""
    run_max = c.rolling(w, min_periods=max(2, w // 2)).max()
    dd = c / run_max - 1
    return dd.rolling(w, min_periods=max(2, w // 2)).min()


def build(ctx: FeatureContext) -> dict[str, pd.DataFrame]:
    p = ctx.prices
    c, h, l = p["close"], p["high"], p["low"]
    ret1 = c / c.shift(1) - 1
    prev_close = c.shift(1)
    out = {f"realized_vol_{w}d": ret1.rolling(w, min_periods=max(3, w // 2)).std()
           for w in (5, 10, 20)}
    out["atr_pct"] = _true_range(h, l, prev_close).rolling(14, min_periods=7).mean() / c
    out["intraday_range"] = (h - l) / c
    out["downside_vol"] = ret1.clip(upper=0).pow(2).rolling(20, min_periods=10).mean().pow(0.5)
    up = (ret1 > 0).astype(float).where(ret1.notna())
    dn = (ret1 < 0).astype(float).where(ret1.notna())
    out["positive_day_ratio"] = up.rolling(20, min_periods=10).mean()
    out["negative_day_ratio"] = dn.rolling(20, min_periods=10).mean()
    for w in (5, 10, 20):
        out[f"max_drawdown_{w}d"] = _max_drawdown(c, w)
    return out
