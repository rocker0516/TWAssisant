"""TDCC 集保中心來源：集保戶股權分散表（大戶/散戶占比）。

兩條取得路徑：
  1. 開放資料 `opendata.tdcc.com.tw/getOD.ashx?id=1-5`（GET CSV）——**全市場、僅最新一週**。
     供每週 pipeline 一次抓全市場、以 (stock_id,date) upsert 維持最新。
  2. 智慧網單檔查詢 `www.tdcc.com.tw/portal/zh/smWeb/qryStock`（POST HTML）——**單檔、可回溯
     約一年週資料**。供「看哪檔就背景回補哪檔」補歷史曲線（爬蟲、需 CSRF token，易壞，
     故僅在個股頁觸發、不跑全市場）。

原始 17 級分布兩路皆消化成固定欄位（占比/人數），對齊 schemas.HOLDING_COLS：
  持股分級（級距為「股」，1 張=1000 股）—
    1~3   散戶（<10 張）      12~15 大戶（≥400 張）      15 千張大戶（≥1000 張）
    16    差異數調整（捨棄）    17    合計（總股東人數 / 平均持股）
"""

from __future__ import annotations

import io
import re
from datetime import date

import pandas as pd

from .base import BaseSource, SourceError
from .interfaces import HoldingProvider
from . import schemas

_BIG_LEVELS = {12, 13, 14, 15}   # ≥400 張
_OVER1000_LEVEL = 15             # ≥1000 張（千張大戶）
_SMALL_LEVELS = {1, 2, 3}        # <10 張（散戶）
_TOTAL_LEVEL = 17                # 合計列

_SMART_URL = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"
_TOKEN_RE = re.compile(r'name="SYNCHRONIZER_TOKEN"\s+value="([^"]+)"')
_URI_RE = re.compile(r'name="SYNCHRONIZER_URI"\s+value="([^"]+)"')
_OPTION_RE = re.compile(r'<option value="(\d{8})"')
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _num(s: str) -> float:
    """'2,160,807' / '85.42' / '' → float（空字串/非數回 0）。"""
    s = (s or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _digest_one(sid: str, d: date, levels: dict[int, tuple[float, float, float]]) -> dict:
    """單檔單週 17 級 {level: (people, shares, pct)} → 消化後固定欄位列。"""
    big = sum(pct for lv, (_, _, pct) in levels.items() if lv in _BIG_LEVELS)
    over1000 = levels.get(_OVER1000_LEVEL, (0, 0, 0))[2]
    small = sum(pct for lv, (_, _, pct) in levels.items() if lv in _SMALL_LEVELS)
    holders = int(levels.get(_TOTAL_LEVEL, (0, 0, 0))[0])
    tot_shares = levels.get(_TOTAL_LEVEL, (0, 0, 0))[1]
    avg_lots = round(tot_shares / holders / 1000, 2) if holders > 0 else None
    return {
        "stock_id": sid,
        "date": d,
        "big_pct": round(big, 2),
        "over1000_pct": round(over1000, 2),
        "small_pct": round(small, 2),
        "holders": holders or None,
        "avg_lots": avg_lots,
    }


class TdccSource(BaseSource, HoldingProvider):
    name = "tdcc"
    base_url = "https://opendata.tdcc.com.tw"
    requires_token = False

    def _probe(self) -> None:
        if self._fetch_raw().empty:
            raise SourceError("TDCC 股權分散表無資料")

    # ── 開放資料（全市場最新週）──

    def _fetch_raw(self) -> pd.DataFrame:
        """抓 id=1-5 CSV → 原始長表（資料日期/證券代號/持股分級/人數/股數/占比）。"""
        resp = self._request("/getOD.ashx", params={"id": "1-5"})
        text = resp.text
        if not text or "證券代號" not in text:
            raise SourceError("TDCC 回傳非預期格式")
        df = pd.read_csv(io.StringIO(text))
        df.columns = ["date", "stock_id", "level", "people", "shares", "pct"]
        return df

    def fetch_holding_distribution(
        self, start: date, end: date, stock_ids: list[str] | None = None
    ) -> pd.DataFrame:
        df = self._fetch_raw()
        if df.empty:
            return pd.DataFrame(columns=schemas.HOLDING_COLS)

        df["stock_id"] = df["stock_id"].astype(str).str.strip()
        df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d", errors="coerce").dt.date
        df["level"] = pd.to_numeric(df["level"], errors="coerce").astype("Int64")
        df["people"] = pd.to_numeric(df["people"], errors="coerce").fillna(0)
        df["shares"] = pd.to_numeric(df["shares"], errors="coerce").fillna(0)
        df["pct"] = pd.to_numeric(df["pct"], errors="coerce").fillna(0)
        df = df.dropna(subset=["date", "level"])
        if stock_ids:
            df = df[df["stock_id"].isin(set(stock_ids))]

        rows: list[dict] = []
        for (sid, d), g in df.groupby(["stock_id", "date"], sort=False):
            levels = {int(r.level): (r.people, r.shares, r.pct) for r in g.itertuples()}
            rows.append(_digest_one(sid, d, levels))
        if not rows:
            return pd.DataFrame(columns=schemas.HOLDING_COLS)
        return pd.DataFrame(rows)[schemas.HOLDING_COLS]

    # ── 智慧網單檔歷史（回補曲線用，爬蟲）──

    def _smart_get(self) -> tuple[str, str, str, str, list[str]]:
        """GET 查詢頁 → (cookie, token, uri, 可查日期清單[新→舊])。"""
        self._bucket.acquire()
        try:
            r = self._client.get(_SMART_URL)
            r.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise SourceError(f"TDCC 智慧網連線失敗：{exc.__class__.__name__}")
        page = r.text
        tok = _TOKEN_RE.search(page)
        uri = _URI_RE.search(page)
        if not tok:
            raise SourceError("TDCC 智慧網無 CSRF token（頁面改版？）")
        cookie = "; ".join(c.split(";")[0] for c in r.headers.get_list("set-cookie")) or ""
        dates = sorted(set(_OPTION_RE.findall(page)), reverse=True)
        return cookie, tok.group(1), (uri.group(1) if uri else _SMART_URL), dates

    def _smart_post(self, cookie: str, token: str, uri: str, sca_date: str, stock_id: str):
        """POST 單檔單週 → (17 級 dict, 回應內新 token)。無資料回 ({}, token)。"""
        self._bucket.acquire()
        data = {
            "SYNCHRONIZER_TOKEN": token, "SYNCHRONIZER_URI": uri,
            "method": "submit", "firDate": "", "scaDate": sca_date,
            "sqlMethod": "StockNo", "stockNo": stock_id, "stockName": "",
        }
        try:
            r = self._client.post(
                _SMART_URL, data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded", "Cookie": cookie},
            )
            r.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise SourceError(f"TDCC 智慧網查詢失敗：{exc.__class__.__name__}")
        body = r.text
        levels: dict[int, tuple[float, float, float]] = {}
        for tr in _TR_RE.findall(body):
            cells = [_TAG_RE.sub("", c).replace("　", "").strip() for c in _TD_RE.findall(tr)]
            cells = [c for c in cells if c != ""]
            if len(cells) >= 5 and cells[0].isdigit():
                lv = int(cells[0])
                levels[lv] = (_num(cells[2]), _num(cells[3]), _num(cells[4]))
        nt = _TOKEN_RE.search(body)
        return levels, (nt.group(1) if nt else token)

    def fetch_holding_history(self, stock_id: str, max_weeks: int = 52) -> pd.DataFrame:
        """單檔近 max_weeks 週集保歷史（智慧網逐週爬）。失敗的週略過、不中斷其他週。"""
        sid = str(stock_id).strip()
        cookie, token, uri, dates = self._smart_get()
        rows: list[dict] = []
        for sca in dates[:max_weeks]:
            try:
                levels, token = self._smart_post(cookie, token, uri, sca, sid)
            except SourceError:
                continue
            if not levels:
                continue
            d = date(int(sca[:4]), int(sca[4:6]), int(sca[6:8]))
            rows.append(_digest_one(sid, d, levels))
        if not rows:
            return pd.DataFrame(columns=schemas.HOLDING_COLS)
        return pd.DataFrame(rows)[schemas.HOLDING_COLS]
