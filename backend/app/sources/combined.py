"""合併市場來源：上市(TWSE) + 上櫃(TPEX) 一起抓。

綁定 price/chip → twmarket，FetchStep 一次取得全台股（含上櫃）。單一市場失敗
不影響另一個（各自 try）。換市場組合只動這裡，下游不變。
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from ..config import settings
from .base import BaseSource, SourceError
from .finmind import FinMindSource
from .interfaces import ChipProvider, FundamentalProvider, NewsProvider, PriceProvider
from .tpex import TpexSource
from .twse import TwseSource


class CombinedMarketSource(BaseSource, PriceProvider, ChipProvider, FundamentalProvider):
    name = "twmarket"
    requires_token = False

    def __init__(self, token: str | None = None) -> None:
        super().__init__(token)
        self._twse = TwseSource()
        self._tpex = TpexSource()

    def _probe(self) -> None:
        self._twse._probe()

    def _merge(self, method: str, start: date, end: date, cols: list[str]) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for src in (self._twse, self._tpex):
            try:
                df = getattr(src, method)(start, end, None)
                if not df.empty:
                    frames.append(df)
            except SourceError:
                continue  # 單一市場失敗，續抓另一個
        if not frames:
            return pd.DataFrame(columns=cols)
        return pd.concat(frames, ignore_index=True)

    def fetch_prices(self, start, end, stock_ids=None):
        from . import schemas
        return self._merge("fetch_prices", start, end, schemas.PRICE_COLS)

    def fetch_institutional(self, start, end, stock_ids=None):
        from . import schemas
        return self._merge("fetch_institutional", start, end, schemas.INSTITUTIONAL_COLS)

    def fetch_margin(self, start, end, stock_ids=None):
        from . import schemas
        return self._merge("fetch_margin", start, end, schemas.MARGIN_COLS)

    def fetch_short_lending(self, start, end, stock_ids=None):
        from . import schemas
        return self._merge("fetch_short_lending", start, end, schemas.SHORT_LENDING_COLS)

    def fetch_day_trading(self, start, end, stock_ids=None):
        # 個股級當沖僅上市有開放端點；TPEX 回預設空表，合併後即上市資料
        from . import schemas
        return self._merge("fetch_day_trading", start, end, schemas.DAY_TRADING_COLS)

    def fetch_insider_holdings(self, start, end, stock_ids=None):
        from . import schemas
        return self._merge("fetch_insider_holdings", start, end, schemas.INSIDER_COLS)

    def fetch_valuation(self, start, end, stock_ids=None):
        from . import schemas
        return self._merge("fetch_valuation", start, end, schemas.VALUATION_COLS)

    def fetch_revenue_monthly(self, start, end, stock_ids=None):
        from . import schemas
        return self._merge("fetch_revenue_monthly", start, end, schemas.REVENUE_COLS)

    def fetch_financials(self, start, end, stock_ids=None):
        from . import schemas
        return self._merge("fetch_financials", start, end, schemas.FINANCIAL_COLS)

    def health(self) -> dict:
        return {"name": self.name, "twse": self._twse.health(), "tpex": self._tpex.health()}


class CombinedNewsSource(BaseSource, NewsProvider):
    """合併新聞來源：TWSE 重訊/處置 + FinMind 個股新聞（+ 選用研報）。

    綁定 news → twnews，NewsEngine 一次取得多源事件。單一來源失敗不影響其他
    （各自 try）。FinMind 需 token，未設定時其 fetch_events 會 SourceError 被略過。
    研報來源預設關閉（config.research_enabled），易壞故不拖垮主流程。
    """

    name = "twnews"
    requires_token = False

    def __init__(self, token: str | None = None) -> None:
        super().__init__(token)
        self._twse = TwseSource()
        self._tpex = TpexSource()  # 上櫃內部人轉讓申報
        self._finmind = FinMindSource()

    def _probe(self) -> None:
        self._twse._probe()

    def fetch_events(self, start: date, end: date) -> pd.DataFrame:
        from . import schemas

        subsources: list[NewsProvider] = [self._twse, self._tpex, self._finmind]
        if settings.research_enabled:
            from .research import ResearchSource

            subsources.append(ResearchSource())

        frames: list[pd.DataFrame] = []
        for src in subsources:
            try:
                df = src.fetch_events(start, end)
                if not df.empty:
                    frames.append(df)
            except SourceError:
                continue  # 單一來源失敗，續抓其他
        if not frames:
            return pd.DataFrame(columns=schemas.EVENT_COLS)
        return pd.concat(frames, ignore_index=True)

    def fetch_stock_events(self, stock_id: str, start: date, end: date) -> pd.DataFrame:
        """逐檔個股新聞：委派給 FinMind（免費層 TaiwanStockNews 逐檔可用）。"""
        try:
            return self._finmind.fetch_stock_events(stock_id, start, end)
        except SourceError:
            from . import schemas

            return pd.DataFrame(columns=schemas.EVENT_COLS)

    def health(self) -> dict:
        return {"name": self.name, "twse": self._twse.health(), "finmind": self._finmind.health()}
