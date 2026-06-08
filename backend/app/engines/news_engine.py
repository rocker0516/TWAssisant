"""NewsEngine（架構③）：重訊/事件分類 → events 表。

規則/關鍵字分類，不丟 LLM 算（LLM 只在 P5 做白話解讀）。利空事件供 ExitEngine
的 NewsRiskSignal 觸發出場警戒；題材當進場催化劑（不硬進評分）。
事件 append-only：以 (stock_id,date,title) 唯一鍵去重，重跑安全。
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..sources import registry
from ..sources.base import SourceError
from ..storage import models
from .base import BaseEngine

# 關鍵字分類（實質影響營運/股價的事件；不做社群情緒）
_RISK_KW = [
    "下修", "下調", "虧損", "減資", "違約", "訴訟", "停工", "停產", "解散", "跳票",
    "處分損失", "財測下修", "裁員", "減產", "召回", "裁罰", "退票", "重整", "下市",
    "警示", "處置", "停止交易", "財務困難", "跌停",
]
_THEME_KW = [
    "擴廠", "擴產", "得標", "新訂單", "併購", "收購", "認證", "投資", "調高", "上修",
    "接單", "量產", "簽約", "合作", "新產品", "取得", "中標", "增資擴產", "法說",
]


def classify(title: str, summary: str | None) -> tuple[str, bool]:
    """回 (category, is_risk)。"""
    text = f"{title} {summary or ''}"
    if any(k in text for k in _RISK_KW):
        return "利空", True
    if any(k in text for k in _THEME_KW):
        return "題材", False
    return "中性", False


class NewsEngine(BaseEngine):
    name = "news"

    def run(self, session: Session, trading_date: date) -> dict:
        try:
            df = registry.provider("news").fetch_events(trading_date, trading_date)
        except SourceError as exc:
            return {"status": "error", "reason": exc.reason}
        if df.empty:
            return {"status": "ok", "events": 0}

        known = set(session.execute(select(models.Stock.id)).scalars().all())
        rows: list[dict] = []
        for rec in df.to_dict("records"):
            sid = str(rec["stock_id"])
            if sid not in known:
                continue
            title = rec["title"]
            # 來源已標利空（處置）則沿用，否則關鍵字分類
            if rec.get("is_risk"):
                category, is_risk = rec.get("category") or "利空", True
            else:
                category, is_risk = classify(title, rec.get("summary"))
            rows.append({
                "stock_id": sid, "date": rec.get("date") or trading_date,
                "category": category, "title": title, "summary": rec.get("summary"),
                "is_risk": is_risk, "source": rec.get("source"), "url": rec.get("url"),
            })

        if rows:
            stmt = sqlite_insert(models.Event).values(rows).on_conflict_do_nothing(
                index_elements=["stock_id", "date", "title"]
            )
            session.execute(stmt)
            session.flush()
        return {"status": "ok", "events": len(rows), "risk": sum(1 for r in rows if r["is_risk"])}
