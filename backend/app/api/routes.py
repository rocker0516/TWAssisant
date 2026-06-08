"""P1 讀取端點：推薦頁 + 詳情頁 + K線。

讀取直查算好的結果（架構⑥：讀繞過 Service 直接 repo）。寫入端點 P2 再加。
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..storage import models
from .deps import get_session
from .schemas import (
    Candle,
    ChipSummary,
    EventDTO,
    FundamentalSummary,
    OhlcvResponse,
    RecommendationItem,
    RecommendationList,
    ScoreDTO,
    StockDetail,
)

router = APIRouter()

_NEAR_BAND = 5.0  # 接近門檻區間寬度


def _latest_score_date(session: Session) -> date | None:
    return session.execute(select(func.max(models.Score.date))).scalar()


def _threshold(session: Session, track: str) -> float:
    row = session.get(models.Setting, "scoring")
    cfg = row.value if row and isinstance(row.value, dict) else {}
    return float(cfg.get(track, {}).get("threshold", 70.0))


def _price_change(session: Session, stock_id: str, d: date) -> tuple[float | None, float | None, float | None]:
    """回 (close, change, change_pct)。"""
    rows = session.execute(
        select(models.DailyPrice.close)
        .where(models.DailyPrice.stock_id == stock_id, models.DailyPrice.date <= d)
        .order_by(models.DailyPrice.date.desc())
        .limit(2)
    ).scalars().all()
    if not rows:
        return None, None, None
    close = rows[0]
    if len(rows) < 2 or rows[1] in (None, 0) or close is None:
        return close, None, None
    change = close - rows[1]
    return close, round(change, 2), round(change / rows[1] * 100, 2)


def _to_item(session: Session, sc: models.Score, name: str, sector_name: str | None, d: date) -> RecommendationItem:
    close, _, change_pct = _price_change(session, sc.stock_id, d)
    return RecommendationItem(
        stock_id=sc.stock_id,
        name=name,
        sector_name=sector_name,
        track=sc.track,
        total_score=sc.total_score,
        sub_scores=sc.sub_scores,
        close=close,
        change_pct=change_pct,
        buy_low=sc.buy_low,
        buy_high=sc.buy_high,
        stop_loss=sc.stop_loss,
        loss_pct=sc.loss_pct,
        reasons=sc.reasons,
    )


@router.get("/recommendations", response_model=RecommendationList)
def recommendations(
    track: str = Query("wave", pattern="^(wave|long)$"),
    session: Session = Depends(get_session),
) -> RecommendationList:
    d = _latest_score_date(session)
    threshold = _threshold(session, track)
    if d is None:
        return RecommendationList(track=track, date=None, threshold=threshold, items=[], near=[])

    base = (
        select(models.Score, models.Stock.name, models.Sector.name)
        .join(models.Stock, models.Score.stock_id == models.Stock.id)
        .join(models.Sector, models.Stock.sector_id == models.Sector.id, isouter=True)
        .where(models.Score.track == track, models.Score.date == d)
    )
    items, near = [], []
    for sc, name, sector_name in session.execute(
        base.order_by(models.Score.total_score.desc())
    ).all():
        if sc.passed:
            items.append(_to_item(session, sc, name, sector_name, d))
        elif sc.passed_filter and sc.total_score is not None and sc.total_score >= threshold - _NEAR_BAND:
            near.append(_to_item(session, sc, name, sector_name, d))

    return RecommendationList(track=track, date=d, threshold=threshold, items=items, near=near)


def _score_dto(sc: models.Score | None) -> ScoreDTO | None:
    if sc is None:
        return None
    return ScoreDTO(
        track=sc.track,
        passed=sc.passed,
        total_score=sc.total_score,
        sub_scores=sc.sub_scores,
        buy_low=sc.buy_low,
        buy_high=sc.buy_high,
        stop_loss=sc.stop_loss,
        loss_pct=sc.loss_pct,
        reasons=sc.reasons,
    )


@router.get("/stocks/{stock_id}", response_model=StockDetail)
def stock_detail(stock_id: str, session: Session = Depends(get_session)) -> StockDetail:
    stock = session.get(models.Stock, stock_id)
    if stock is None:
        raise HTTPException(404, f"找不到股票 {stock_id}")
    sector = session.get(models.Sector, stock.sector_id) if stock.sector_id else None

    d = _latest_score_date(session) or session.execute(
        select(func.max(models.DailyPrice.date)).where(models.DailyPrice.stock_id == stock_id)
    ).scalar()
    close, change, change_pct = _price_change(session, stock_id, d) if d else (None, None, None)

    scores: dict[str, ScoreDTO | None] = {}
    for tk in ("wave", "long"):
        sc = session.get(models.Score, {"stock_id": stock_id, "date": d, "track": tk}) if d else None
        scores[tk] = _score_dto(sc)

    inst = session.execute(
        select(models.Institutional).where(models.Institutional.stock_id == stock_id)
        .order_by(models.Institutional.date.desc()).limit(1)
    ).scalars().first()
    mg = session.execute(
        select(models.Margin).where(models.Margin.stock_id == stock_id)
        .order_by(models.Margin.date.desc()).limit(1)
    ).scalars().first()
    chip = ChipSummary(
        date=inst.date if inst else (mg.date if mg else None),
        foreign_net=inst.foreign_net if inst else None,
        trust_net=inst.trust_net if inst else None,
        dealer_net=inst.dealer_net if inst else None,
        total_net=inst.total_net if inst else None,
        margin_balance=mg.margin_balance if mg else None,
        short_balance=mg.short_balance if mg else None,
    )

    val = session.execute(
        select(models.Valuation).where(models.Valuation.stock_id == stock_id)
        .order_by(models.Valuation.date.desc()).limit(1)
    ).scalars().first()
    rev = session.execute(
        select(models.RevenueMonthly).where(models.RevenueMonthly.stock_id == stock_id)
        .order_by(models.RevenueMonthly.year.desc(), models.RevenueMonthly.month.desc()).limit(1)
    ).scalars().first()
    fin = session.execute(
        select(models.FinancialQuarter).where(models.FinancialQuarter.stock_id == stock_id)
        .order_by(models.FinancialQuarter.year.desc(), models.FinancialQuarter.quarter.desc()).limit(1)
    ).scalars().first()
    # EPS：優先用財報；無則以 收盤價/本益比 推導近4季 trailing EPS
    eps = fin.eps if fin and fin.eps is not None else None
    if eps is None and val and val.pe and val.pe > 0 and close:
        eps = round(close / val.pe, 2)
    fundamental = FundamentalSummary(
        pe=val.pe if val else None,
        pb=val.pb if val else None,
        dividend_yield=val.dividend_yield if val else None,
        eps=eps,
        revenue_yoy=rev.yoy if rev else None,
    )

    events = session.execute(
        select(models.Event).where(models.Event.stock_id == stock_id)
        .order_by(models.Event.date.desc(), models.Event.id.desc()).limit(10)
    ).scalars().all()

    return StockDetail(
        stock_id=stock.id,
        name=stock.name,
        sector_name=sector.name if sector else None,
        market=stock.market,
        date=d,
        close=close,
        change=change,
        change_pct=change_pct,
        scores=scores,
        chip=chip,
        fundamental=fundamental,
        events=[
            EventDTO(date=e.date, category=e.category, title=e.title, summary=e.summary,
                     is_risk=e.is_risk, source=e.source, url=e.url)
            for e in events
        ],
    )


@router.get("/stocks/{stock_id}/ohlcv", response_model=OhlcvResponse)
def stock_ohlcv(
    stock_id: str,
    days: int = Query(120, ge=20, le=500),
    session: Session = Depends(get_session),
) -> OhlcvResponse:
    rows = session.execute(
        select(models.DailyPrice, models.Indicator)
        .join(
            models.Indicator,
            (models.Indicator.stock_id == models.DailyPrice.stock_id)
            & (models.Indicator.date == models.DailyPrice.date),
            isouter=True,
        )
        .where(models.DailyPrice.stock_id == stock_id)
        .order_by(models.DailyPrice.date.desc())
        .limit(days)
    ).all()
    candles = [
        Candle(
            date=p.date, open=p.open, high=p.high, low=p.low, close=p.close, volume=p.volume,
            ma5=i.ma5 if i else None, ma20=i.ma20 if i else None, ma60=i.ma60 if i else None,
            kd_k=i.kd_k if i else None, kd_d=i.kd_d if i else None,
            macd=i.macd if i else None, macd_signal=i.macd_signal if i else None,
            macd_hist=i.macd_hist if i else None,
        )
        for p, i in reversed(rows)
    ]
    return OhlcvResponse(stock_id=stock_id, candles=candles)
