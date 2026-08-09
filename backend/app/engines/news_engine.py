"""NewsEngine（架構③）：重訊/事件分類 → events 表。

規則/關鍵字分類，不丟 LLM 算（LLM 只在 P5 做白話解讀）。利空事件供 ExitEngine
的 NewsRiskSignal 觸發出場警戒；題材當進場催化劑（不硬進評分）。
事件 append-only：以 (stock_id,date,title) 唯一鍵去重，重跑安全。
"""

from __future__ import annotations

from datetime import date, timedelta

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
    "接單", "量產", "簽約", "合作", "新產品", "取得", "中標", "增資擴產",
]
# 展望：前瞻性消息（財測/法說/評等/後市看法），與「已發生事實」的題材區隔。
# 注意：負向前瞻（如「財測下修」）已在 _RISK_KW，利空優先權保留供 ExitEngine。
_OUTLOOK_KW = [
    "展望", "財測", "法說", "法人說明會", "目標價", "調升評等", "調降評等",
    "調升目標", "調降目標", "看好", "看淡", "樂觀", "保守", "全年目標", "下半年",
    "營運展望", "後市", "上看", "旺季", "急單", "拉貨", "預估營收",
]


def classify(title: str, summary: str | None) -> tuple[str, bool]:
    """回 (category, is_risk)。優先序：利空 → 展望 → 題材 → 中性。"""
    text = f"{title} {summary or ''}"
    if any(k in text for k in _RISK_KW):
        return "利空", True
    if any(k in text for k in _OUTLOOK_KW):
        return "展望", False
    if any(k in text for k in _THEME_KW):
        return "題材", False
    return "中性", False


class NewsEngine(BaseEngine):
    name = "news"

    # 抓取窗口：給冷啟動/補跑用（重訊/處置為當日快照）。
    # append-only + (stock_id,date,title) 去重，重跑安全。
    lookback_days = 7
    # 個股新聞窗口較短（FinMind 免費層一檔一日一呼叫，控量）。
    stock_news_days = 3
    # 最多對幾檔抓個股新聞（持股+觀察+今日推薦，去重後取前 N）。
    max_focus_news = 30

    def _focus_ids(self, session: Session, td: date) -> list[str]:
        """聚焦股：持股(open) + 觀察清單 + 今日達門檻推薦（去重保序、取前 N）。"""
        ids: list[str] = []
        ids += session.execute(
            select(models.Holding.stock_id).where(models.Holding.status == "open").distinct()
        ).scalars().all()
        ids += session.execute(select(models.WatchlistItem.stock_id).distinct()).scalars().all()
        ids += session.execute(
            select(models.Score.stock_id)
            .where(models.Score.date == td, models.Score.passed.is_(True))
            .order_by(models.Score.total_score.desc()).limit(self.max_focus_news)
        ).scalars().all()
        return list(dict.fromkeys(str(i) for i in ids))[:self.max_focus_news]

    def run(self, session: Session, trading_date: date) -> dict:
        start = trading_date - timedelta(days=self.lookback_days)
        provider = registry.provider("news")

        frames: list[pd.DataFrame] = []
        # ① 整市場重訊/處置（Combined 內部各源自行容錯，不 raise）
        whole = provider.fetch_events(start, trading_date)
        if not whole.empty:
            frames.append(whole)

        # ② 聚焦股個股新聞（FinMind 免費層逐檔）
        news_start = trading_date - timedelta(days=self.stock_news_days)
        stock_news = 0
        for sid in self._focus_ids(session, trading_date):
            sdf = provider.fetch_stock_events(sid, news_start, trading_date)
            if not sdf.empty:
                frames.append(sdf)
                stock_news += len(sdf)

        df = (
            pd.concat(frames, ignore_index=True)
            if frames else pd.DataFrame(columns=["stock_id", "date", "title", "summary", "is_risk", "source", "url", "category"])
        )
        if df.empty:
            return {"status": "ok", "events": 0, "stock_news": 0}

        known = set(session.execute(select(models.Stock.id)).scalars().all())
        rows: list[dict] = []
        for rec in df.to_dict("records"):
            sid = str(rec["stock_id"])
            if sid not in known:
                continue
            title = rec["title"]
            # 來源已標利空（處置）或已標分類（內部人轉讓）則沿用，否則關鍵字分類
            if rec.get("is_risk"):
                category, is_risk = rec.get("category") or "利空", True
            elif rec.get("category"):
                category, is_risk = rec["category"], False
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
        return {
            "status": "ok", "events": len(rows), "stock_news": stock_news,
            "risk": sum(1 for r in rows if r["is_risk"]),
        }
