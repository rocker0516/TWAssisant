"""StockContext（架構③）：每檔每天打包所有資料，餵給所有規則。

規則只讀 context、不各自查 DB。ScoringEngine 批次載入後切片建 context。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from ..storage import models


@dataclass
class StockContext:
    stock: models.Stock
    date: date
    prices: pd.DataFrame  # 升冪，欄 open/high/low/close/volume，index=date
    inds: pd.DataFrame  # 升冪，indicator 欄，index=date
    inst: pd.DataFrame  # 升冪，法人欄，index=date（可能空）
    # 長線軌資料（P1 fundamentals 之後填；先給空/None）
    valuation: pd.Series | None = None
    revenue: pd.DataFrame | None = None
    financials: pd.DataFrame | None = None
    sector: models.SectorDaily | None = None  # P3

    # ── 行情 / 指標 ──

    @property
    def close(self) -> float | None:
        return float(self.prices["close"].iloc[-1]) if len(self.prices) else None

    @property
    def ind(self) -> pd.Series | None:
        """最新一日指標。"""
        return self.inds.iloc[-1] if len(self.inds) else None

    def ind_ago(self, n: int) -> pd.Series | None:
        """n 個交易日前的指標列（看斜率用）。"""
        return self.inds.iloc[-1 - n] if len(self.inds) > n else None

    @property
    def n_bars(self) -> int:
        return len(self.prices)

    def recent_low(self, days: int = 20, exclude_today: bool = True) -> float | None:
        """近 days 日最低（前低，支撐用）。"""
        lows = self.prices["low"]
        if exclude_today:
            lows = lows.iloc[:-1]
        lows = lows.iloc[-days:]
        return float(lows.min()) if len(lows) else None

    # ── 籌碼 ──

    def inst_sum(self, col: str, days: int = 5) -> float:
        """近 days 日某法人欄位淨額加總（張）。"""
        if self.inst is None or self.inst.empty or col not in self.inst:
            return 0.0
        return float(self.inst[col].iloc[-days:].fillna(0).sum())
