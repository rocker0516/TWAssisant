"""首頁總覽端點（P6）：大盤摘要 + 各 widget 摘要資料。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..engines.exit_engine import ExitEngine
from ..llm.lazy import market_note
from ..services.holding_service import HoldingService
from ..storage import models
from .deps import get_session
from .schemas import (
    AlertBrief,
    EventBrief,
    MarketSummary,
    OverviewResponse,
    RecoBrief,
    SectorBrief,
)

router = APIRouter(tags=["overview"])
_exit = ExitEngine()
_holding = HoldingService()
_LEVEL = {"red": 0, "orange": 1, "yellow": 2, "green": 3}


def _market(session: Session, td: date) -> MarketSummary:
    dates = list(session.execute(
        select(models.DailyPrice.date).where(models.DailyPrice.date <= td)
        .distinct().order_by(models.DailyPrice.date.desc()).limit(2)
    ).scalars().all())
    prev = dates[1] if len(dates) > 1 else None

    def closes(d):
        return dict(session.execute(
            select(models.DailyPrice.stock_id, models.DailyPrice.close).where(models.DailyPrice.date == d)
        ).all())

    cur, pv = closes(td), (closes(prev) if prev else {})
    adv = dec = unch = 0
    for sid, c in cur.items():
        p = pv.get(sid)
        if c is None or p is None:
            continue
        if c > p:
            adv += 1
        elif c < p:
            dec += 1
        else:
            unch += 1
    turnover = session.execute(
        select(func.sum(models.DailyPrice.turnover)).where(models.DailyPrice.date == td)
    ).scalar() or 0
    f, t, de = session.execute(
        select(
            func.sum(models.Institutional.foreign_net),
            func.sum(models.Institutional.trust_net),
            func.sum(models.Institutional.dealer_net),
        ).where(models.Institutional.date == td)
    ).one()
    from ..engines.market_breadth import compute_breadth
    b = compute_breadth(session, td)
    return MarketSummary(
        date=td, turnover_billion=round(turnover / 1e8, 1), advancers=adv, decliners=dec,
        unchanged=unch, foreign_net=int(f) if f is not None else None,
        trust_net=int(t) if t is not None else None, dealer_net=int(de) if de is not None else None,
        pct_above_ma20=b["pct_above_ma20"], pct_above_ma60=b["pct_above_ma60"],
        foreign_buy_count=b["foreign_buy_count"], foreign_sell_count=b["foreign_sell_count"],
        trust_buy_count=b["trust_buy_count"], trust_sell_count=b["trust_sell_count"],
        trust_top10_concentration=b["trust_top10_concentration"],
    )


@router.get("/overview", response_model=OverviewResponse)
def overview(session: Session = Depends(get_session)) -> OverviewResponse:
    td = session.execute(select(func.max(models.DailyPrice.date))).scalar()
    if td is None:
        return OverviewResponse(
            market=MarketSummary(date=None, turnover_billion=None, advancers=0, decliners=0,
                                 unchanged=0, foreign_net=None, trust_net=None, dealer_net=None),
            holdings_alerts=[], reco_wave_count=0, reco_long_count=0, reco_top=[],
            sectors_top=[], recent_events=[],
        )

    # 持股提醒（🔴🟠 優先）
    alerts: list[AlertBrief] = []
    for h in session.execute(select(models.Holding).where(models.Holding.status == "open")).scalars().all():
        pos = _holding.position(session, h)
        if pos.shares <= 0 or pos.avg_cost is None:
            continue
        close = session.execute(
            select(models.DailyPrice.close).where(models.DailyPrice.stock_id == h.stock_id, models.DailyPrice.date <= td)
            .order_by(models.DailyPrice.date.desc()).limit(1)
        ).scalar()
        if close is None:
            continue
        st = _exit.evaluate(session, h, td, avg_cost=pos.avg_cost, close=close)
        if st.level in ("red", "orange", "yellow"):
            stock = session.get(models.Stock, h.stock_id)
            alerts.append(AlertBrief(stock_id=h.stock_id, name=stock.name if stock else h.stock_id,
                                     light=st.light, return_pct=round((close / pos.avg_cost - 1) * 100, 2),
                                     signals=st.signals[:2]))
    alerts.sort(key=lambda a: _LEVEL.get({"🔴": "red", "🟠": "orange", "🟡": "yellow"}.get(a.light, "green"), 9))

    # 進場推薦摘要
    counts = {tk: session.execute(
        select(func.count()).select_from(models.Score)
        .where(models.Score.track == tk, models.Score.date == td, models.Score.passed.is_(True))
    ).scalar_one() for tk in ("wave", "long")}
    top_rows = session.execute(
        select(models.Score, models.Stock.name).join(models.Stock, models.Score.stock_id == models.Stock.id)
        .where(models.Score.date == td, models.Score.passed.is_(True))
        .order_by(models.Score.total_score.desc()).limit(5)
    ).all()
    reco_top = [RecoBrief(stock_id=sc.stock_id, name=name, track=sc.track, total_score=sc.total_score)
                for sc, name in top_rows]

    # 類股強弱 top
    sec_rows = session.execute(
        select(models.SectorDaily, models.Sector.name)
        .join(models.Sector, models.SectorDaily.sector_id == models.Sector.id)
        .where(models.SectorDaily.date == td).order_by(models.SectorDaily.strength_score.desc()).limit(6)
    ).all()
    sectors_top = [SectorBrief(id=sd.sector_id, name=name, strength_score=sd.strength_score,
                               trend_short=sd.trend_short, rotation_stage=sd.rotation_stage)
                   for sd, name in sec_rows]

    # 重要消息（利空優先）
    ev_rows = session.execute(
        select(models.Event, models.Stock.name).join(models.Stock, models.Event.stock_id == models.Stock.id)
        .where(models.Event.is_risk.is_(True)).order_by(models.Event.date.desc(), models.Event.id.desc()).limit(8)
    ).all()
    recent_events = [EventBrief(stock_id=e.stock_id, name=name, date=e.date, category=e.category,
                                title=e.title, is_risk=e.is_risk) for e, name in ev_rows]

    return OverviewResponse(
        market=_market(session, td), market_note=market_note(session, td),
        holdings_alerts=alerts[:5],
        reco_wave_count=counts["wave"], reco_long_count=counts["long"], reco_top=reco_top,
        sectors_top=sectors_top, recent_events=recent_events,
    )
