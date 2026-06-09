"""TPEX 櫃買中心來源（上櫃，全免費、免 token、歷史 by-date 可回補）。

與 TwseSource 對稱（上市），補上櫃股票的行情/法人/融資券。端點每日回一天，
方法內逐交易日迴圈。日期格式 YYYY/MM/DD。數值欄位用位置索引（欄名多重複）。
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .base import BaseSource
from .interfaces import ChipProvider, PriceProvider
from .twse import _UA, _cell, _num
from . import schemas

_BASE = "https://www.tpex.org.tw/www/zh-tw"


class TpexSource(BaseSource, PriceProvider, ChipProvider):
    name = "tpex"
    base_url = _BASE
    requires_token = False

    def _auth_headers(self) -> dict[str, str]:
        return {"User-Agent": _UA, "Referer": "https://www.tpex.org.tw/"}

    def _probe(self) -> None:
        self._day_json("/afterTrading/otc", date(2024, 1, 2), {"type": "EW"})

    def _day_json(self, path: str, d: date, extra: dict) -> dict | None:
        params = {"date": d.strftime("%Y/%m/%d"), "response": "json", **extra}
        body = self._request(path, params=params).json()
        return body if body.get("stat") in ("ok", "OK") else None

    @staticmethod
    def _big_table(body: dict, keyword: str) -> dict | None:
        for t in body.get("tables", []):
            if keyword in (t.get("title") or "") and len(t.get("data") or []) > 20:
                return t
        return None

    def _iter_days(self, start: date, end: date):
        d = start
        while d <= end:
            if d.weekday() < 5:
                yield d
            d += timedelta(days=1)

    # ── PriceProvider ──

    def fetch_prices(self, start: date, end: date, stock_ids: list[str] | None = None) -> pd.DataFrame:
        rows: list[dict] = []
        for d in self._iter_days(start, end):
            body = self._day_json("/afterTrading/otc", d, {"type": "EW"})
            table = self._big_table(body, "每日收盤行情") if body else None
            if not table:
                continue
            for r in table["data"]:
                sid = _cell(r, 0)
                if sid is None:
                    continue
                rows.append({
                    "stock_id": str(sid).strip(), "date": d,
                    "open": _num(_cell(r, 4)), "high": _num(_cell(r, 5)), "low": _num(_cell(r, 6)),
                    "close": _num(_cell(r, 2)),
                    "volume": _num(_cell(r, 7)), "turnover": _num(_cell(r, 8)),
                })
        if not rows:
            return pd.DataFrame(columns=schemas.PRICE_COLS)
        df = pd.DataFrame(rows)
        df["volume"] = df["volume"].astype("Int64")
        return df[schemas.PRICE_COLS]

    # ── ChipProvider ──

    def fetch_institutional(self, start: date, end: date, stock_ids: list[str] | None = None) -> pd.DataFrame:
        # 位置：10=外資合計超 13=投信超 22=自營合計超 23=三大法人合計超（股，→張）
        rows: list[dict] = []
        for d in self._iter_days(start, end):
            body = self._day_json("/insti/dailyTrade", d, {"type": "Daily", "sect": "EW"})
            table = self._big_table(body, "三大法人") if body else None
            if not table:
                continue
            for r in table["data"]:
                sid = _cell(r, 0)
                if sid is None:
                    continue

                def lots(i, _r=r):
                    v = _num(_cell(_r, i))
                    return round(v / 1000) if v is not None else None

                rows.append({
                    "stock_id": str(sid).strip(), "date": d,
                    "foreign_net": lots(10), "trust_net": lots(13),
                    "dealer_net": lots(22), "total_net": lots(23),
                })
        if not rows:
            return pd.DataFrame(columns=schemas.INSTITUTIONAL_COLS)
        df = pd.DataFrame(rows)
        for c in ("foreign_net", "trust_net", "dealer_net", "total_net"):
            df[c] = df[c].astype("Int64")
        return df[schemas.INSTITUTIONAL_COLS]

    def fetch_margin(self, start: date, end: date, stock_ids: list[str] | None = None) -> pd.DataFrame:
        # 位置：2前資餘 6資餘 10前券餘 14券餘（張）
        rows: list[dict] = []
        for d in self._iter_days(start, end):
            body = self._day_json("/margin/balance", d, {})
            table = self._big_table(body, "融資融券餘額") if body else None
            if not table:
                continue
            for r in table["data"]:
                sid = _cell(r, 0)
                if sid is None:
                    continue
                mt, mp = _num(_cell(r, 6)), _num(_cell(r, 2))
                st, sp = _num(_cell(r, 14)), _num(_cell(r, 10))
                rows.append({
                    "stock_id": str(sid).strip(), "date": d,
                    "margin_balance": mt,
                    "margin_change": (mt - mp) if mt is not None and mp is not None else None,
                    "short_balance": st,
                    "short_change": (st - sp) if st is not None and sp is not None else None,
                })
        if not rows:
            return pd.DataFrame(columns=schemas.MARGIN_COLS)
        df = pd.DataFrame(rows)
        for c in ("margin_balance", "margin_change", "short_balance", "short_change"):
            df[c] = df[c].astype("Int64")
        return df[schemas.MARGIN_COLS]
