"""CNN Fear & Greed（美股官方情緒指數）直抓。

端點：production.dataviz.cnn.io/index/fearandgreed/graphdata（CNN 官網同源資料）。
需帶瀏覽器 UA 否則回 418。回傳現值 + 前收/週/月/年比較 + 約一年歷史。
行程內快取 6 小時；抓失敗回 None（前端隱藏美股區塊，不影響台股自算）。

注意：CNN 指數衡量的是「美股」情緒（7 組件：動能/強度/廣度/PC比/垃圾債利差/
避險需求/VIX），台股情緒仍以本站自算組件為準，兩者並列互為參照。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.cnn.com/",
    "Origin": "https://www.cnn.com",
}
_TTL = 6 * 3600

_RATING_ZH = {
    "extreme fear": "極度恐懼",
    "fear": "恐懼",
    "neutral": "中性",
    "greed": "貪婪",
    "extreme greed": "極度貪婪",
}

_cache: dict = {}


def fetch_cnn_fear_greed() -> dict | None:
    """回 {score, rating, label, prev_close, prev_week, prev_month, prev_year, history} 或 None。"""
    now = time.time()
    if _cache.get("at", 0) > now - _TTL:
        return _cache["value"]

    try:
        resp = httpx.get(_URL, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        fg = data["fear_and_greed"]
        hist_raw = (data.get("fear_and_greed_historical") or {}).get("data") or []
        # 一年約 250 點 → 隔點取樣壓到 ~125 點，尾點必留
        step = max(1, len(hist_raw) // 125)
        picked = hist_raw[::step]
        if hist_raw and picked[-1] is not hist_raw[-1]:
            picked.append(hist_raw[-1])
        history = [
            {
                "date": datetime.fromtimestamp(p["x"] / 1000, tz=timezone.utc).date(),
                "score": round(float(p["y"]), 1),
            }
            for p in picked
            if p.get("x") and p.get("y") is not None
        ]
        rating = str(fg.get("rating", "")).lower()
        value = {
            "score": round(float(fg["score"]), 1),
            "rating": rating,
            "label": _RATING_ZH.get(rating, rating),
            "prev_close": round(float(fg["previous_close"]), 1) if fg.get("previous_close") is not None else None,
            "prev_week": round(float(fg["previous_1_week"]), 1) if fg.get("previous_1_week") is not None else None,
            "prev_month": round(float(fg["previous_1_month"]), 1) if fg.get("previous_1_month") is not None else None,
            "prev_year": round(float(fg["previous_1_year"]), 1) if fg.get("previous_1_year") is not None else None,
            "history": history,
        }
    except Exception:
        value = None  # 網路/格式異常：隱藏美股區塊即可，不拋錯

    # 失敗也快取（短一點），避免每次請求都重試逾時
    _cache["at"] = now if value is not None else now - _TTL + 600
    _cache["value"] = value
    return value


__all__ = ["fetch_cnn_fear_greed"]
