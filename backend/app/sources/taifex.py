"""TAIFEX 期交所來源（期貨籌碼市場級，全免費、免 token）。

抓兩塊、合併成 market_derivatives 日資料（PK=date）：
  - 台指期三大法人未平倉淨口數：/cht/3/futContractsDateDown（POST，Big5 CSV，
    commodityId=TXF，區間限制保守以 30 天分段）
  - 選擇權 P/C ratio：/cht/3/pcRatioDown（POST，Big5 CSV）

OpenAPI（openapi.taifex.com.tw）僅回最新日/近月快照，無法回補歷史，故走 CSV 下載
（增量與回補同一條路）。定位＝觀察儀表（外資現貨期貨背離）；要折進 regime 閘門
或評分需先過回測（既有定論：籌碼對大盤「方向」無資訊，此處先不做方向宣稱）。
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .base import BaseSource
from . import schemas

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_CHUNK_DAYS = 30  # 下載端點區間上限保守值

# 身份別 → 欄位鍵（歷史欄名「外資及陸資」，新制改「外資」也相容）
_ITEM_KEYS = {
    "自營商": "tx_dealer_oi_net",
    "投信": "tx_trust_oi_net",
    "外資及陸資": "tx_foreign_oi_net",
    "外資": "tx_foreign_oi_net",
}


def _num(v: str) -> float | None:
    s = str(v).replace(",", "").strip()
    if not s or s in ("-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _d(v: str) -> date | None:
    s = str(v).strip()
    try:
        y, m, d = s.split("/")
        return date(int(y), int(m), int(d))
    except (ValueError, AttributeError):
        return None


class TaifexSource(BaseSource):
    name = "taifex"
    base_url = "https://www.taifex.com.tw"
    requires_token = False

    def _auth_headers(self) -> dict[str, str]:
        return {"User-Agent": _UA, "Referer": "https://www.taifex.com.tw/"}

    def _probe(self) -> None:
        self._download_csv("/cht/3/pcRatioDown", date(2024, 1, 2), date(2024, 1, 5))

    # ── CSV 下載共用（Big5、逗號分隔、首列表頭）──

    def _download_csv(self, path: str, start: date, end: date, extra: dict | None = None) -> list[list[str]]:
        data = {
            "queryStartDate": start.strftime("%Y/%m/%d"),
            "queryEndDate": end.strftime("%Y/%m/%d"),
            **(extra or {}),
        }
        resp = self._request(path, data=data)
        text = resp.content.decode("big5", errors="replace")
        rows = [line.split(",") for line in text.splitlines() if line.strip()]
        return rows[1:] if len(rows) > 1 else []  # 去表頭

    def _iter_chunks(self, start: date, end: date):
        d = start
        while d <= end:
            hi = min(d + timedelta(days=_CHUNK_DAYS - 1), end)
            yield d, hi
            d = hi + timedelta(days=1)

    # ── 市場級期貨籌碼（FetchStep._MARKET 直呼，介面同 fetch_institutional_market_total）──

    def fetch_market_derivatives(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        """台指期三大法人未平倉淨口數 + 選擇權 P/C ratio，欄位見 MARKET_DERIVATIVES_COLS。"""
        by_date: dict[date, dict] = {}

        # 1) 台指期三大法人（TXF）：欄 0日期 1商品 2身份別 … 13多空未平倉淨額口數
        for lo, hi in self._iter_chunks(start, end):
            for r in self._download_csv("/cht/3/futContractsDateDown", lo, hi, {"commodityId": "TXF"}):
                if len(r) < 14:
                    continue
                d = _d(r[0])
                key = _ITEM_KEYS.get(str(r[2]).strip())
                if d is None or key is None:
                    continue
                net = _num(r[13])
                if net is not None:
                    by_date.setdefault(d, {})[key] = int(net)

        # 2) P/C ratio：欄 0日期 …3成交量比率% …6未平倉量比率%
        for lo, hi in self._iter_chunks(start, end):
            for r in self._download_csv("/cht/3/pcRatioDown", lo, hi):
                if len(r) < 7:
                    continue
                d = _d(r[0])
                if d is None:
                    continue
                rec = by_date.setdefault(d, {})
                rec["pc_vol_ratio"] = _num(r[3])
                rec["pc_oi_ratio"] = _num(r[6])

        if not by_date:
            return pd.DataFrame(columns=schemas.MARKET_DERIVATIVES_COLS)
        rows = [{"date": d, **v} for d, v in sorted(by_date.items())]
        df = pd.DataFrame(rows)
        for c in schemas.MARKET_DERIVATIVES_COLS:
            if c not in df:
                df[c] = None
        return df[schemas.MARKET_DERIVATIVES_COLS]
