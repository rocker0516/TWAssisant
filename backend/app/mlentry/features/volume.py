"""§8.2 Volume / Liquidity（raw）。turnover_rate 以 amihud_20d 取代：issued_shares 無 PIT 保證。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .context import FeatureContext

LOOKBACK = 21


def build(ctx: FeatureContext) -> dict[str, pd.DataFrame]:
    p = ctx.prices
    t, v, c = p["turnover"], p["volume"], p["close"]
    t5 = t.rolling(5, min_periods=3).mean()
    t20 = t.rolling(20, min_periods=10).mean()
    v5 = v.rolling(5, min_periods=3).mean()
    v20 = v.rolling(20, min_periods=10).mean()
    ret1 = (c / c.shift(1) - 1).abs()
    illiq = (ret1 / t).replace([np.inf, -np.inf], np.nan)
    return {
        "turnover_1d": t,
        "turnover_5d_mean": t5,
        "turnover_20d_mean": t20,
        "volume_ratio_5d": v / v5,
        "volume_ratio_20d": v / v20,
        "turnover_change": t5 / t20 - 1,
        "amihud_20d": illiq.rolling(20, min_periods=10).mean() * 1e9,
    }
