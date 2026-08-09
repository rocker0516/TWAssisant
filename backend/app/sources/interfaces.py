"""能力介面（架構②：能力介面分離，下游依賴介面而非具體來源）。

換來源 = 新類實作同介面 + 改 config.source_bindings，下游邏輯不動。
所有 fetch_* 回傳 schemas 定義之固定欄位 DataFrame。
stock_ids=None 代表「全市場 by-date」（FinMind 三招之一，1700 次 → 1 次）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class UniverseProvider(ABC):
    @abstractmethod
    def fetch_universe(self) -> pd.DataFrame:
        """個股主檔清單。欄位見 schemas.UNIVERSE_COLS。"""


class PriceProvider(ABC):
    @abstractmethod
    def fetch_prices(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        """日 K 行情。欄位見 schemas.PRICE_COLS。"""


class ChipProvider(ABC):
    @abstractmethod
    def fetch_institutional(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        """三大法人買賣超。欄位見 schemas.INSTITUTIONAL_COLS。"""

    @abstractmethod
    def fetch_margin(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        """融資融券。欄位見 schemas.MARGIN_COLS。"""

    def fetch_short_lending(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        """借券賣出餘額（選用能力，預設無）。欄位見 schemas.SHORT_LENDING_COLS。"""
        from . import schemas

        return pd.DataFrame(columns=schemas.SHORT_LENDING_COLS)

    def fetch_day_trading(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        """個股現股當沖統計（選用能力，預設無）。欄位見 schemas.DAY_TRADING_COLS。"""
        from . import schemas

        return pd.DataFrame(columns=schemas.DAY_TRADING_COLS)


class HoldingProvider(ABC):
    @abstractmethod
    def fetch_holding_distribution(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        """集保戶股權分散（大戶/散戶占比）。欄位見 schemas.HOLDING_COLS。

        來源（TDCC 開放資料）僅回最新一週快照，start/end 多被忽略；靠每週 upsert
        累積歷史。回傳已消化的固定欄位（占比/人數），非 17 級原始分布。
        """

    def fetch_holding_history(self, stock_id: str, max_weeks: int = 52) -> pd.DataFrame:
        """單一個股近 max_weeks 週集保歷史（選用能力，預設無）。供曲線回補歷史。

        欄位見 schemas.HOLDING_COLS。整市場快照無歷史，故另開逐檔歷史路徑（如 TDCC
        智慧網單檔查詢）；不支援的來源回空表。
        """
        from . import schemas

        return pd.DataFrame(columns=schemas.HOLDING_COLS)


class FundamentalProvider(ABC):
    @abstractmethod
    def fetch_revenue_monthly(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        """月營收。欄位見 schemas.REVENUE_COLS。"""

    @abstractmethod
    def fetch_financials(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        """季財報。欄位見 schemas.FINANCIAL_COLS。"""

    @abstractmethod
    def fetch_valuation(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        """估值（PE/PB/殖利率）。欄位見 schemas.VALUATION_COLS。"""

    def fetch_insider_holdings(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        """董監持股彙總月快照（選用能力，預設無）。欄位見 schemas.INSIDER_COLS。"""
        from . import schemas

        return pd.DataFrame(columns=schemas.INSIDER_COLS)


class NewsProvider(ABC):
    @abstractmethod
    def fetch_events(self, start: date, end: date) -> pd.DataFrame:
        """全市場重訊 / 新聞事件。欄位見 schemas.EVENT_COLS。"""

    def fetch_stock_events(self, stock_id: str, start: date, end: date) -> pd.DataFrame:
        """單一個股新聞（選用能力，預設無）。供無法整市場抓、只能逐檔抓的來源
        （如 FinMind 免費層 TaiwanStockNews）。欄位見 schemas.EVENT_COLS。"""
        from . import schemas

        return pd.DataFrame(columns=schemas.EVENT_COLS)
