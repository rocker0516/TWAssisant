"""§8.4 Cross-sectional：同日 U_t 內百分位（(0,1]，1 = 最大）與市場相對表示。

依賴已算好的 raw 特徵（base），只在 eligible 格內排名；非 U_t 格為 NaN。
"""

from __future__ import annotations

import pandas as pd

from .context import FeatureContext

LOOKBACK = 0

RANKED = ("ret_1d", "ret_5d", "ret_20d", "turnover_20d_mean", "realized_vol_20d", "volume_ratio_20d")
MARKET_RELATIVE = ("ret_5d", "ret_20d", "realized_vol_20d", "turnover_20d_mean")


def pct_rank_in_universe(x: pd.DataFrame, eligible: pd.DataFrame) -> pd.DataFrame:
    return x.where(eligible).rank(axis=1, pct=True, method="average")


def build(ctx: FeatureContext, base: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for name in RANKED:
        out[f"{name}_pct_rank"] = pct_rank_in_universe(base[name], ctx.eligible)
    for name in MARKET_RELATIVE:
        x = base[name].where(ctx.eligible)
        med = x.median(axis=1)
        if name.startswith("ret_"):
            out[f"{name}_mktrel"] = x.sub(med, axis=0)
        else:
            out[f"{name}_mktrel"] = x.div(med.replace(0, float("nan")), axis=0)
    return out
