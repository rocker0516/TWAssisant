"""TWSE 證交所官方開放資料來源（上市，全免費、免 token、全市場 by-date）。

取代 FinMind 免費層被擋的「全市場 by-date」需求（FinMind 全市場需付費 Sponsor）。
端點皆「每個日期回一天」，故各方法內部以「逐交易日迴圈」涵蓋區間；每日增量
= 1 請求。實測欄位（2026-06）：
  - 行情   MI_INDEX  type=ALLBUT0999  → tables 中「每日收盤行情」表
  - 法人   T86       selectType=ALLBUT0999 → 平面 fields/data
  - 融資券 MI_MARGN  selectType=ALL   → tables 中「融資融券彙總」表（欄位重名，用位置）

上櫃（TPEX, www.tpex.org.tw）格式不同，P0 先做上市；TPEX 之後加成 sibling 來源
（實作同介面、binding 不動）。
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .base import BaseSource, SourceError
from .interfaces import ChipProvider, FundamentalProvider, NewsProvider, PriceProvider
from . import schemas

_OPENAPI = "https://openapi.twse.com.tw/v1"


def _roc_ym(s: str) -> tuple[int | None, int | None]:
    """民國年月字串 '11504' → (2026, 4)。"""
    s = str(s).strip()
    if len(s) < 4 or not s.isdigit():
        return None, None
    return int(s[:-2]) + 1911, int(s[-2:])


def _roc_date(s: str) -> date | None:
    """民國日期 '1150527' → date(2026,5,27)。"""
    s = str(s).strip().replace("/", "")
    if len(s) < 7 or not s.isdigit():
        return None
    try:
        return date(int(s[:-4]) + 1911, int(s[-4:-2]), int(s[-2:]))
    except ValueError:
        return None

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _num(v) -> float | None:
    """TWSE 數字字串 → float。逗號、空白、'--'、'' 等視為 None。"""
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "--", "-", "X", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _cell(row: list, i: int | None):
    """邊界安全取值：歷史資料偶有殘缺/較短列，越界回 None 而非炸掉。"""
    if i is None or i < 0 or i >= len(row):
        return None
    return row[i]


class TwseSource(BaseSource, PriceProvider, ChipProvider, FundamentalProvider, NewsProvider):
    name = "twse"
    base_url = "https://www.twse.com.tw"
    requires_token = False

    def _auth_headers(self) -> dict[str, str]:
        return {"User-Agent": _UA}

    def _probe(self) -> None:
        # 用最近交易日附近探測（MI_INDEX 有資料即視為連線正常）
        self._day_json("/exchangeReport/MI_INDEX", date(2024, 1, 2), {"type": "ALLBUT0999"})

    # ── 共用：抓單日 JSON ──

    def _day_json(self, path: str, d: date, extra: dict) -> dict | None:
        params = {"response": "json", "date": d.strftime("%Y%m%d"), **extra}
        resp = self._request(path, params=params)
        body = resp.json()
        if body.get("stat") != "OK":
            return None  # 假日 / 無資料
        return body

    @staticmethod
    def _pick_table(body: dict, keyword: str) -> dict | None:
        for t in body.get("tables", []):
            if keyword in (t.get("title") or "") and t.get("data"):
                return t
        return None

    def _iter_days(self, start: date, end: date):
        d = start
        while d <= end:
            if d.weekday() < 5:  # 跳週末；假日靠 stat!=OK 過濾
                yield d
            d += timedelta(days=1)

    # ── PriceProvider ──

    def fetch_prices(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        rows: list[dict] = []
        for d in self._iter_days(start, end):
            body = self._day_json("/exchangeReport/MI_INDEX", d, {"type": "ALLBUT0999"})
            if not body:
                continue
            table = self._pick_table(body, "每日收盤行情")
            if not table:
                continue
            idx = {name: i for i, name in enumerate(table["fields"])}
            for r in table["data"]:
                sid = _cell(r, idx.get("證券代號"))
                if sid is None:
                    continue
                rows.append(
                    {
                        "stock_id": str(sid).strip(),
                        "date": d,
                        "open": _num(_cell(r, idx.get("開盤價"))),
                        "high": _num(_cell(r, idx.get("最高價"))),
                        "low": _num(_cell(r, idx.get("最低價"))),
                        "close": _num(_cell(r, idx.get("收盤價"))),
                        "volume": _num(_cell(r, idx.get("成交股數"))),
                        "turnover": _num(_cell(r, idx.get("成交金額"))),
                    }
                )
        if not rows:
            return pd.DataFrame(columns=schemas.PRICE_COLS)
        df = pd.DataFrame(rows)
        df["volume"] = df["volume"].astype("Int64")
        return df[schemas.PRICE_COLS]

    # ── ChipProvider ──

    def fetch_institutional(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        rows: list[dict] = []
        for d in self._iter_days(start, end):
            body = self._day_json("/fund/T86", d, {"selectType": "ALLBUT0999"})
            if not body or not body.get("data"):
                continue
            idx = {name: i for i, name in enumerate(body["fields"])}

            def g(r, key, _idx=idx):  # noqa: ANN001
                return _num(_cell(r, _idx.get(key)))

            for r in body["data"]:
                sid = _cell(r, idx.get("證券代號"))
                if sid is None:
                    continue
                foreign = (g(r, "外陸資買賣超股數(不含外資自營商)") or 0) + (
                    g(r, "外資自營商買賣超股數") or 0
                )
                trust = g(r, "投信買賣超股數") or 0
                dealer = g(r, "自營商買賣超股數") or 0
                total = g(r, "三大法人買賣超股數")
                rows.append(
                    {
                        "stock_id": str(sid).strip(),
                        "date": d,
                        "foreign_net": round(foreign / 1000),  # 股 → 張
                        "trust_net": round(trust / 1000),
                        "dealer_net": round(dealer / 1000),
                        "total_net": round((total if total is not None else foreign + trust + dealer) / 1000),
                    }
                )
        if not rows:
            return pd.DataFrame(columns=schemas.INSTITUTIONAL_COLS)
        df = pd.DataFrame(rows)
        for c in ("foreign_net", "trust_net", "dealer_net", "total_net"):
            df[c] = df[c].astype("Int64")
        return df[schemas.INSTITUTIONAL_COLS]

    def fetch_margin(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        # MI_MARGN 欄位重名（融資/融券各有 買進/賣出/今日餘額），用位置索引：
        # 0代號 1名稱 | 2買 3賣 4現償 5前餘 6今餘 7限額(融資) | 8買 9賣 10券償 11前餘 12今餘 13限額(融券) | 14資券互抵 15註記
        rows: list[dict] = []
        for d in self._iter_days(start, end):
            body = self._day_json("/exchangeReport/MI_MARGN", d, {"selectType": "ALL"})
            if not body:
                continue
            table = self._pick_table(body, "融資融券彙總")
            if not table:
                continue
            for r in table["data"]:
                sid = _cell(r, 0)
                if sid is None:
                    continue
                margin_today, margin_prev = _num(_cell(r, 6)), _num(_cell(r, 5))
                short_today, short_prev = _num(_cell(r, 12)), _num(_cell(r, 11))
                rows.append(
                    {
                        "stock_id": str(sid).strip(),
                        "date": d,
                        "margin_balance": margin_today,
                        "margin_change": (margin_today - margin_prev)
                        if margin_today is not None and margin_prev is not None
                        else None,
                        "short_balance": short_today,
                        "short_change": (short_today - short_prev)
                        if short_today is not None and short_prev is not None
                        else None,
                    }
                )
        if not rows:
            return pd.DataFrame(columns=schemas.MARGIN_COLS)
        df = pd.DataFrame(rows)
        for c in ("margin_balance", "margin_change", "short_balance", "short_change"):
            df[c] = df[c].astype("Int64")
        return df[schemas.MARGIN_COLS]

    # ── 市場級彙總（全市場三大法人總表 + 加權指數，皆 by date 逐日，無 stock_id）──

    def fetch_institutional_market_total(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        """全市場三大法人買賣超總表（BFI82U，買賣差額）。單位元→億元（float）。

        欄位見 schemas.INSTITUTIONAL_MARKET_COLS。無 stock_id（PK=date）。
        歷史早期自營商可能未拆「自行買賣/避險」，缺欄時回退合計「自營商」。
        """
        e8 = 1e8
        rows: list[dict] = []
        for d in self._iter_days(start, end):
            params = {"response": "json", "dayDate": d.strftime("%Y%m%d"), "type": "day"}
            body = self._request("/fund/BFI82U", params=params).json()
            if body.get("stat") != "OK" or not body.get("data"):
                continue
            vals: dict[str, float] = {}
            for r in body["data"]:
                name = str(_cell(r, 0) or "").strip()
                vals[name] = _num(_cell(r, 3)) or 0.0  # 買賣差額
            foreign = vals.get("外資及陸資(不含外資自營商)", 0.0) + vals.get("外資自營商", 0.0)
            if "外資及陸資(不含外資自營商)" not in vals:
                foreign = vals.get("外資及陸資", 0.0) + vals.get("外資自營商", 0.0)
            trust = vals.get("投信", 0.0)
            if "自營商(自行買賣)" in vals or "自營商(避險)" in vals:
                dealer = vals.get("自營商(自行買賣)", 0.0) + vals.get("自營商(避險)", 0.0)
            else:
                dealer = vals.get("自營商", 0.0)
            total = vals.get("合計")
            if total is None:
                total = foreign + trust + dealer
            rows.append({
                "date": d,
                "foreign_net": round(foreign / e8, 2),
                "trust_net": round(trust / e8, 2),
                "dealer_net": round(dealer / e8, 2),
                "total_net": round(total / e8, 2),
            })
        if not rows:
            return pd.DataFrame(columns=schemas.INSTITUTIONAL_MARKET_COLS)
        return pd.DataFrame(rows)[schemas.INSTITUTIONAL_MARKET_COLS]

    def fetch_index(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        """加權指數日線（MI_INDEX「價格指數(臺灣證券交易所)」表中『發行量加權股價指數』收盤）。

        欄位見 schemas.MARKET_INDEX_COLS。無 stock_id（PK=date）。
        """
        rows: list[dict] = []
        for d in self._iter_days(start, end):
            body = self._day_json("/exchangeReport/MI_INDEX", d, {"type": "ALLBUT0999"})
            if not body:
                continue
            table = self._pick_table(body, "價格指數(臺灣證券交易所)")
            if not table:
                continue
            idx = {name: i for i, name in enumerate(table["fields"])}
            for r in table["data"]:
                if str(_cell(r, idx.get("指數")) or "").strip() == "發行量加權股價指數":
                    close = _num(_cell(r, idx.get("收盤指數")))
                    if close is not None:
                        rows.append({"date": d, "close": close})
                    break
        if not rows:
            return pd.DataFrame(columns=schemas.MARKET_INDEX_COLS)
        return pd.DataFrame(rows)[schemas.MARKET_INDEX_COLS]

    # ── FundamentalProvider（TWSE 全市場免費）──

    def fetch_valuation(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        """BWIBBU_d：本益比 / 殖利率 / 股價淨值比，by date 逐日。"""
        rows: list[dict] = []
        for d in self._iter_days(start, end):
            body = self._day_json("/exchangeReport/BWIBBU_d", d, {"selectType": "ALL"})
            if not body or not body.get("data"):
                continue
            idx = {name: i for i, name in enumerate(body["fields"])}
            for r in body["data"]:
                sid = _cell(r, idx.get("證券代號"))
                if sid is None:
                    continue
                rows.append(
                    {
                        "stock_id": str(sid).strip(),
                        "date": d,
                        "pe": _num(_cell(r, idx.get("本益比"))),
                        "pb": _num(_cell(r, idx.get("股價淨值比"))),
                        "dividend_yield": _num(_cell(r, idx.get("殖利率(%)"))),
                    }
                )
        if not rows:
            return pd.DataFrame(columns=schemas.VALUATION_COLS)
        return pd.DataFrame(rows)[schemas.VALUATION_COLS]

    def fetch_revenue_monthly(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        """月營收彙總（openapi 最新月快照，含 YoY/MoM）。日期參數忽略，靠 upsert 去重。"""
        resp = self._request(f"{_OPENAPI}/opendata/t187ap05_L")
        data = resp.json()
        rows: list[dict] = []
        for r in data:
            year, month = _roc_ym(r.get("資料年月", ""))
            if year is None:
                continue
            rows.append(
                {
                    "stock_id": str(r.get("公司代號", "")).strip(),
                    "year": year,
                    "month": month,
                    "revenue": _num(r.get("營業收入-當月營收")),
                    "yoy": _num(r.get("營業收入-去年同月增減(%)")),
                    "mom": _num(r.get("營業收入-上月比較增減(%)")),
                }
            )
        if not rows:
            return pd.DataFrame(columns=schemas.REVENUE_COLS)
        return pd.DataFrame(rows)[schemas.REVENUE_COLS]

    def fetch_financials(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        """季財報（MOPS 累計制→單季，含 EPS）。

        原 openapi t187ap17_L 為「累計」快照：Q2 起會把上半年累計率當單季寫庫，
        污染長線軌的單季成長/利潤率趨勢因子 → 改走 MOPS 彙總表差分還原單季。
        """
        from . import mops  # 延遲匯入避免循環

        return mops.fetch_recent_financials(markets=("sii",), today=end)

    # ── ETF 身分資料（基金基本資料彙總表 t187ap47_L，openapi 全快照）──

    def fetch_etf_profiles(
        self, start: date | None = None, end: date | None = None,
        stock_ids: list[str] | None = None,
    ) -> pd.DataFrame:
        """ETF 基金基本資料（追蹤指數/類型/含國外成分/發行單位數）。日期參數忽略（全快照）。"""
        rows: list[dict] = []
        for r in self._openapi_list("/opendata/t187ap47_L"):
            sid = str(r.get("基金代號", "")).strip()
            if not sid:
                continue
            idx = (r.get("標的指數/追蹤指數名稱") or "").strip()
            foreign = (r.get("是否包含國外成分股") or "").strip()
            rows.append({
                "stock_id": sid,
                "fund_type": (r.get("基金類型") or "").strip() or None,
                "track_index": None if idx in ("", "不適用") else idx,
                "has_foreign": True if foreign == "是" else False if foreign == "否" else None,
                "units": _num(r.get("發行單位數/轉換數")),
                "etf_listed_date": _roc_date(r.get("上市日期") or ""),
            })
        if not rows:
            return pd.DataFrame(columns=schemas.ETF_PROFILE_COLS)
        return pd.DataFrame(rows)[schemas.ETF_PROFILE_COLS]

    # ── NewsProvider（重大訊息 + 處置股，皆 TWSE OpenAPI 免費）──

    def fetch_events(self, start: date, end: date) -> pd.DataFrame:
        """每日重大訊息(t187ap04_L 當日快照) + 處置股(punish)。

        分類/標利空交給 NewsEngine；此處只回原始事件。日期參數忽略（皆當日/最新）。
        """
        rows: list[dict] = []
        rows.extend(self._fetch_material())
        rows.extend(self._fetch_punish())
        if not rows:
            return pd.DataFrame(columns=schemas.EVENT_COLS)
        return pd.DataFrame(rows)[schemas.EVENT_COLS]

    def _openapi_list(self, path: str) -> list[dict]:
        resp = self._request(f"{_OPENAPI}{path}")
        data = resp.json()
        return data if isinstance(data, list) else []

    def _fetch_material(self) -> list[dict]:
        out: list[dict] = []
        for r in self._openapi_list("/opendata/t187ap04_L"):
            sid = (r.get("公司代號") or "").strip()
            title = r.get("主旨") or r.get("標題") or ""
            d = _roc_date(r.get("事實發生日") or r.get("發言日期") or r.get("出表日期") or "")
            if not sid or not title:
                continue
            out.append({
                "stock_id": sid, "date": d, "category": None, "title": title,
                "summary": r.get("說明") or r.get("符合條款"), "is_risk": False,
                "source": "重訊", "url": None,
            })
        return out

    def _fetch_punish(self) -> list[dict]:
        out: list[dict] = []
        for r in self._openapi_list("/announcement/punish"):
            sid = (r.get("Code") or "").strip()
            if not sid:
                continue
            out.append({
                "stock_id": sid, "date": _roc_date(r.get("Date") or ""),
                "category": "處置警示",
                "title": f"處置股票：{r.get('ReasonsOfDisposition', '')}".strip("："),
                "summary": (r.get("Detail") or "")[:500], "is_risk": True,
                "source": "處置", "url": r.get("LinkInformation"),
            })
        return out
