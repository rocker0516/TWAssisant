"""情報頁端點：近期消息總結（llm_cache 首讀懶生成 + events 表）。

四視角：全市場 digest / 依題材（類股）digest / 持股+觀察焦點 digest / 近期事件列表。
digest 走 news_digest.intel_digests：當日已快取直接讀、缺的平行生成一次回寫；
事件列表直查 events 表，可依天數 / 類別 / 是否利空篩選。
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..llm.news_digest import intel_digests
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

    digests = intel_digests(session, td)
    market_digest = digests["market"]
    focus_digest = digests["focus"]

    # 題材 digest：補上窗口內事件統計
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
    sect_names = dict(session.execute(select(models.Sector.id, models.Sector.name)).all())
    for sec_id, text in digests["themes"]:
        themes.append(ThemeDigest(
            sector_id=sec_id,
            sector_name=sect_names.get(sec_id, str(sec_id)),
            digest=text,
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
