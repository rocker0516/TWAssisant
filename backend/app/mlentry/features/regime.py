"""§8.5 Market / Industry Regime（raw；同日各股相同或依類股）。

industry_* 只用 U_t 內股票算類股均值；無類股者 NaN。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .context import FeatureContext

LOOKBACK = 21


def _broadcast(s: pd.Series, like: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(np.repeat(s.to_numpy(dtype=float)[:, None], like.shape[1], axis=1),
                        index=like.index, columns=like.columns)


def _industry_mean(x: pd.DataFrame, sector: pd.Series, eligible: pd.DataFrame) -> pd.DataFrame:
    """每格 = 同日同類股（U_t 內）x 的均值。"""
    sec = sector.reindex(x.columns)
    xe = x.where(eligible)
    out = pd.DataFrame(np.nan, index=x.index, columns=x.columns)
    for sid, cols in xe.columns.groupby(sec).items():
        if pd.isna(sid):
            continue
        m = xe[cols].mean(axis=1)
        out[cols] = _broadcast(m, xe[cols])
    return out


def build(ctx: FeatureContext) -> dict[str, pd.DataFrame]:
    c = ctx.prices["close"]
    mkt = ctx.market_close.reindex(c.index)
    mret = {k: mkt / mkt.shift(k) - 1 for k in (1, 5, 20)}
    mvol = (mkt / mkt.shift(1) - 1).rolling(20, min_periods=10).std()
    ret5 = c / c.shift(5) - 1
    ret20 = c / c.shift(20) - 1
    ind5 = _industry_mean(ret5, ctx.sector_map, ctx.eligible)
    ind20 = _industry_mean(ret20, ctx.sector_map, ctx.eligible)
    # industry_strength_rank：每日各類股 ret20 均值在類股間的百分位，再廣播回股票
    strength = ind20.rank(axis=1, pct=True, method="average")
    return {
        "market_ret_1d": _broadcast(mret[1], c),
        "market_ret_5d": _broadcast(mret[5], c),
        "market_ret_20d": _broadcast(mret[20], c),
        "market_volatility": _broadcast(mvol, c),
        "industry_ret_5d": ind5,
        "industry_ret_20d": ind20,
        "industry_strength_rank": strength,
        "stock_excess_return_vs_market": ret20.sub(mret[20], axis=0),
        "stock_excess_return_vs_industry": ret20 - ind20,
    }
