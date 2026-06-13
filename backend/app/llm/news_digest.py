"""近期消息總結批次（情報頁，架構④ LLMBatchStep 的一部分）。

讀 events 表近 N 日事件 → 用 News 翻譯員產四種視角 digest 存 llm_cache：
  news_market（全市場）/ news_theme:<sector_id>（依題材分群）/
  news_focus:me（持股+觀察清單焦點）/ news_stock:<stock_id>（個股）。
全用 Haiku；client 不可用→略過（白天讀舊快取）；單檔失敗回 None 不中斷其他。
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..storage import models
from .assistant import _pe_level
from .client import LLMClient
from .store import cache_key, put_cached
from .translators import (
    NewsFocusTranslator,
    NewsMarketTranslator,
    NewsStockTranslator,
    NewsThemeTranslator,
    _mom,
)

DIGEST_DAYS = 7      # 總結窗口（對齊 NewsEngine.lookback_days）
MAX_THEMES = 12      # 最多產幾個類股 digest（限 LLM 呼叫量）
MAX_STOCK_DIGESTS = 30  # 最多產幾檔個股 digest


def _recent_events(session: Session, td: date) -> list[dict]:
    """近 DIGEST_DAYS 日事件（含個股名/類股），利空優先、再依日期新到舊。"""
    start = td - timedelta(days=DIGEST_DAYS)
    rows = session.execute(
        select(models.Event, models.Stock.name, models.Stock.sector_id)
        .join(models.Stock, models.Event.stock_id == models.Stock.id, isouter=True)
        .where(models.Event.date >= start, models.Event.date <= td)
        .order_by(models.Event.is_risk.desc(), models.Event.date.desc())
    ).all()
    return [
        {
            "stock_id": e.stock_id, "name": name, "sector_id": sector_id,
            "date": e.date, "category": e.category, "title": e.title,
            "is_risk": bool(e.is_risk),
        }
        for e, name, sector_id in rows
    ]


def _focus_ids(session: Session) -> dict[str, str]:
    """持股(open) ∪ 觀察清單股號 → role（持股優先）。"""
    out: dict[str, str] = {}
    for sid in session.execute(
        select(models.WatchlistItem.stock_id).distinct()
    ).scalars().all():
        out[sid] = "watch"
    for sid in session.execute(
        select(models.Holding.stock_id).where(models.Holding.status == "open").distinct()
    ).scalars().all():
        out[sid] = "holding"
    return out


def _stock_fund(session: Session, stock_id: str) -> tuple[str, str, str]:
    """個股名 + 月營收趨勢 + 估值水準（質化）。"""
    stock = session.get(models.Stock, stock_id)
    rev = session.execute(
        select(models.RevenueMonthly).where(models.RevenueMonthly.stock_id == stock_id)
        .order_by(models.RevenueMonthly.year.desc(), models.RevenueMonthly.month.desc()).limit(1)
    ).scalars().first()
    val = session.execute(
        select(models.Valuation).where(models.Valuation.stock_id == stock_id)
        .order_by(models.Valuation.date.desc()).limit(1)
    ).scalars().first()
    name = stock.name if stock else stock_id
    return name, (_mom(rev.yoy) if rev else "未知"), _pe_level(val.pe if val else None)


def run_news_batch(session: Session, td: date, client: LLMClient | None = None) -> dict:
    client = client or LLMClient()
    if not client.available:
        return {"status": "skipped", "reason": "no_api_key"}

    events = _recent_events(session, td)
    if not events:
        return {"status": "ok", "market": 0, "themes": 0, "stocks": 0, "focus": 0}

    model = NewsMarketTranslator().model
    risk_count = sum(1 for e in events if e["is_risk"])
    theme_count = sum(1 for e in events if e["category"] == "題材")

    # ① 全市場
    market = 0
    text = NewsMarketTranslator().translate(
        client, days=DIGEST_DAYS, total=len(events),
        risk_count=risk_count, theme_count=theme_count, events=events,
    )
    if text:
        put_cached(session, cache_key("news_market", "tw", td), "news_market", "tw", td, text, model)
        market = 1
    session.flush()

    # 分群索引
    by_sector: dict[int, list[dict]] = {}
    by_stock: dict[str, list[dict]] = {}
    for e in events:
        if e["sector_id"] is not None:
            by_sector.setdefault(e["sector_id"], []).append(e)
        if e["stock_id"]:
            by_stock.setdefault(e["stock_id"], []).append(e)

    # ② 依題材（類股）：含利空優先、再依事件數；門檻 = ≥3 則或含利空
    sect_names = dict(session.execute(select(models.Sector.id, models.Sector.name)).all())
    sect_dir = {
        sd.sector_id: sd.trend_short
        for sd in session.execute(
            select(models.SectorDaily).where(models.SectorDaily.date == td)
        ).scalars().all()
    }
    sector_ids = sorted(
        by_sector,
        key=lambda s: (any(x["is_risk"] for x in by_sector[s]), len(by_sector[s])),
        reverse=True,
    )
    sector_ids = [
        s for s in sector_ids
        if len(by_sector[s]) >= 3 or any(x["is_risk"] for x in by_sector[s])
    ][:MAX_THEMES]
    themes = 0
    tt = NewsThemeTranslator()
    for sid in sector_ids:
        evs = by_sector[sid]
        text = tt.translate(
            client, sector=sect_names.get(sid, str(sid)), direction=sect_dir.get(sid) or "未知",
            total=len(evs), risk_count=sum(1 for x in evs if x["is_risk"]), events=evs,
        )
        if text:
            put_cached(session, cache_key("news_theme", sid, td), "news_theme", sid, td, text, model)
            themes += 1
    session.flush()

    # ③ 持股+觀察焦點
    focus_ids = _focus_ids(session)
    focus_stocks = []
    for sid, role in focus_ids.items():
        evs = by_stock.get(sid, [])
        if not evs:
            continue
        stock = session.get(models.Stock, sid)
        focus_stocks.append({
            "name": stock.name if stock else sid, "role": role,
            "has_risk": any(x["is_risk"] for x in evs), "events": evs,
        })
    focus = 0
    if focus_stocks:
        text = NewsFocusTranslator().translate(client, stocks=focus_stocks)
        if text:
            put_cached(session, cache_key("news_focus", "me", td), "news_focus", "me", td, text, model)
            focus = 1
    session.flush()

    # ④ 個股：焦點股 + 事件最多的前 N 檔
    target_ids = list(dict.fromkeys(
        [s for s in focus_ids if by_stock.get(s)]
        + sorted(by_stock, key=lambda s: len(by_stock[s]), reverse=True)
    ))[:MAX_STOCK_DIGESTS]
    stocks = 0
    st = NewsStockTranslator()
    for sid in target_ids:
        evs = by_stock.get(sid, [])
        if not evs:
            continue
        name, rev_trend, pe_level = _stock_fund(session, sid)
        text = st.translate(
            client, name=name, events=evs, revenue_trend=rev_trend,
            pe_level=pe_level, has_risk=any(x["is_risk"] for x in evs),
        )
        if text:
            put_cached(session, cache_key("news_stock", sid, td), "news_stock", sid, td, text, model)
            stocks += 1
    session.flush()

    return {"status": "ok", "market": market, "themes": themes, "stocks": stocks, "focus": focus}
