"""MOPS 公開資訊觀測站「歷史彙總」資料（月營收 / 季財報，上市+上櫃通用）。

openapi 的 t187ap05/t187ap17 只回最新期快照；長線軌（釣大魚）的成長持續性、
加速度、利潤率趨勢需要多期歷史 → 從 MOPS 舊版彙總檔補：
  - 月營收   GET  /nas/t21/{sii|otc}/t21sc03_{民國年}_{月}_{0|1}.html（Big5；0=國內 1=KY）
  - 損益彙總 POST /mops/web/ajax_t163sb04（EPS；金融業亦有，各產業分表、EPS 皆在最後一欄）
  - 營益分析 POST /mops/web/ajax_t163sb06（毛利率/營益率/稅後純益率；金融保險業不適用故缺席）

**季表為累計制**（season=02 顯示上半年累計）→ single_quarter() 差分還原單季：
EPS 直接相減；利潤率用「累計利潤額差 ÷ 累計營收差」還原單季率。
金融股只有 EPS、利潤率 None（ScoreRule 對 None 類別本就會重分配權重）。
"""

from __future__ import annotations

import re
import time
from datetime import date

import httpx
import pandas as pd

from . import schemas
from .base import SourceError

_BASE = "https://mopsov.twse.com.tw"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_MIN_INTERVAL = 1.0  # MOPS 無明示限流，比照 TWSE 1 req/s 禮貌節流
_MARKETS = ("sii", "otc")  # 上市 / 上櫃

_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_TH = re.compile(r"<th[^>]*>(.*?)</th>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_CODE = re.compile(r"\d{4,6}[A-Z]?")


def _num(v) -> float | None:
    """MOPS 數字字串 → float。逗號、'--'、'-'、空字串視為 None（同 twse._num，避免循環匯入自帶一份）。"""
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "--", "-", "X", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _text(cell: str) -> str:
    return _TAG.sub("", cell).replace("&nbsp;", " ").strip()


def _rows(html: str) -> list[list[str]]:
    """所有 <tr> 的 <td> 文字，只留首欄是股號的資料列（自動略過表頭/合計列）。"""
    out = []
    for tr in _TR.findall(html):
        cells = [_text(c) for c in _TD.findall(tr)]
        if cells and _CODE.fullmatch(cells[0]):
            out.append(cells)
    return out


class MopsClient:
    """輕量 MOPS 客戶端（非 BaseSource：需要 POST 且無能力綁定，回補腳本與來源共用）。"""

    def __init__(self) -> None:
        # follow_redirects：MOPS 偶發 307 轉址（限流/主機切換），不跟隨會整批請求報錯
        self._client = httpx.Client(
            timeout=30.0, headers={"User-Agent": _UA}, follow_redirects=True
        )
        self._last = 0.0

    def _throttle(self) -> None:
        wait = self._last + _MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def _fetch(self, method: str, path: str, data: dict | None = None) -> str | None:
        """回文字內容；404 回 None（該期檔案不存在，如 KY 檔早年缺）；其餘錯誤重試後拋。"""
        last: Exception | None = None
        for attempt in range(6):
            self._throttle()
            try:
                if method == "GET":
                    resp = self._client.get(f"{_BASE}{path}")
                else:
                    resp = self._client.post(f"{_BASE}{path}", data=data)
                if resp.status_code == 404:
                    return None
                if resp.status_code == 307:
                    # MOPS WAF 節流訊號（無 Location 的 307，follow_redirects 也跟不了）：
                    # 快速重試只會被繼續擋，改長退避讓對方冷卻
                    last = SourceError(f"MOPS {path} 節流（307）")
                    time.sleep(15.0 * (attempt + 1))
                    continue
                resp.raise_for_status()
                # nas 舊檔為 Big5；ajax 端點為 UTF-8（httpx 依 header 自動判斷，nas 無 charset 需手動）
                if "/nas/" in path:
                    return resp.content.decode("big5", errors="replace")
                return resp.text
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last = exc
                time.sleep(2**attempt)
        raise SourceError(f"MOPS {path} 失敗：{last}")

    # ── 月營收 ──

    def revenue_month(self, year: int, month: int, markets: tuple[str, ...] = _MARKETS) -> pd.DataFrame:
        """單一年月、全市場月營收（千元）。欄位同 REVENUE_COLS。"""
        rows: list[dict] = []
        for mk in markets:
            for kind in (0, 1):  # 0=國內 1=KY
                html = self._fetch("GET", f"/nas/t21/{mk}/t21sc03_{year - 1911}_{month}_{kind}.html")
                if html is None:
                    continue
                for c in _rows(html):
                    # [代號, 名稱, 當月營收, 上月營收, 去年當月, 上月增減%, 去年同月增減%, 累計, 去年累計, 累計增減%, 備註]
                    if len(c) < 7:
                        continue
                    rows.append({
                        "stock_id": c[0], "year": year, "month": month,
                        "revenue": _num(c[2]), "mom": _num(c[5]), "yoy": _num(c[6]),
                    })
        return pd.DataFrame(rows)[schemas.REVENUE_COLS] if rows else pd.DataFrame(columns=schemas.REVENUE_COLS)

    # ── 董監持股（單檔單月歷史）──

    def insider_month(self, co_id: str, year: int, month: int) -> dict | None:
        """單檔單月董監事持股餘額明細（ajax_stapap1，isnew=false 可回溯全歷史）。

        逐列（每列一席，含經理人；口徑對齊 twse._digest_insider＝openapi t187ap11
        全列直加，已以 2330 11507 期比對分毫不差）加總 → INSIDER_COLS 單列 dict。
        該月無資料（未上市/停售）回 None。
        """
        html = self._fetch("POST", "/mops/web/ajax_stapap1", data={
            "encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1",
            "queryName": "co_id", "inpuType": "co_id", "TYPEK": "all",
            "isnew": "false", "co_id": str(co_id),
            "year": str(year - 1911), "month": f"{month:02d}",
        })
        if not html:
            return None
        held_sum = pledged_sum = 0.0
        n = 0
        for tr in _TR.findall(html):
            cells = [_text(c) for c in _TD.findall(tr)]
            # 資料列：[職稱, 姓名, 選任時持股, 目前持股, 設質股數, 設質%, 配偶目前持股, 設質股數, 設質%]
            if len(cells) < 9 or _CODE.fullmatch(cells[0]):
                continue
            held = _num(cells[3])
            if held is None:
                continue
            held_sum += held
            pledged_sum += _num(cells[4]) or 0.0
            n += 1
        if n == 0:
            return None
        return {
            "stock_id": str(co_id), "year": year, "month": month,
            "director_shares": held_sum,
            "pledge_pct": round(pledged_sum / held_sum * 100, 2) if held_sum > 0 else None,
            "positions": n,
        }

    # ── 季財報（累計制）──

    def financials_cumulative(self, year: int, quarter: int, markets: tuple[str, ...] = _MARKETS) -> pd.DataFrame:
        """單一季、全市場**累計**財報：revenue(千元)/毛利率/營益率/稅後純益率/EPS。

        欄位同 FINANCIAL_COLS（值為累計制，需經 single_quarter() 還原單季再落庫）。
        """
        merged: dict[str, dict] = {}
        form = {
            "encodeURIComponent": 1, "step": 1, "firstin": 1, "off": 1, "isQuery": "Y",
            "year": year - 1911, "season": f"{quarter:02d}",
        }
        for mk in markets:
            data = {**form, "TYPEK": mk}
            # 營益分析：固定 7 欄 [代號, 名稱, 營收(百萬), 毛利率, 營益率, 稅前純益率, 稅後純益率]
            for c in _rows(self._fetch("POST", "/mops/web/ajax_t163sb06", data) or ""):
                if len(c) != 7:
                    continue
                rev_m = _num(c[2])
                merged[c[0]] = {
                    "revenue": rev_m * 1000 if rev_m is not None else None,
                    "gross_margin": _num(c[3]), "op_margin": _num(c[4]), "net_margin": _num(c[6]),
                }
            # 損益彙總：各產業分表欄數不同，但代號皆首欄、基本每股盈餘皆末欄
            html04 = self._fetch("POST", "/mops/web/ajax_t163sb04", data) or ""
            for tbl in re.split(r"<table", html04, flags=re.I):
                ths = [_text(h) for h in _TH.findall(tbl)]
                if not ths or "公司代號" not in ths[0] or "每股盈餘" not in ths[-1]:
                    continue
                for c in _rows(tbl):
                    merged.setdefault(c[0], {})["eps"] = _num(c[-1])

        rows = [
            {"stock_id": sid, "year": year, "quarter": quarter,
             "eps": v.get("eps"), "revenue": v.get("revenue"),
             "gross_margin": v.get("gross_margin"), "op_margin": v.get("op_margin"),
             "net_margin": v.get("net_margin"), "roe": None}
            for sid, v in merged.items()
        ]
        return pd.DataFrame(rows)[schemas.FINANCIAL_COLS] if rows else pd.DataFrame(columns=schemas.FINANCIAL_COLS)


def single_quarter(cum: pd.DataFrame, prev_cum: pd.DataFrame | None) -> pd.DataFrame:
    """累計制 → 單季：EPS/營收相減；利潤率=(累計利潤額差)/(累計營收差)。

    Q1（prev_cum=None）累計即單季。只在 cum 出現、prev 缺席的個股（年中新掛牌/
    前期未申報）沿用累計值當單季（最佳可得近似）。
    """
    if prev_cum is None or prev_cum.empty or cum.empty:
        return cum
    prev = prev_cum.set_index("stock_id")
    out = cum.copy()

    def _one(row: pd.Series) -> pd.Series:
        if row["stock_id"] not in prev.index:
            return row
        p = prev.loc[row["stock_id"]]
        rev_c, rev_p = row["revenue"], p["revenue"]
        rev_q = rev_c - rev_p if pd.notna(rev_c) and pd.notna(rev_p) else None
        for col in ("gross_margin", "op_margin", "net_margin"):
            m_c, m_p = row[col], p[col]
            if rev_q and rev_q > 0 and pd.notna(m_c) and pd.notna(m_p) and pd.notna(rev_c) and pd.notna(rev_p):
                profit_q = m_c / 100 * rev_c - m_p / 100 * rev_p
                row[col] = round(profit_q / rev_q * 100, 2)
            else:
                row[col] = None
        if pd.notna(row["eps"]) and pd.notna(p["eps"]):
            row["eps"] = round(row["eps"] - p["eps"], 2)
        row["revenue"] = rev_q
        return row

    return out.apply(_one, axis=1)


def due_quarters(today: date, n: int = 2) -> list[tuple[int, int]]:
    """最近 n 個「已結束」的季度，新→舊。7/2 → [(2026,2), (2026,1)]（Q2 早鳥已可申報）。"""
    y, q = today.year, (today.month - 1) // 3  # 上一個結束的季
    if q == 0:
        y, q = y - 1, 4
    out = []
    for _ in range(n):
        out.append((y, q))
        y, q = (y - 1, 4) if q == 1 else (y, q - 1)
    return out


_client: MopsClient | None = None


def client() -> MopsClient:
    global _client
    if _client is None:
        _client = MopsClient()
    return _client


def fetch_recent_financials(markets: tuple[str, ...], today: date | None = None) -> pd.DataFrame:
    """給每日排程用：抓最近 2 個已結束季度的**單季**財報（涵蓋申報期陸續出爐+遲交者）。

    每季差分需要再前一季的累計 → 實際抓 3 期累計、產出 2 期單季。
    """
    today = today or date.today()
    quarters = due_quarters(today, n=3)  # 新→舊，最舊那期只當差分基底
    c = client()
    cums = {yq: c.financials_cumulative(*yq, markets=markets) for yq in quarters}
    frames = []
    for (y, q) in quarters[:2]:
        cum = cums[(y, q)]
        if cum.empty:
            continue  # 申報期未開始/無資料
        prev = cums.get((y, q - 1)) if q > 1 else None  # Q1 無需差分
        frames.append(single_quarter(cum, prev))
    if not frames:
        return pd.DataFrame(columns=schemas.FINANCIAL_COLS)
    return pd.concat(frames, ignore_index=True)[schemas.FINANCIAL_COLS]
