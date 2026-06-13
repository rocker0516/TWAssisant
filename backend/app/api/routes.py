"""P1 讀取端點：推薦頁 + 詳情頁 + K線。

讀取直查算好的結果（架構⑥：讀繞過 Service 直接 repo）。寫入端點 P2 再加。
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..storage import models
from .deps import get_session, get_session_write
from ..llm.assistant import _etf_kind, _scale_label
from ..llm.store import cache_key, get_cached
from .schemas import (
    Candle,
    ChipSummary,
    EtfInfo,
    EventDTO,
    FundamentalSummary,
    LevelDTO,
    LevelsResponse,
    OhlcvResponse,
    RecommendationItem,
    RecommendationList,
    ScoreDTO,
    StockDetail,
    StockSearchItem,
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
        coverage=sc.coverage,
        confidence=sc.confidence,
        stability=sc.stability,
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
        coverage=sc.coverage,
        confidence=sc.confidence,
        stability=sc.stability,
        buy_low=sc.buy_low,
        buy_high=sc.buy_high,
        stop_loss=sc.stop_loss,
        loss_pct=sc.loss_pct,
        reasons=sc.reasons,
    )


@router.get("/stocks/search", response_model=list[StockSearchItem])
def stock_search(
    q: str = Query(..., min_length=1, description="股號或股名關鍵字"),
    session: Session = Depends(get_session),
) -> list[StockSearchItem]:
    """股號/股名查詢（給查詢框跳轉用）。代號前綴或名稱包含皆比對，依相關度排序取前 10。"""
    term = q.strip()
    if not term:
        return []
    rows = session.execute(
        select(models.Stock).where(
            or_(models.Stock.id.like(f"{term}%"), models.Stock.name.like(f"%{term}%"))
        ).limit(50)
    ).scalars().all()

    def rank(s: models.Stock) -> tuple:
        low = term.lower()
        if s.id.lower() == low:
            return (0, s.id)
        if s.id.lower().startswith(low):
            return (1, s.id)
        if low in s.name.lower():
            return (2, s.id)
        return (3, s.id)

    ranked = sorted(rows, key=rank)[:10]
    return [
        StockSearchItem(stock_id=s.id, name=s.name, market=s.market, is_etf=s.is_etf)
        for s in ranked
    ]


@router.get("/calibration")
def calibration(session: Session = Depends(get_session)) -> dict:
    """分數校準回測結果（波段軌；L4）。讀快取，無則回空殼。重算用 POST /calibration/recompute。"""
    row = session.get(models.Setting, "calibration")
    if row and isinstance(row.value, dict):
        return row.value
    return {"track": "wave", "buckets": {}, "baseline": {}, "samples": 0,
            "window": {"score_dates": 0}, "horizons": [], "note": "尚未計算，請按重新計算。"}


@router.post("/calibration/recompute")
def calibration_recompute(session: Session = Depends(get_session_write)) -> dict:
    """重跑校準（較重，~分鐘級）。as-of 用最新行情日。"""
    from ..engines.calibration import CalibrationEngine

    as_of = session.execute(select(func.max(models.DailyPrice.date))).scalar() or date.today()
    return CalibrationEngine().run(session, as_of)


@router.get("/expectancy")
def expectancy(session: Session = Depends(get_session)) -> dict:
    """逐筆交易期望值回測結果（波段軌）。讀快取，重算用 POST /expectancy/recompute。"""
    row = session.get(models.Setting, "expectancy")
    if row and isinstance(row.value, dict):
        return row.value
    return {"track": "wave", "overall": {}, "by_score": [], "by_confidence": [],
            "window": {"entry_dates": 0}, "note": "尚未計算，請按重新計算。"}


@router.post("/expectancy/recompute")
def expectancy_recompute(session: Session = Depends(get_session_write)) -> dict:
    """重跑逐筆期望值回測（較重，~分鐘級）。"""
    from ..engines.expectancy import ExpectancyEngine

    as_of = session.execute(select(func.max(models.DailyPrice.date))).scalar() or date.today()
    return ExpectancyEngine().run(session, as_of)


@router.get("/param-sweep")
def param_sweep(session: Session = Depends(get_session)) -> dict:
    """出場參數掃描 + walk-forward 結果（波段軌）。讀快取。"""
    row = session.get(models.Setting, "param_sweep")
    if row and isinstance(row.value, dict):
        return row.value
    return {"track": "wave", "grid_top": [], "walkforward": {}, "note": "尚未計算，請按重新計算。"}


@router.post("/param-sweep/recompute")
def param_sweep_recompute(session: Session = Depends(get_session_write)) -> dict:
    """重跑參數掃描（最重，~數分鐘）。"""
    from ..engines.param_sweep import ParamSweepEngine

    as_of = session.execute(select(func.max(models.DailyPrice.date))).scalar() or date.today()
    return ParamSweepEngine().run(session, as_of)


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

    etf_info = None
    if stock.is_etf:
        prof = session.get(models.EtfProfile, stock_id)
        if prof:
            billion = round(prof.units * close / 1e8, 0) if (prof.units and close) else None
            etf_info = EtfInfo(
                kind=_etf_kind(prof.fund_type),
                fund_type=prof.fund_type,
                track_index=prof.track_index,
                has_foreign=prof.has_foreign,
                scale_label=_scale_label(billion),
                scale_billion=billion,
                listed_date=prof.etf_listed_date,
            )

    events = session.execute(
        select(models.Event).where(models.Event.stock_id == stock_id)
        .order_by(models.Event.date.desc(), models.Event.id.desc()).limit(10)
    ).scalars().all()

    market_td = session.execute(select(func.max(models.DailyPrice.date))).scalar()
    news_digest = (
        get_cached(session, cache_key("news_stock", stock_id, market_td)) if market_td else None
    )

    return StockDetail(
        stock_id=stock.id,
        name=stock.name,
        sector_name=sector.name if sector else None,
        market=stock.market,
        date=d,
        close=close,
        change=change,
        change_pct=change_pct,
        is_etf=stock.is_etf,
        scores=scores,
        chip=chip,
        fundamental=fundamental,
        etf=etf_info,
        events=[
            EventDTO(date=e.date, category=e.category, title=e.title, summary=e.summary,
                     is_risk=e.is_risk, source=e.source, url=e.url)
            for e in events
        ],
        news_digest=news_digest,
    )


@router.get("/stocks/{stock_id}/ohlcv", response_model=OhlcvResponse)
def stock_ohlcv(
    stock_id: str,
    days: int = Query(120, ge=20, le=3000),
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


@router.get("/stocks/{stock_id}/levels", response_model=LevelsResponse)
def stock_levels(
    stock_id: str,
    session: Session = Depends(get_session),
) -> LevelsResponse:
    """客觀支撐/壓力位（均線群+波段前低+量價套牢區，純算不靠 LLM）。"""
    from ..engines.support import levels_for_stock

    levels = levels_for_stock(session, stock_id)
    close = session.execute(
        select(models.DailyPrice.close)
        .where(models.DailyPrice.stock_id == stock_id)
        .order_by(models.DailyPrice.date.desc())
        .limit(1)
    ).scalar()
    dto = [
        LevelDTO(
            price=l.price, kind=l.kind, strength=l.strength,
            methods=l.methods, distance_pct=l.distance_pct,
        )
        for l in levels
    ]
    return LevelsResponse(
        stock_id=stock_id,
        close=float(close) if close is not None else None,
        supports=[d for d in dto if d.kind == "support"],
        resistances=[d for d in dto if d.kind == "resistance"],
    )
