"""注意股／處置股名單（TWSE + TPEX 官方公告，免 token）。

四端點皆支援日期區間查詢（實測 2026-08）：
  - TWSE 注意  /rwd/zh/announcement/notice?startDate&endDate（日期格式 115.08.04）
  - TWSE 處置  /rwd/zh/announcement/punish（處置起迄 115/08/12～115/08/18）
  - TPEX 注意  /www/zh-tw/bulletin/attention?startDate=YYYY/MM/DD&response=json
  - TPEX 處置  /www/zh-tw/bulletin/disposal

名單含權證/ETN 等（如 033945），呼叫端以 universe 股號過濾。
回補時以「月」為查詢單位（官方區間上限約 3 個月，取保守值）。
"""

from __future__ import annotations

import time
from datetime import date

import httpx

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
_TIMEOUT = 15


def _roc(s: str | None) -> date | None:
    """民國日期字串（'115.08.04' / '115/08/12' / '1150812'）→ date。"""
    if not s:
        return None
    t = str(s).strip().replace(".", "").replace("/", "")
    if len(t) < 7 or not t.isdigit():
        return None
    try:
        return date(int(t[:-4]) + 1911, int(t[-4:-2]), int(t[-2:]))
    except ValueError:
        return None


def _roc_range(s: str | None) -> tuple[date | None, date | None]:
    """'115/08/12～115/08/18' → (起, 迄)。分隔符容忍 ～ ~ 〜。"""
    if not s:
        return None, None
    for sep in ("～", "~", "〜"):
        if sep in s:
            a, b = s.split(sep, 1)
            return _roc(a), _roc(b)
    return _roc(s), None


def _int(v) -> int | None:
    try:
        return int(str(v).strip())
    except (ValueError, TypeError):
        return None


def _get_json(url: str, params: dict) -> dict:
    resp = httpx.get(url, params=params, headers=_UA, timeout=_TIMEOUT)
    resp.raise_for_status()
    # 官方回應偶缺 charset 標頭，httpx 會誤判編碼產生亂碼——內容實為 UTF-8，強制指定
    import json as _json
    return _json.loads(resp.content.decode("utf-8", errors="replace"))


def _ymd_twse(d: date) -> str:
    return d.strftime("%Y%m%d")


def _ymd_tpex(d: date) -> str:
    return d.strftime("%Y/%m/%d")


def fetch_range(start: date, end: date, pause: float = 0.4) -> list[dict]:
    """抓四端點的 [start, end] 名單，回正規化 records（kind: notice/punish）。"""
    out: list[dict] = []

    # TWSE 注意：fields 編號,證券代號,證券名稱,累計次數,注意交易資訊,日期,收盤價,本益比
    data = _get_json("https://www.twse.com.tw/rwd/zh/announcement/notice",
                     {"startDate": _ymd_twse(start), "endDate": _ymd_twse(end), "response": "json"})
    for r in data.get("data") or []:
        d = _roc(r[5])
        if d:
            out.append({"stock_id": str(r[1]).strip(), "date": d, "kind": "notice",
                        "times": _int(r[3]), "begin_date": None, "end_date": None,
                        "reason": str(r[4])[:200]})
    time.sleep(pause)

    # TWSE 處置：編號,公布日期,證券代號,證券名稱,累計,處置條件,處置起迄時間,處置措施,處置內容,備註
    data = _get_json("https://www.twse.com.tw/rwd/zh/announcement/punish",
                     {"startDate": _ymd_twse(start), "endDate": _ymd_twse(end), "response": "json"})
    for r in data.get("data") or []:
        d = _roc(r[1])
        if d:
            b, e = _roc_range(r[6])
            out.append({"stock_id": str(r[2]).strip(), "date": d, "kind": "punish",
                        "times": _int(r[4]), "begin_date": b, "end_date": e,
                        "reason": str(r[5])[:200]})
    time.sleep(pause)

    # TPEX 注意：編號,證券代號,證券名稱,累計,注意交易資訊,公告日期,收盤價,本益比,link
    data = _get_json("https://www.tpex.org.tw/www/zh-tw/bulletin/attention",
                     {"startDate": _ymd_tpex(start), "endDate": _ymd_tpex(end), "response": "json"})
    for tb in data.get("tables") or []:
        for r in tb.get("data") or []:
            d = _roc(r[5])
            if d:
                out.append({"stock_id": str(r[1]).strip(), "date": d, "kind": "notice",
                            "times": _int(r[3]), "begin_date": None, "end_date": None,
                            "reason": str(r[4])[:200]})
    time.sleep(pause)

    # TPEX 處置：編號,公布日期,證券代號,證券名稱,累計,處置起訖時間,處置原因,處置內容,收盤價,本益比
    data = _get_json("https://www.tpex.org.tw/www/zh-tw/bulletin/disposal",
                     {"startDate": _ymd_tpex(start), "endDate": _ymd_tpex(end), "response": "json"})
    for tb in data.get("tables") or []:
        for r in tb.get("data") or []:
            d = _roc(r[1])
            if d:
                b, e = _roc_range(r[5])
                out.append({"stock_id": str(r[2]).strip(), "date": d, "kind": "punish",
                            "times": _int(r[4]), "begin_date": b, "end_date": e,
                            "reason": str(r[6])[:200]})

    # 同 (stock, date, kind) 多列（如兩市重複、多條款）取最後一筆
    dedup: dict[tuple, dict] = {}
    for r in out:
        dedup[(r["stock_id"], r["date"], r["kind"])] = r
    return list(dedup.values())
