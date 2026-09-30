"""Tradable Universe U_t（FRS §7.2；附錄 A eligibility 鏈）。

Coverage → 有 PIT 價格 → 歷史夠長（calendar 基準）→ 歷史無缺口 → hard 品質 → 可選流動性。
處置、注意、波動、成交量、市值、產業一律不篩（§7.4）。
"""

from __future__ import annotations

from enum import IntFlag

import numpy as np
import pandas as pd

from ..config import UniverseConfig
from .quality import hard_flags, present_mask


class EligFlag(IntFlag):
    NO_PRICE = 0x01
    HISTORY_TOO_SHORT = 0x02
    HISTORY_GAPPED = 0x04
    DATA_QUALITY = 0x08
    LOW_LIQUIDITY = 0x10


PRECEDENCE = (EligFlag.NO_PRICE, EligFlag.HISTORY_TOO_SHORT, EligFlag.HISTORY_GAPPED,
              EligFlag.DATA_QUALITY, EligFlag.LOW_LIQUIDITY)


def primary_reason(flags: pd.DataFrame) -> pd.DataFrame:
    """依 PRECEDENCE 取第一個命中的原因名稱；eligible 為 None。僅供報表／UI。"""
    out = np.full(flags.shape, None, dtype=object)
    for fl in reversed(PRECEDENCE):
        out[(flags.to_numpy() & int(fl)) != 0] = fl.name
    return pd.DataFrame(out, index=flags.index, columns=flags.columns)


def history_flags(valid: pd.DataFrame, lookback: int, min_coverage: float) -> pd.DataFrame:
    """valid: bool 矩陣（該日有有效價格列）。→ uint16（HISTORY_TOO_SHORT / HISTORY_GAPPED）。

    歷史窗 = t 之前的 lookback 個 calendar 日 [t−lookback, t−1]（不含 t：t 當日的問題由
    NO_PRICE / DATA_QUALITY 表達，不重複計）。首個有效日晚於窗起點、或 calendar 本身
    不足 lookback 日 → TOO_SHORT；否則窗內 coverage < min_coverage → GAPPED。
    """
    v = valid.to_numpy(dtype=np.int64)
    n, m = v.shape
    csum = np.vstack([np.zeros((1, m), dtype=np.int64), np.cumsum(v, axis=0)])
    rows = np.arange(n)
    start = rows - lookback                                   # 窗起點位置（可為負）
    cnt = csum[rows] - csum[np.maximum(start, 0)]             # [start, t−1] 有效日數
    first = np.where(v.any(axis=0), np.argmax(v, axis=0), n)
    too_short = (start[:, None] < 0) | (first[None, :] > start[:, None])
    gapped = (~too_short) & (cnt / lookback < min_coverage)
    f = (too_short.astype(np.uint16) * int(EligFlag.HISTORY_TOO_SHORT)
         | gapped.astype(np.uint16) * int(EligFlag.HISTORY_GAPPED))
    return pd.DataFrame(f, index=valid.index, columns=valid.columns)


def adv(turnover: pd.DataFrame, window: int = 20, min_periods: int = 10) -> pd.DataFrame:
    return turnover.rolling(window, min_periods=min_periods).mean()


def build_universe(m: dict[str, pd.DataFrame], cfg: UniverseConfig,
                   ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """→ (eligible bool 矩陣, eligibility_flags uint16 矩陣)。矩陣列 = calendar。"""
    close = m["close"]
    hard = hard_flags(m)
    present = present_mask(m)
    valid = present & (hard == 0)
    flags = pd.DataFrame(0, index=close.index, columns=close.columns, dtype="uint16")
    flags = flags | (~present).astype("uint16") * int(EligFlag.NO_PRICE)
    flags = flags | history_flags(valid, cfg.history_lookback, cfg.min_history_coverage)
    flags = flags | (present & (hard != 0)).astype("uint16") * int(EligFlag.DATA_QUALITY)
    if cfg.min_liquidity_twd is not None:
        low = (adv(m["turnover"]) < cfg.min_liquidity_twd).fillna(True)
        flags = flags | (present & low).astype("uint16") * int(EligFlag.LOW_LIQUIDITY)
    return flags == 0, flags
