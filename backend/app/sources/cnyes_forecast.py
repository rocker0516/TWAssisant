"""鉅亨「台股預估」快報 → FactSet 共識目標價。

來源：https://api.cnyes.com/media/api/v1/newslist/category/tw_forecast
每則快報內文固定格式，regex 抽中位數/最高低/人數/評級分布。
抓失敗 graceful 回空（SourceError 由呼叫端吞），不可拖垮 pipeline。
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime

from .base import BaseSource

_CATEGORY_URL = "https://api.cnyes.com/media/api/v1/newslist/category/tw_forecast"

_RE_STOCK = re.compile(r"[（(](\d{4,6})-TW[)）]")
_NUM = r"(-?\d+(?:,\d{3})*(?:\.\d+)?)"
# 目標價型內文：「提出目標價估值：中位數由665元下修至640元」／「中位數維持640元」
_RE_TP_REVISE = re.compile(rf"目標價估值：中位數由{_NUM}元(上修|下修)至{_NUM}元")
_RE_TP_KEEP = re.compile(rf"目標價估值：中位數(?:維持|為){_NUM}元")
# EPS 型：目標價只在「預估目標價為80元」出現（標題與內文皆可）
_RE_TP_PLAIN = re.compile(rf"預估目標價為{_NUM}元")
_RE_HILO = re.compile(rf"最高估值{_NUM}元，最低估值{_NUM}元")
_RE_COUNT = re.compile(r"共(?:有)?(\d+)位分析師")
_RE_RATING = re.compile(r"積極樂觀(\d+)位、保持中立(\d+)位、保守悲觀(\d+)位")
_RE_EPS = re.compile(rf"EPS預估(?:上修|下修)至{_NUM}元")


def _f(s: str) -> float:
    return float(s.replace(",", ""))


def parse_forecast_item(title: str, content: str, publish_date: date) -> dict | None:
    """單則快報 → target_prices row（不含 news_id，由呼叫端補）。非台股或無目標價回 None。"""
    m_stock = _RE_STOCK.search(title)
    if not m_stock:
        return None
    text = re.sub(r"<[^>]+>", " ", html.unescape(content or ""))

    target = prev = None
    direction = "new"
    m = _RE_TP_REVISE.search(text)
    if m:
        prev, target = _f(m.group(1)), _f(m.group(3))
        direction = "up" if m.group(2) == "上修" else "down"
    else:
        m = _RE_TP_KEEP.search(text)
        if m:
            target = _f(m.group(1))
            direction = "flat"
        else:
            m = _RE_TP_PLAIN.search(text) or _RE_TP_PLAIN.search(title)
            if m:
                target = _f(m.group(1))
    if target is None:
        return None

    hilo = _RE_HILO.search(text)
    cnt = _RE_COUNT.search(text)
    rating = _RE_RATING.search(text)
    eps = _RE_EPS.search(title) or _RE_EPS.search(text)
    # EPS 型內文的「最高/最低估值」是 EPS 區間，不是目標價區間 → 只有目標價型才收
    is_tp_body = "目標價估值" in text
    return {
        "stock_id": m_stock.group(1),
        "date": publish_date,
        "target_price": target,
        "prev_target": prev,
        "direction": direction,
        "target_high": _f(hilo.group(1)) if hilo and is_tp_body else None,
        "target_low": _f(hilo.group(2)) if hilo and is_tp_body else None,
        "analyst_count": int(cnt.group(1)) if cnt else None,
        "rating_bull": int(rating.group(1)) if rating else None,
        "rating_neutral": int(rating.group(2)) if rating else None,
        "rating_bear": int(rating.group(3)) if rating else None,
        "eps_est": _f(eps.group(1)) if eps else None,
        "title": title,
    }


class CnyesForecastSource(BaseSource):
    name = "cnyes_forecast"
    requires_token = False

    def _probe(self) -> None:
        self._request(_CATEGORY_URL, {"page": 1, "limit": 1})

    def fetch_target_prices(self, known_ids: set[int], min_date: date) -> list[dict]:
        """翻頁抓快報：整頁 news_id 皆已存在（增量到頂）或最舊 < min_date（回補到底）即停。"""
        rows: list[dict] = []
        page = 1
        while True:
            j = self._request(_CATEGORY_URL, {"page": page, "limit": 30}).json()
            data = (j.get("items") or {}).get("data") or []
            if not data:
                break
            all_known = True
            oldest: date | None = None
            for it in data:
                nid = it.get("newsId")
                ts = it.get("publishAt")
                if nid is None or ts is None:
                    continue
                d = datetime.fromtimestamp(ts).date()
                oldest = d if oldest is None or d < oldest else oldest
                if nid in known_ids:
                    continue
                all_known = False
                parsed = parse_forecast_item(it.get("title") or "", it.get("content") or "", d)
                if parsed:
                    parsed["news_id"] = nid
                    rows.append(parsed)
            if all_known or (oldest is not None and oldest < min_date):
                break
            page += 1
        return rows
