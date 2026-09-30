"""F1 Relative / Interaction 族（feature challenger round 1）。

診斷：同樣波動度下哪一支較偏上行。故加相對類股／市場的 context 與交互項，不加 TA 變體。
依賴 base（price/volume/volatility/regime/event 已算好的矩陣），只向後看。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .context import FeatureContext

LOOKBACK = 61

INPUTS = ("ret_5d", "ret_20d", "realized_vol_20d", "turnover_20d_mean", "turnover_change",
          "stock_excess_return_vs_market", "stock_excess_return_vs_industry", "industry_ret_5d",
          "industry_ret_20d", "breadth_ma20", "market_ret_5d")


def _sector_groups(x: pd.DataFrame, sector: pd.Series):
    sec = sector.reindex(x.columns)
    for sid, cols in x.columns.groupby(sec).items():
        if pd.isna(sid) or len(cols) == 0:
            continue
        yield sid, list(cols)


def _within_sector_rank(x: pd.DataFrame, sector: pd.Series, eligible: pd.DataFrame) -> pd.DataFrame:
    xe = x.where(eligible)
    out = pd.DataFrame(np.nan, index=x.index, columns=x.columns)
    for _, cols in _sector_groups(x, sector):
        out[cols] = xe[cols].rank(axis=1, pct=True, method="average")
    return out


def _sector_stat(x: pd.DataFrame, sector: pd.Series, eligible: pd.DataFrame, how: str) -> pd.DataFrame:
    xe = x.where(eligible)
    out = pd.DataFrame(np.nan, index=x.index, columns=x.columns)
    for _, cols in _sector_groups(x, sector):
        s = xe[cols].median(axis=1) if how == "median" else xe[cols].mean(axis=1)
        out[cols] = np.repeat(s.to_numpy()[:, None], len(cols), axis=1)
    return out


def build(ctx: FeatureContext, base: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    c = ctx.prices["close"]
    sec, elig = ctx.sector_map, ctx.eligible
    ma20 = c.rolling(20, min_periods=10).mean()
    above = (c > ma20).astype(float).where(c.notna() & ma20.notna() & elig)
    ret60 = c / c.shift(60) - 1
    ind_vol = _sector_stat(base["realized_vol_20d"], sec, elig, "median")
    ind_breadth = _sector_stat(above, sec, elig, "mean")
    ind_mom60 = _sector_stat(ret60, sec, elig, "mean")
    exm, exi = base["stock_excess_return_vs_market"], base["stock_excess_return_vs_industry"]
    return {
        "rel_ret_5d_vs_industry": base["ret_5d"] - base["industry_ret_5d"],
        "rel_vol_vs_industry": base["realized_vol_20d"] / ind_vol.replace(0, np.nan),
        "turnover_rank_in_industry": _within_sector_rank(base["turnover_20d_mean"], sec, elig),
        "ret_20d_rank_in_industry": _within_sector_rank(base["ret_20d"], sec, elig),
        "industry_breadth_ma20": ind_breadth,
        "industry_momentum_60d": ind_mom60,
        "x_strength_market_breadth": exm * base["breadth_ma20"],
        "x_strength_industry_breadth": exi * ind_breadth,
        "x_vol_breadth": base["realized_vol_20d"] * base["breadth_ma20"],
        "x_turnover_accel_rel_strength": base["turnover_change"] * exm,
        "x_rel_ret5_market_ret5": (base["ret_5d"] - base["market_ret_5d"]) * base["market_ret_5d"],
    }
