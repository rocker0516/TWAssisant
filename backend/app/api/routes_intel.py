"""情報頁端點：近期消息總結（讀盤後批次快取 + events 表，請求時不打 LLM）。

四視角：全市場 digest / 依題材（類股）digest / 持股+觀察焦點 digest / 近期事件列表。
digest 來自 LLMBatchStep 夜間生成的 llm_cache（news_market / news_theme / news_focus）；
事件列表直查 events 表，可依天數 / 類別 / 是否利空篩選。
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..llm.store import cache_key, get_cached
from ..storage import models
from .deps import get_session
from .schemas import IntelEvent, IntelResponse, ThemeDigest

router = APIRouter(tags=["intel"])

_DIGEST_DAYS = 7  # 對齊 news_digest.DIGEST_DAYS（題材分群統計窗口）


@router.get("/intel", response_model=IntelResponse)
def intel(
    days: int = Query(14, ge=1, le=60),
    category: str | None = Query(None),  # 利空 / 題材 / 中性
    risk_only: bool = Query(False),
    session: Session = Depends(get_session),
) -> IntelResponse:
    td = session.execute(select(func.max(models.DailyPrice.date))).scalar()
    if td is None:
        return IntelResponse(date=None, themes=[], events=[], total=0, risk_count=0, has_digest=False)

    market_digest = get_cached(session, cache_key("news_market", "tw", td))
    focus_digest = get_cached(session, cache_key("news_focus", "me", td))

    # 題材 digest：找批次當天已快取的 news_theme，補上窗口內事件統計
    theme_start = td - timedelta(days=_DIGEST_DAYS)
    sector_counts = dict(session.execute(
        select(
            models.Stock.sector_id,
            func.count(),
        )
        .join(models.Event, models.Event.stock_id == models.Stock.id)
        .where(models.Event.date >= theme_start, models.Event.date <= td)
        .group_by(models.Stock.sector_id)
    ).all())
    sector_risk = dict(session.execute(
        select(models.Stock.sector_id, func.count())
        .join(models.Event, models.Event.stock_id == models.Stock.id)
        .where(models.Event.date >= theme_start, models.Event.date <= td, models.Event.is_risk.is_(True))
        .group_by(models.Stock.sector_id)
    ).all())
    themes: list[ThemeDigest] = []
    cached = session.execute(
        select(models.LlmCache).where(models.LlmCache.kind == "news_theme", models.LlmCache.date == td)
    ).scalars().all()
    sect_names = dict(session.execute(select(models.Sector.id, models.Sector.name)).all())
    for c in cached:
        try:
            sec_id = int(c.ref_id)
        except (TypeError, ValueError):
            continue
        themes.append(ThemeDigest(
            sector_id=sec_id,
            sector_name=sect_names.get(sec_id, str(sec_id)),
            digest=c.content or "",
            event_count=sector_counts.get(sec_id, 0),
            risk_count=sector_risk.get(sec_id, 0),
        ))
    themes.sort(key=lambda t: (t.risk_count, t.event_count), reverse=True)

    # 窗口內事件列表（篩選後），含個股名/來源
    start = td - timedelta(days=days)
    base = (
        select(models.Event, models.Stock.name)
        .join(models.Stock, models.Event.stock_id == models.Stock.id)
        .where(models.Event.date >= start, models.Event.date <= td)
    )
    total = session.execute(
        select(func.count()).select_from(models.Event)
        .where(models.Event.date >= start, models.Event.date <= td, models.Event.stock_id.isnot(None))
    ).scalar_one()
    risk_count = session.execute(
        select(func.count()).select_from(models.Event)
        .where(models.Event.date >= start, models.Event.date <= td, models.Event.is_risk.is_(True))
    ).scalar_one()

    q = base
    if category:
        q = q.where(models.Event.category == category)
    if risk_only:
        q = q.where(models.Event.is_risk.is_(True))
    rows = session.execute(
        q.order_by(models.Event.is_risk.desc(), models.Event.date.desc(), models.Event.id.desc()).limit(200)
    ).all()
    events = [
        IntelEvent(stock_id=e.stock_id, name=name, date=e.date, category=e.category,
                   title=e.title, is_risk=e.is_risk, source=e.source, url=e.url)
        for e, name in rows
    ]

    return IntelResponse(
        date=td,
        market_digest=market_digest,
        focus_digest=focus_digest,
        themes=themes,
        events=events,
        total=total,
        risk_count=risk_count,
        has_digest=bool(market_digest or focus_digest or themes),
    )
