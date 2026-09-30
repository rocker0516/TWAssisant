"""F3 Flows 族：法人／融資融券（lag-1 矩陣由 data/flows.py 提供）。

以「÷ 20 日均量（張）」正規化，不用 raw 張數；加多日累積與連續買超天數。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .context import FeatureContext

LOOKBACK = 21


def _streak(cond: pd.DataFrame) -> pd.DataFrame:
    arr = cond.fillna(False).to_numpy()
    out = np.zeros(arr.shape, dtype=np.int32)
    run = np.zeros(arr.shape[1], dtype=np.int32)
    for i in range(arr.shape[0]):
        run = np.where(arr[i], run + 1, 0)
        out[i] = run
    return pd.DataFrame(out, index=cond.index, columns=cond.columns, dtype=float)


def build(ctx: FeatureContext) -> dict[str, pd.DataFrame]:
    f = ctx.flows
    if not f:
        raise RuntimeError("flows family enabled but ctx.flows is empty")
    vol_lots = (ctx.prices["volume"] / 1000.0).shift(1)                       # 對齊 lag-1 語意
    v20 = vol_lots.rolling(20, min_periods=10).mean().replace(0, np.nan)
    fo, tr, tot = f["foreign_net"], f["trust_net"], f["total_net"]
    mb, mc, sb, sc = f["margin_balance"], f["margin_change"], f["short_balance"], f["short_change"]
    return {
        "foreign_net_1d_v20": fo / v20,
        "foreign_net_5d_v20": fo.rolling(5, min_periods=3).sum() / v20,
        "foreign_net_20d_v20": fo.rolling(20, min_periods=10).sum() / v20,
        "trust_net_5d_v20": tr.rolling(5, min_periods=3).sum() / v20,
        "trust_net_20d_v20": tr.rolling(20, min_periods=10).sum() / v20,
        "total_net_5d_v20": tot.rolling(5, min_periods=3).sum() / v20,
        "foreign_buy_streak": _streak(fo > 0),
        "foreign_sell_streak": _streak(fo < 0),
        "margin_chg_5d_rel": mc.rolling(5, min_periods=3).sum() / mb.replace(0, np.nan),
        "margin_balance_days": mb / v20,
        "short_chg_5d_rel": sc.rolling(5, min_periods=3).sum() / sb.replace(0, np.nan),
        "short_to_margin": sb / mb.replace(0, np.nan),
    }
