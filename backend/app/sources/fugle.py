"""Fugle 富果行情來源：逐檔日 K。

Fugle historical candles 為「逐檔」端點，不支援全市場 by-date，
故 stock_ids=None（全市場）會擋下並提示改用 finmind。
本來源保留給 P1+「候選池」與詳情頁細 K（只對少量股票抓，量小）。
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from .base import BaseSource, SourceError
from .interfaces import PriceProvider
from . import schemas

_FIELDS = "open,high,low,close,volume,turnover"


class FugleSource(BaseSource, PriceProvider):
    name = "fugle"
    base_url = "https://api.fugle.tw/marketdata/v1.0/stock"
    requires_token = True

    def _auth_headers(self) -> dict[str, str]:
        return {"X-API-KEY": self.token} if self.token else {}

    def _probe(self) -> None:
        self._candles("2330", date(2024, 1, 2), date(2024, 1, 5))

    def _candles(self, symbol: str, start: date, end: date) -> list[dict]:
        resp = self._request(
            f"/historical/candles/{symbol}",
            params={"from": start.isoformat(), "to": end.isoformat(), "fields": _FIELDS},
        )
        payload = resp.json()
        return payload.get("data", []) or payload.get("candles", [])

    def fetch_prices(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        if not stock_ids:
            raise SourceError("Fugle 為逐檔端點，全市場請改用 finmind（config.source_bindings）")
        frames: list[pd.DataFrame] = []
        for sid in stock_ids:
            rows = self._candles(sid, start, end)
            if not rows:
                continue
            df = pd.DataFrame(rows)
            df["stock_id"] = sid
            frames.append(df)
        if not frames:
            return pd.DataFrame(columns=schemas.PRICE_COLS)
        all_df = pd.concat(frames, ignore_index=True)
        out = pd.DataFrame()
        out["stock_id"] = all_df["stock_id"].astype(str)
        out["date"] = pd.to_datetime(all_df["date"]).dt.date
        for col in ("open", "high", "low", "close"):
            out[col] = pd.to_numeric(all_df.get(col), errors="coerce")
        out["volume"] = pd.to_numeric(all_df.get("volume"), errors="coerce").astype("Int64")
        out["turnover"] = pd.to_numeric(all_df.get("turnover"), errors="coerce")
        return out[schemas.PRICE_COLS]
