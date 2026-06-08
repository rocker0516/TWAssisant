"""運算引擎底層（架構③）。

BaseEngine：每個引擎吃 session + 交易日，算完落庫，回 summary（供 pipeline log）。
執行順序（依賴）：Indicator → Sector → News → Scoring → Exit。
規則不各自查 DB：ScoringEngine 先把每檔資料打包成 StockContext 再餵規則。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from sqlalchemy.orm import Session


class BaseEngine(ABC):
    name: str = "engine"

    @abstractmethod
    def run(self, session: Session, trading_date: date) -> dict:
        """執行並回 summary dict。"""
