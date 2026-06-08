"""類股端點（P3）：強弱排行 / 熱力圖資料 + 類股專屬頁。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..llm.store import cache_key, get_cached
from ..storage import models
from .deps import get_session
from .schemas import SectorConstituent, SectorDetail, SectorItem, SectorList

router = APIRouter(prefix="/sectors", tags=["sectors"])


def _to_item(sd: models.SectorDaily, name: str) -> SectorItem:
    return SectorItem(
        id=sd.sector_id, name=name, strength_score=sd.strength_score,
        dim_momentum=sd.dim_momentum, dim_fund=sd.dim_fund, dim_tech=sd.dim_tech,
        trend_short=sd.trend_short, trend_long=sd.trend_long, rotation_stage=sd.rotation_stage,
        momentum_5=sd.momentum_5, momentum_20=sd.momentum_20, foreign_net=sd.foreign_net,
        turnover_share=sd.turnover_share, above_ma20=sd.above_ma20, constituents=sd.constituents,
    )


@router.get("", response_model=SectorList)
def list_sectors(session: Session = Depends(get_session)) -> SectorList:
    d = session.execute(select(func.max(models.SectorDaily.date))).scalar()
    if d is None:
        return SectorList(date=None, items=[])
    rows = session.execute(
        select(models.SectorDaily, models.Sector.name)
        .join(models.Sector, models.SectorDaily.sector_id == models.Sector.id)
        .where(models.SectorDaily.date == d)
        .order_by(models.SectorDaily.strength_score.desc())
    ).all()
    return SectorList(date=d, items=[_to_item(sd, name) for sd, name in rows])


@router.get("/{sector_id}", response_model=SectorDetail)
def sector_detail(sector_id: int, session: Session = Depends(get_session)) -> SectorDetail:
    d = session.execute(select(func.max(models.SectorDaily.date))).scalar()
    sd = session.get(models.SectorDaily, {"sector_id": sector_id, "date": d}) if d else None
    sector = session.get(models.Sector, sector_id)
    if sector is None or sd is None:
        raise HTTPException(404, f"找不到類股 {sector_id}")

    # 成分股 + 最新評分 + 當日漲跌（領漲排序、標記已推薦）
    stocks = session.execute(
        select(models.Stock.id, models.Stock.name).where(models.Stock.sector_id == sector_id)
    ).all()
    scores = {
        (sc.stock_id, sc.track): sc
        for sc in session.execute(
            select(models.Score).where(models.Score.date == d)
        ).scalars().all()
    }

    items: list[SectorConstituent] = []
    for sid, name in stocks:
        rows = session.execute(
            select(models.DailyPrice.close)
            .where(models.DailyPrice.stock_id == sid, models.DailyPrice.date <= d)
            .order_by(models.DailyPrice.date.desc()).limit(2)
        ).scalars().all()
        if not rows:
            continue
        close = rows[0]
        change_pct = round((close - rows[1]) / rows[1] * 100, 2) if len(rows) > 1 and rows[1] else None
        w, ll = scores.get((sid, "wave")), scores.get((sid, "long"))
        items.append(SectorConstituent(
            stock_id=sid, name=name, close=close, change_pct=change_pct,
            wave_score=w.total_score if w else None,
            long_score=ll.total_score if ll else None,
            recommended=bool((w and w.passed) or (ll and ll.passed)),
        ))
    items.sort(key=lambda c: (c.change_pct if c.change_pct is not None else -999), reverse=True)
    interp = get_cached(session, cache_key("sector", sector_id, d)) if d else None
    return SectorDetail(sector=_to_item(sd, sector.name), constituents=items, interpretation=interp)
