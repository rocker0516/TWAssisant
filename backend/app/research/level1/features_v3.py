"""Level 1 v3 特徵（設計 2026-09-28 §2）——按「角色」決定表示法，不做雙表示。

角色 → 表示：
- 方向訊號（D 動能／E 流動性比率／F 基本面）→ 每日橫斷面 rank，缺值補 0.5
- 尺度（B）／情境（C 大盤寬度）／成交（A）→ 原始值，缺值留 NaN 給 GBM，不補 0

所有回看視窗含 T 日（決策時點 = T 收盤後）。任何用到 O(t+1)、C(t+1) 的量都是標籤，
不得出現在這裡（test_level1_v3_features 的 _assert_no_future 釘死）。
"""

from __future__ import annotations

from dataclasses import dataclass, field  # noqa: F401 -- Task 6 uses it

import numpy as np
import pandas as pd

from .targets_v3 import limit_up_from_prev

_W20, _MP20 = 20, 10
_W60, _MP60 = 60, 30


# ── A. 成交／隔夜（execution；raw）──

def build_execution_features(open_: pd.DataFrame, close: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """dist_limit_up / lockup_days20 / gap_std20 / overnight_minus_intraday20。"""
    prev_close = close.shift(1)
    lim_today = limit_up_from_prev(prev_close)           # C(t−1) → t 日漲停價
    locked = (close >= lim_today - 1e-9).astype(float).where(close.notna() & lim_today.notna())
    gap = open_ / prev_close - 1                         # 隔夜報酬 O(t)/C(t−1)
    intraday = close / open_ - 1                         # 盤中報酬 C(t)/O(t)
    return {
        "dist_limit_up": close / lim_today - 1,
        "lockup_days20": locked.rolling(_W20, min_periods=_MP20).sum(),
        "gap_std20": gap.rolling(_W20, min_periods=_MP20).std(),
        "overnight_minus_intraday20": (gap.rolling(_W20, min_periods=_MP20).sum()
                                       - intraday.rolling(_W20, min_periods=_MP20).sum()),
    }


# ── B. 尺度（scale；raw）──

def build_scale_features(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame,
                         ) -> dict[str, pd.DataFrame]:
    """vol20 / vol60 / downside_vol20（下行半標準差）/ atr14_pct。"""
    ret1 = close.pct_change(fill_method=None)
    prev_close = close.shift(1)
    tr_arr = np.maximum.reduce([
        (high - low).to_numpy(),
        (high - prev_close).abs().to_numpy(),
        (low - prev_close).abs().to_numpy(),
    ])
    tr = pd.DataFrame(tr_arr, index=close.index, columns=close.columns)
    downside_var = ret1.clip(upper=0).pow(2).rolling(_W20, min_periods=_MP20).mean()
    return {
        "vol20": ret1.rolling(_W20, min_periods=_MP20).std(),
        "vol60": ret1.rolling(_W60, min_periods=_MP60).std(),
        "downside_vol20": downside_var.pow(0.5),
        "atr14_pct": tr.rolling(14, min_periods=7).mean() / close,
    }
