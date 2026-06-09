"""合併市場來源：上市(TWSE) + 上櫃(TPEX) 一起抓。

綁定 price/chip → twmarket，FetchStep 一次取得全台股（含上櫃）。單一市場失敗
不影響另一個（各自 try）。換市場組合只動這裡，下游不變。
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from .base import BaseSource, SourceError
from .interfaces import ChipProvider, FundamentalProvider, PriceProvider
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
