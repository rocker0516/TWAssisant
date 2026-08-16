"""FinMind 來源：主檔 + 籌碼 + 基本面。

流量三招（架構②）：
  1. 按日期全市場端點（stock_ids=None → 不帶 data_id，一次抓整個市場）
  2. 增量抓（FetchStep 只給 last_date+1 ~ today）
  3. token bucket 限流 + 退避重試（BaseSource 已提供）

FinMind v4 data 端點回 {status, msg, data:[...]}，status!=200 視為失敗。
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .base import BaseSource, SourceError
from .interfaces import (
    ChipProvider,
    FundamentalProvider,
    NewsProvider,
    PriceProvider,
    UniverseProvider,
)
from . import schemas

_FOREIGN = {"Foreign_Investor", "Foreign_Dealer_Self"}
_TRUST = {"Investment_Trust"}
_DEALER = {"Dealer_self", "Dealer_Hedging"}


class FinMindSource(
    BaseSource, UniverseProvider, PriceProvider, ChipProvider, FundamentalProvider, NewsProvider
):
    name = "finmind"
    base_url = "https://api.finmindtrade.com/api/v4"
    requires_token = True

    # ── 底層：呼叫 data 端點 ──

    def _data(
        self,
        dataset: str,
        start: date | None = None,
        end: date | None = None,
        data_id: str | None = None,
    ) -> pd.DataFrame:
        params: dict[str, str] = {"dataset": dataset}
        if data_id:
            params["data_id"] = data_id
        if start:
            params["start_date"] = start.isoformat()
        if end:
            params["end_date"] = end.isoformat()
        if self.token:
            params["token"] = self.token

        resp = self._request("/data", params=params)
        payload = resp.json()
        status = payload.get("status")
        if status != 200:
            msg = payload.get("msg", "unknown")
            # 402 / upper limit → 當作限流原因回報
            raise SourceError(f"FinMind {status}: {msg}", status=status)
        return pd.DataFrame(payload.get("data", []))

    def _probe(self) -> None:
        # 抓台積電一天，驗 token 與連線
        self._data("TaiwanStockPrice", date(2024, 1, 2), date(2024, 1, 2), data_id="2330")

    # ── UniverseProvider ──

    def fetch_universe(self) -> pd.DataFrame:
        df = self._data("TaiwanStockInfo")
        if df.empty:
            return pd.DataFrame(columns=schemas.UNIVERSE_COLS)
        out = pd.DataFrame()
        out["id"] = df["stock_id"].astype(str)
        out["name"] = df["stock_name"]
        out["industry_category"] = df.get("industry_category")
        out["sector_name"] = df.get("industry_category")
        # type: twse=上市 / tpex=上櫃
        out["market"] = df.get("type", pd.Series()).map({"twse": "上市", "tpex": "上櫃"})
        out["listed_date"] = pd.to_datetime(df.get("date"), errors="coerce").dt.date
        out["is_etf"] = df.get("industry_category", "").eq("ETF") | out["id"].str.startswith("00")
        # 同股號可能多列，留一筆
        out = out.drop_duplicates(subset=["id"], keep="first")
        return out[schemas.UNIVERSE_COLS]

    # ── PriceProvider（全市場日K：TaiwanStockPrice 支援 by-date 一次抓）──

    def fetch_prices(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        df = self._data(
            "TaiwanStockPrice",
            start,
            end,
            data_id=stock_ids[0] if stock_ids and len(stock_ids) == 1 else None,
        )
        if df.empty:
            return pd.DataFrame(columns=schemas.PRICE_COLS)
        out = pd.DataFrame()
        out["stock_id"] = df["stock_id"].astype(str)
        out["date"] = pd.to_datetime(df["date"]).dt.date
        out["open"] = pd.to_numeric(df["open"], errors="coerce")
        out["high"] = pd.to_numeric(df["max"], errors="coerce")
        out["low"] = pd.to_numeric(df["min"], errors="coerce")
        out["close"] = pd.to_numeric(df["close"], errors="coerce")
        out["volume"] = pd.to_numeric(df["Trading_Volume"], errors="coerce").astype("Int64")
        out["turnover"] = pd.to_numeric(df["Trading_money"], errors="coerce")
        return out[schemas.PRICE_COLS]

    # ── ChipProvider ──

    def fetch_institutional(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        df = self._data(
            "TaiwanStockInstitutionalInvestorsBuySell",
            start,
            end,
            data_id=stock_ids[0] if stock_ids and len(stock_ids) == 1 else None,
        )
        if df.empty:
            return pd.DataFrame(columns=schemas.INSTITUTIONAL_COLS)
        df["buy"] = pd.to_numeric(df["buy"], errors="coerce").fillna(0)
        df["sell"] = pd.to_numeric(df["sell"], errors="coerce").fillna(0)
        df["net"] = (df["buy"] - df["sell"]) / 1000.0  # 股 → 張

        def bucket(name: str) -> str:
            if name in _FOREIGN:
                return "foreign_net"
            if name in _TRUST:
                return "trust_net"
            if name in _DEALER:
                return "dealer_net"
            return "other"

        df["cat"] = df["name"].map(bucket)
        pivot = (
            df.pivot_table(index=["date", "stock_id"], columns="cat", values="net", aggfunc="sum")
            .reset_index()
        )
        for col in ("foreign_net", "trust_net", "dealer_net"):
            if col not in pivot:
                pivot[col] = 0
        out = pd.DataFrame()
        out["stock_id"] = pivot["stock_id"].astype(str)
        out["date"] = pd.to_datetime(pivot["date"]).dt.date
        for col in ("foreign_net", "trust_net", "dealer_net"):
            out[col] = pivot[col].round().astype("Int64")
        out["total_net"] = (pivot["foreign_net"] + pivot["trust_net"] + pivot["dealer_net"]).round().astype("Int64")
        return out[schemas.INSTITUTIONAL_COLS]

    def fetch_margin(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        df = self._data(
            "TaiwanStockMarginPurchaseShortSale",
            start,
            end,
            data_id=stock_ids[0] if stock_ids and len(stock_ids) == 1 else None,
        )
        if df.empty:
            return pd.DataFrame(columns=schemas.MARGIN_COLS)
        num = lambda c: pd.to_numeric(df.get(c), errors="coerce")  # noqa: E731
        out = pd.DataFrame()
        out["stock_id"] = df["stock_id"].astype(str)
        out["date"] = pd.to_datetime(df["date"]).dt.date
        out["margin_balance"] = num("MarginPurchaseTodayBalance").astype("Int64")
        out["margin_change"] = (
            num("MarginPurchaseTodayBalance") - num("MarginPurchaseYesterdayBalance")
        ).astype("Int64")
        out["short_balance"] = num("ShortSaleTodayBalance").astype("Int64")
        out["short_change"] = (
            num("ShortSaleTodayBalance") - num("ShortSaleYesterdayBalance")
        ).astype("Int64")
        return out[schemas.MARGIN_COLS]

    # ── FundamentalProvider ──

    def fetch_revenue_monthly(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        df = self._data(
            "TaiwanStockMonthRevenue",
            start,
            end,
            data_id=stock_ids[0] if stock_ids and len(stock_ids) == 1 else None,
        )
        if df.empty:
            return pd.DataFrame(columns=schemas.REVENUE_COLS)
        out = pd.DataFrame()
        out["stock_id"] = df["stock_id"].astype(str)
        out["year"] = pd.to_numeric(df["revenue_year"], errors="coerce").astype("Int64")
        out["month"] = pd.to_numeric(df["revenue_month"], errors="coerce").astype("Int64")
        out["revenue"] = pd.to_numeric(df["revenue"], errors="coerce")
        out["yoy"] = None  # YoY/MoM 由 P1 引擎用歷史月營收回算
        out["mom"] = None
        return out[schemas.REVENUE_COLS]

    def fetch_financials(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        """FinancialStatements 為 long 格式（type/value），pivot 取 EPS 等。"""
        df = self._data(
            "TaiwanStockFinancialStatements",
            start,
            end,
            data_id=stock_ids[0] if stock_ids and len(stock_ids) == 1 else None,
        )
        if df.empty:
            return pd.DataFrame(columns=schemas.FINANCIAL_COLS)
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        pivot = (
            df.pivot_table(index=["stock_id", "date"], columns="type", values="value", aggfunc="last")
            .reset_index()
        )
        d = pd.to_datetime(pivot["date"])
        out = pd.DataFrame()
        out["stock_id"] = pivot["stock_id"].astype(str)
        out["year"] = d.dt.year
        out["quarter"] = d.dt.quarter
        out["eps"] = pivot.get("EPS")
        out["revenue"] = pivot.get("Revenue")
        out["gross_margin"] = None
        out["op_margin"] = None
        out["net_margin"] = None
        out["roe"] = None
        return out[schemas.FINANCIAL_COLS]

    def fetch_valuation(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        df = self._data(
            "TaiwanStockPER",
            start,
            end,
            data_id=stock_ids[0] if stock_ids and len(stock_ids) == 1 else None,
        )
        if df.empty:
            return pd.DataFrame(columns=schemas.VALUATION_COLS)
        out = pd.DataFrame()
        out["stock_id"] = df["stock_id"].astype(str)
        out["date"] = pd.to_datetime(df["date"]).dt.date
        out["pe"] = pd.to_numeric(df.get("PER"), errors="coerce")
        out["pb"] = pd.to_numeric(df.get("PBR"), errors="coerce")
        out["dividend_yield"] = pd.to_numeric(df.get("dividend_yield"), errors="coerce")
        return out[schemas.VALUATION_COLS]

    def fetch_dividends(self, stock_id: str, start: date) -> pd.DataFrame:
        """股利政策（TaiwanStockDividend，逐檔）。免 token 可用（低速層）。

        現金/股票股利 = 盈餘分配 + 法定盈餘公積 兩欄加總（資本公積現金歸在
        CashStatutorySurplus）。欄位對齊 DIVIDEND_COLS。
        """
        df = self._data("TaiwanStockDividend", start, data_id=stock_id)
        if df.empty:
            return pd.DataFrame(columns=schemas.DIVIDEND_COLS)

        def _d(col: str) -> pd.Series:
            return pd.to_datetime(df.get(col), errors="coerce").dt.date

        out = pd.DataFrame()
        out["stock_id"] = df["stock_id"].astype(str)
        out["period"] = df["year"].astype(str)
        out["cash"] = (
            pd.to_numeric(df.get("CashEarningsDistribution"), errors="coerce").fillna(0)
            + pd.to_numeric(df.get("CashStatutorySurplus"), errors="coerce").fillna(0)
        )
        out["stock"] = (
            pd.to_numeric(df.get("StockEarningsDistribution"), errors="coerce").fillna(0)
            + pd.to_numeric(df.get("StockStatutorySurplus"), errors="coerce").fillna(0)
        )
        out["cash_ex_date"] = _d("CashExDividendTradingDate")
        out["pay_date"] = _d("CashDividendPaymentDate")
        # 同 period 多列（董事會→股東會進度）取最後公告
        out = out.drop_duplicates(subset=["stock_id", "period"], keep="last")
        return out[schemas.DIVIDEND_COLS]

    # ── 財務報表（資產負債表 + 現金流量表；逐檔懶抓，個股頁快取用）──

    # FinMind type 英文鍵（穩定）→ 本地欄名。_per 結尾為占比欄，不取。
    _BS_TYPES = {
        "CashAndCashEquivalents": "cash",
        "CurrentAssets": "current_assets",
        "TotalAssets": "total_assets",
        "CurrentLiabilities": "current_liab",
        "Liabilities": "total_liab",
        "Equity": "equity",
        "Inventories": "inventories",
        "AccountsReceivableNet": "receivables",
    }
    _CF_TYPES = {
        "CashFlowsFromOperatingActivities": "op_cf",
        "CashProvidedByInvestingActivities": "inv_cf",
        "CashFlowsProvidedFromFinancingActivities": "fin_cf",
        "PropertyAndPlantAndEquipment": "capex",
    }
    _FS_COLS = ["stock_id", "year", "quarter",
                *_BS_TYPES.values(), *_CF_TYPES.values()]

    def fetch_financial_statements(self, stock_id: str, start: date) -> pd.DataFrame:
        """資產負債表（期末餘額）＋現金流量表（單季化）→ 一列一季，金額單位：元。

        兩 dataset 均為長格式（一列一科目）；現金流量為年度累計制，
        逐年由 Q1 差分還原單季（Q1 即累計首季，保留原值）。
        """
        bs = self._data("TaiwanStockBalanceSheet", start, data_id=stock_id)
        cf = self._data("TaiwanStockCashFlowsStatement", start, data_id=stock_id)

        def _pivot(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
            if df.empty or "type" not in df.columns:
                return pd.DataFrame()
            sub = df[df["type"].isin(mapping)]
            if sub.empty:
                return pd.DataFrame()
            wide = sub.pivot_table(index="date", columns="type", values="value", aggfunc="first")
            return wide.rename(columns=mapping)

        out = _pivot(bs, self._BS_TYPES).join(_pivot(cf, self._CF_TYPES), how="outer").reset_index()
        if out.empty:
            return pd.DataFrame(columns=self._FS_COLS)

        dt = pd.to_datetime(out["date"], errors="coerce")
        out["year"] = dt.dt.year
        out["quarter"] = dt.dt.month.map({3: 1, 6: 2, 9: 3, 12: 4})
        out = out.dropna(subset=["year", "quarter"]).astype({"year": int, "quarter": int})
        out = out.sort_values(["year", "quarter"])
        # 現金流量累計 → 單季：同年內與前一季差分；Q1（或該年首見季）保留累計原值
        for col in self._CF_TYPES.values():
            if col in out.columns:
                single = out.groupby("year")[col].diff()
                out[col] = single.where(single.notna(), out[col])
        out["stock_id"] = stock_id
        for col in self._FS_COLS:  # 缺科目（如金融業無存貨）補 None
            if col not in out.columns:
                out[col] = None
        return out[self._FS_COLS]

    # ── NewsProvider（TaiwanStockNews：個股新聞）──
    #
    # 注意：TaiwanStockNews「一次只給一天」（帶 end_date 會 400），且整市場（不帶 data_id）
    # 需付費層；免費(register)層只能「指定個股 + 單日」逐檔抓。故整市場 fetch_events 多半
    # 在免費層失敗（被 NewsEngine/Combined 略過），改走 fetch_stock_events 逐檔。

    @staticmethod
    def _map_news(df: pd.DataFrame) -> pd.DataFrame:
        """TaiwanStockNews 原始欄位 → EVENT_COLS（分類/標利空交給 NewsEngine）。"""
        if df.empty or "stock_id" not in df:
            return pd.DataFrame(columns=schemas.EVENT_COLS)
        out = pd.DataFrame()
        out["stock_id"] = df["stock_id"].astype(str)
        out["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
        out["category"] = None
        out["title"] = df.get("title", "").fillna("").astype(str)
        out["summary"] = None
        out["is_risk"] = False
        out["source"] = df.get("source", "").fillna("").replace("", "新聞")
        out["url"] = df.get("link")
        out = out[out["title"].str.len() > 0]
        return out[schemas.EVENT_COLS]

    def fetch_events(self, start: date, end: date) -> pd.DataFrame:
        """整市場個股新聞（單日，不帶 data_id）。免費層通常被擋 → SourceError 由上層略過。"""
        return self._map_news(self._data("TaiwanStockNews", start))

    def fetch_stock_events(self, stock_id: str, start: date, end: date) -> pd.DataFrame:
        """單一個股新聞：逐日呼叫（TaiwanStockNews 一次一天），合併 [start, end]。

        單日失敗（含無資料/限流）略過該日，不中斷其他日。
        """
        frames: list[pd.DataFrame] = []
        d = end
        while d >= start:
            try:
                raw = self._data("TaiwanStockNews", d, data_id=stock_id)
            except SourceError:
                raw = pd.DataFrame()
            if not raw.empty:
                frames.append(raw)
            d -= timedelta(days=1)
        if not frames:
            return pd.DataFrame(columns=schemas.EVENT_COLS)
        return self._map_news(pd.concat(frames, ignore_index=True))
