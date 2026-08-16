"""TPEX 櫃買中心來源（上櫃，全免費、免 token、歷史 by-date 可回補）。

與 TwseSource 對稱（上市），補上櫃股票的行情/法人/融資券。端點每日回一天，
方法內逐交易日迴圈。日期格式 YYYY/MM/DD。數值欄位用位置索引（欄名多重複）。
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .base import BaseSource
from .interfaces import ChipProvider, FundamentalProvider, NewsProvider, PriceProvider
from .twse import _UA, _ad_date, _cell, _digest_insider, _insider_transfer_events, _num, _roc_ym
from . import schemas

_BASE = "https://www.tpex.org.tw/www/zh-tw"
_OPENAPI = "https://www.tpex.org.tw/openapi/v1"


class TpexSource(BaseSource, PriceProvider, ChipProvider, FundamentalProvider, NewsProvider):
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

    # ── FundamentalProvider（上櫃，openapi 當日/最新快照）──

    def _openapi(self, name: str) -> list[dict]:
        data = self._request(f"{_OPENAPI}/{name}").json()
        return data if isinstance(data, list) else []

    def fetch_valuation(self, start: date, end: date, stock_ids: list[str] | None = None) -> pd.DataFrame:
        rows: list[dict] = []
        for r in self._openapi("tpex_mainboard_peratio_analysis"):
            sid = (r.get("SecuritiesCompanyCode") or "").strip()
            if not sid:
                continue
            rows.append({
                "stock_id": sid, "date": end,  # 快照，戳當期日期
                "pe": _num(r.get("PriceEarningRatio")), "pb": _num(r.get("PriceBookRatio")),
                "dividend_yield": _num(r.get("YieldRatio")),
            })
        return pd.DataFrame(rows)[schemas.VALUATION_COLS] if rows else pd.DataFrame(columns=schemas.VALUATION_COLS)

    def fetch_revenue_monthly(self, start: date, end: date, stock_ids: list[str] | None = None) -> pd.DataFrame:
        rows: list[dict] = []
        for r in self._openapi("mopsfin_t187ap05_O"):
            year, month = _roc_ym(r.get("資料年月", ""))
            if year is None:
                continue
            rows.append({
                "stock_id": str(r.get("公司代號", "")).strip(), "year": year, "month": month,
                "revenue": _num(r.get("營業收入-當月營收")), "yoy": _num(r.get("營業收入-去年同月增減(%)")),
                "mom": _num(r.get("營業收入-上月比較增減(%)")),
            })
        return pd.DataFrame(rows)[schemas.REVENUE_COLS] if rows else pd.DataFrame(columns=schemas.REVENUE_COLS)

    def fetch_financials(self, start: date, end: date, stock_ids: list[str] | None = None) -> pd.DataFrame:
        """季財報（MOPS 累計制→單季）。原 openapi 快照為累計制，同 TwseSource 改走差分。"""
        from . import mops  # 延遲匯入避免循環

        return mops.fetch_recent_financials(markets=("otc",), today=end)

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

    def fetch_short_lending(self, start: date, end: date, stock_ids: list[str] | None = None) -> pd.DataFrame:
        # /margin/sbl 信用額度總量管制餘額表，欄位佈局與 TWSE TWT93U 相同（單位＝股）：
        # 0代號 1名稱 | 2~7 融券 | 8前餘 9當日賣出 10當日還券 11當日調整 12當日餘額 13次日限額 14備註
        rows: list[dict] = []
        for d in self._iter_days(start, end):
            body = self._day_json("/margin/sbl", d, {})
            table = self._big_table(body, "信用額度總量管制") if body else None
            if not table:
                continue
            for r in table["data"]:
                sid = _cell(r, 0)
                if sid is None:
                    continue
                bal, prev = _num(_cell(r, 12)), _num(_cell(r, 8))
                sell = _num(_cell(r, 9))
                rows.append({
                    "stock_id": str(sid).strip(), "date": d,
                    "sbl_balance": round(bal / 1000) if bal is not None else None,
                    "sbl_change": round((bal - prev) / 1000)
                    if bal is not None and prev is not None else None,
                    "sbl_sell": round(sell / 1000) if sell is not None else None,
                })
        if not rows:
            return pd.DataFrame(columns=schemas.SHORT_LENDING_COLS)
        df = pd.DataFrame(rows)
        for c in ("sbl_balance", "sbl_change", "sbl_sell"):
            df[c] = df[c].astype("Int64")
        return df[schemas.SHORT_LENDING_COLS]

    def fetch_company_profiles(
        self, start: date | None = None, end: date | None = None,
        stock_ids: list[str] | None = None,
    ) -> pd.DataFrame:
        """上櫃公司基本資料（mopsfin_t187ap03_O 全快照），欄位對齊 TWSE 版。"""
        rows: list[dict] = []
        for r in self._openapi("mopsfin_t187ap03_O"):
            sid = (r.get("SecuritiesCompanyCode") or "").strip()
            if not sid:
                continue
            rows.append({
                "stock_id": sid,
                "chairman": (r.get("Chairman") or "").strip() or None,
                "president": (r.get("GeneralManager") or "").strip() or None,
                "capital": _num(r.get("Paidin.Capital.NTDollars")),
                "issued_shares": _num(r.get("IssueShares")),
                "established_date": _ad_date(r.get("DateOfIncorporation") or ""),
                "listed_date": _ad_date(r.get("DateOfListing") or ""),
                "website": (r.get("WebAddress") or "").strip() or None,
            })
        if not rows:
            return pd.DataFrame(columns=schemas.COMPANY_PROFILE_COLS)
        return pd.DataFrame(rows)[schemas.COMPANY_PROFILE_COLS]

    def fetch_insider_holdings(self, start: date, end: date, stock_ids: list[str] | None = None) -> pd.DataFrame:
        """董監事持股餘額明細（mopsfin_t187ap11_O 月快照）→ 逐公司加總。日期參數忽略。"""
        return _digest_insider(self._openapi("mopsfin_t187ap11_O"))

    # ── NewsProvider（上櫃內部人轉讓申報；重訊/處置由 TWSE 端點涵蓋上市）──

    def fetch_events(self, start: date, end: date) -> pd.DataFrame:
        rows = _insider_transfer_events(self._openapi("mopsfin_t187ap12_O"))
        if not rows:
            return pd.DataFrame(columns=schemas.EVENT_COLS)
        return pd.DataFrame(rows)[schemas.EVENT_COLS]
