"""F2 Fundamentals 族（PIT 矩陣由 data/fundamentals.py 提供，feature 函式不讀 DB）。

只放可能影響橫斷面品質的少數欄位：營收成長／加速度、獲利、毛利趨勢、相對類股估值、營收驚喜。
缺值留 NaN（無資料或尚未可得）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .context import FeatureContext

LOOKBACK = 0


def _within_sector_rank(x: pd.DataFrame, sector: pd.Series, eligible: pd.DataFrame) -> pd.DataFrame:
    xe = x.where(eligible)
    out = pd.DataFrame(np.nan, index=x.index, columns=x.columns)
    sec = sector.reindex(x.columns)
    for sid, cols in xe.columns.groupby(sec).items():
        if pd.isna(sid) or len(cols) == 0:
            continue
        out[list(cols)] = xe[list(cols)].rank(axis=1, pct=True, method="average")
    return out


def build(ctx: FeatureContext) -> dict[str, pd.DataFrame]:
    f = ctx.fundamentals
    if not f:
        raise RuntimeError("fundamentals family enabled but ctx.fundamentals is empty")
    yoy = f["rev_yoy"]
    yoy3 = (yoy + f["rev_yoy_lag1"] + f["rev_yoy_lag2"]) / 3
    eps_ttm = f["eps"] + f["eps_lag1"] + f["eps_lag2"] + f["eps_lag3"]
    eps_ttm_prev = f["eps_lag4"] + f["eps_lag5"] + f["eps_lag6"] + f["eps_lag7"]
    pe = f["pe"].where(f["pe"] > 0)
    pos_pe = pe.notna()
    return {
        "rev_yoy": yoy,
        "rev_yoy_3m": yoy3,
        "rev_accel": yoy - f["rev_yoy_lag3"],
        "rev_mom": f["rev_mom"],
        "rev_surprise": yoy - f["rev_yoy_lag12"],                       # 對一年前同期 yoy 的變化
        "net_margin": f["net_margin"],
        "gross_margin_chg": f["gross_margin"] - f["gross_margin_lag4"],
        "op_margin_chg": f["op_margin"] - f["op_margin_lag4"],
        "eps_ttm_growth": (eps_ttm - eps_ttm_prev) / eps_ttm_prev.abs().replace(0, np.nan),
        "pe_rank_in_industry": _within_sector_rank(pe, ctx.sector_map, ctx.eligible & pos_pe),
        "pb_rank_in_industry": _within_sector_rank(f["pb"], ctx.sector_map, ctx.eligible),
        "dividend_yield": f["dividend_yield"],
        "earnings_yield": (1.0 / pe),
    }
