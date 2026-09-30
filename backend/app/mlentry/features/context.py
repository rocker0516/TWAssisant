"""FeatureContext：feature 函式唯一輸入（FRS §3.3、附錄 A）。

所有矩陣已由 builder 截斷到 as_of（含）。feature 模組不得 import DB 相關套件
（tests/test_mlentry_features.py 的架構測試釘住）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..data.calendar import TradingCalendar


@dataclass(frozen=True)
class FeatureContext:
    as_of: str
    calendar: TradingCalendar
    prices: dict[str, pd.DataFrame]          # open/high/low/close/volume/turnover，列 <= as_of
    market_close: pd.Series                  # 與 prices 同列
    sector_map: pd.Series                    # stock_id → sector_id（NaN = 無類股）
    eligible: pd.DataFrame                   # U_t bool 矩陣（橫斷面統計只在 U_t 內做）
    events: dict[str, pd.DataFrame] = field(default_factory=dict)   # attention / disposition bool
    limits: dict[str, pd.DataFrame] = field(default_factory=dict)   # up / down（以前收算）
    fundamentals: object = None              # v1 不用；介面保留

    def __post_init__(self):
        idx = self.prices["close"].index
        if idx[-1] != self.as_of:
            raise ValueError(f"prices end at {idx[-1]} but as_of={self.as_of}: not truncated")
        for k, v in self.prices.items():
            if not v.index.equals(idx):
                raise ValueError(f"prices[{k}] index misaligned")
        if not self.eligible.index.equals(idx):
            raise ValueError("eligible index misaligned")
