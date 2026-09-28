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


# ── C. 大盤／寬度（context-gate；raw，同日各股相同）──

def _broadcast(s: pd.Series, like: pd.DataFrame) -> pd.DataFrame:
    arr = np.broadcast_to(s.reindex(like.index).to_numpy(dtype=float)[:, None], like.shape)
    return pd.DataFrame(arr.copy(), index=like.index, columns=like.columns)


def build_market_features(close: pd.DataFrame, in_universe: pd.DataFrame,
                          mkt_close: pd.Series) -> dict[str, pd.DataFrame]:
    """mkt_ret5 / mkt_ret20 / mkt_vol20（加權指數）；breadth_ma20 / dispersion（只用 U_t 內）。

    大盤特徵在橫斷面內是常數——v2 因 rank 表示而只能做交互；v3 目標是絕對報酬，
    GBM 直接吃原始值即可（「今天不進場」主要靠這族）。
    """
    mkt = mkt_close.reindex(close.index).ffill()
    mret1 = mkt.pct_change(fill_method=None)
    ma20 = close.rolling(_W20, min_periods=_MP20).mean()
    ret1 = close.pct_change(fill_method=None)
    series = {
        "mkt_ret5": mkt.pct_change(5, fill_method=None),
        "mkt_ret20": mkt.pct_change(20, fill_method=None),
        "mkt_vol20": mret1.rolling(_W20, min_periods=_MP20).std(),
        "breadth_ma20": (close > ma20).astype(float).where(in_universe & ma20.notna()).mean(axis=1),
        "dispersion": ret1.where(in_universe).std(axis=1),
    }
    return {k: _broadcast(v, close) for k, v in series.items()}


# ── D 動能 / E 流動性（direction；to_rank）＋ dollar_vol20（raw）──

def build_direction_features(close: pd.DataFrame, volume: pd.DataFrame, turnover: pd.DataFrame,
                             in_universe: pd.DataFrame, sector_of: pd.Series,
                             ) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    """→ (to_rank, raw)。sec_neutral_ret20 = ret20 − 同日同類股（U_t 內）ret20 均值。"""
    ret1 = close.pct_change(fill_method=None)
    ret20 = close.pct_change(20, fill_method=None)
    v5 = volume.rolling(5, min_periods=3).mean()
    v60 = volume.rolling(_W60, min_periods=20).mean()

    sec = sector_of.reindex(close.columns)
    r20_u = ret20.where(in_universe)
    # 以 columns 分組：轉置後 groupby 類股 → transform mean → 轉回。NaN 類股不分組 → NaN。
    sec_mean = r20_u.T.groupby(sec.to_numpy()).transform("mean").T.reindex(columns=close.columns)

    to_rank = {
        "ret1": ret1,
        "ret5": close.pct_change(5, fill_method=None),
        "ret20": ret20,
        "ret60": close.pct_change(60, fill_method=None),
        "ret20_ex5": close.shift(5) / close.shift(20) - 1,
        "bias20": close / close.rolling(_W20, min_periods=_MP20).mean() - 1,
        "pos240": close / close.rolling(240, min_periods=60).max() - 1,
        "vr5_60": v5 / v60,
        "amihud20": (ret1.abs() / turnover).rolling(_W20, min_periods=_MP20).mean(),
        "sec_neutral_ret20": ret20 - sec_mean,
    }
    raw = {"dollar_vol20": np.log1p(turnover.rolling(_W20, min_periods=_MP20).mean())}
    return to_rank, raw
