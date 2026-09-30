"""資料品質旗標（FRS §7.2、§23.1；附錄 A）。

hard（踢出 U_t）只用結構性規則；soft 只記錄、不影響 eligibility——
Data Quality Layer 不得變成 Alpha Filter。所有規則向量化在矩陣上算，
輸入為對齊 calendar 的 OHLCV+turnover 矩陣。
"""

from __future__ import annotations

from enum import IntFlag

import numpy as np
import pandas as pd

from app.research.level2.costs import down_limit, up_limit


class HardFlag(IntFlag):
    OPEN_MISSING = 0x0001
    HIGH_MISSING = 0x0002
    LOW_MISSING = 0x0004
    CLOSE_MISSING = 0x0008
    HIGH_LT_LOW = 0x0010
    HIGH_LT_OPEN = 0x0020
    HIGH_LT_CLOSE = 0x0040
    LOW_GT_OPEN = 0x0080
    LOW_GT_CLOSE = 0x0100
    NEGATIVE_VOLUME = 0x0200
    NEGATIVE_TURNOVER = 0x0400
    NON_POSITIVE_PRICE = 0x0800


class SoftFlag(IntFlag):
    ABNORMAL_RETURN = 0x0001          # |close/prev_close − 1| > 0.12（超出漲跌停含 tick 餘裕）
    PRICE_LIMIT_VIOLATION = 0x0002    # high > up_limit(prev) 或 low < down_limit(prev)
    CORPORATE_ACTION_SUSPECT = 0x0004  # open 與 close 皆低於 down_limit(prev)：疑除權息／減資


_up = np.vectorize(up_limit, otypes=[float])
_down = np.vectorize(down_limit, otypes=[float])
_TOL = 1e-6


def limits_from_prev_close(close: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """以前一日收盤算當日漲停／跌停價矩陣（tick 貼齊由 costs 單一來源負責）。"""
    prev = close.shift(1)
    vals = prev.to_numpy(dtype=float)
    ok = np.isfinite(vals)
    up = np.full_like(vals, np.nan)
    dn = np.full_like(vals, np.nan)
    up[ok] = _up(vals[ok])
    dn[ok] = _down(vals[ok])
    return (pd.DataFrame(up, index=close.index, columns=close.columns),
            pd.DataFrame(dn, index=close.index, columns=close.columns))


def present_mask(m: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """該格是否有價格列：任一 OHLC 非 NaN。"""
    return m["open"].notna() | m["high"].notna() | m["low"].notna() | m["close"].notna()


def _accumulate(base: pd.DataFrame, rules: list[tuple[pd.DataFrame, IntFlag]],
                gate: pd.DataFrame) -> pd.DataFrame:
    f = base
    for cond, flag in rules:
        f = f | ((cond.fillna(False) & gate).astype("uint16") * int(flag))
    return f


def hard_flags(m: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """uint16 矩陣。無價格列的格為 0（那是 NO_PRICE，屬 universe 而非 quality）。"""
    o, h, l, c = m["open"], m["high"], m["low"], m["close"]
    present = present_mask(m)
    zero = pd.DataFrame(0, index=c.index, columns=c.columns, dtype="uint16")
    rules = [
        (o.isna(), HardFlag.OPEN_MISSING),
        (h.isna(), HardFlag.HIGH_MISSING),
        (l.isna(), HardFlag.LOW_MISSING),
        (c.isna(), HardFlag.CLOSE_MISSING),
        (h < l, HardFlag.HIGH_LT_LOW),
        (h < o, HardFlag.HIGH_LT_OPEN),
        (h < c, HardFlag.HIGH_LT_CLOSE),
        (l > o, HardFlag.LOW_GT_OPEN),
        (l > c, HardFlag.LOW_GT_CLOSE),
        (m["volume"] < 0, HardFlag.NEGATIVE_VOLUME),
        (m["turnover"] < 0, HardFlag.NEGATIVE_TURNOVER),
        ((o <= 0) | (h <= 0) | (l <= 0) | (c <= 0), HardFlag.NON_POSITIVE_PRICE),
    ]
    return _accumulate(zero, rules, present)


def soft_flags(m: dict[str, pd.DataFrame]) -> pd.DataFrame:
    o, h, l, c = m["open"], m["high"], m["low"], m["close"]
    up, dn = limits_from_prev_close(c)
    ret = c / c.shift(1) - 1
    present = present_mask(m)
    zero = pd.DataFrame(0, index=c.index, columns=c.columns, dtype="uint16")
    rules = [
        (ret.abs() > 0.12, SoftFlag.ABNORMAL_RETURN),
        ((h > up * (1 + _TOL)) | (l < dn * (1 - _TOL)), SoftFlag.PRICE_LIMIT_VIOLATION),
        ((o < dn * (1 - _TOL)) & (c < dn * (1 - _TOL)), SoftFlag.CORPORATE_ACTION_SUSPECT),
    ]
    return _accumulate(zero, rules, present)
