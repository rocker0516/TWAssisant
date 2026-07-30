"""籌碼動向端點：法人 + 大戶散戶看市場 / 類股 / 個股方向。

市場層讀全市場三大法人總表（億元）+ 加權指數疊圖 + 量化關係；類股/個股層即時彙總
個股 institutional + shareholding。各 actor（合計/外資/投信/自營）可個別檢視。
inst_price_relation 較重（point-in-time 回測），快取進 Setting，避免每次重算。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..engines.flow_engine import FlowEngine
from ..storage import models
from .deps import get_session, get_session_write
from .schemas import (
    ChipAlertList,
    FlowStockList,
    InstPriceRelation,
    MarketFlowResponse,
    SectorFlowList,
    SectorRotationResponse,
)

_ACTOR_PARAMS = {"total", "foreign", "trust", "dealer"}

router = APIRouter(prefix="/flow", tags=["flow"])
_engine = FlowEngine()

_SORTS = {
    "total_cum20", "foreign_cum20", "trust_cum20", "dealer_cum20",
    "total_cum60", "consec_days", "big_trend", "holders_change",
    "sbl_chg20", "dt_ratio5",
}


@router.get("/market", response_model=MarketFlowResponse)
def market_flow(
    days: int = Query(250, ge=20, le=3000),
    session: Session = Depends(get_session),
) -> MarketFlowResponse:
    return MarketFlowResponse(**_engine.market_flow(session, days=days))


@router.get("/sectors", response_model=SectorFlowList)
def sector_flow(
    lookback: int = Query(20, ge=5, le=120),
    session: Session = Depends(get_session),
) -> SectorFlowList:
    return SectorFlowList(**_engine.sector_flow(session, lookback=lookback))


@router.get("/rotation", response_model=SectorRotationResponse)
def sector_rotation(
    actor: str = Query("total"),
    weeks: int = Query(6, ge=2, le=16),
    session: Session = Depends(get_session),
) -> SectorRotationResponse:
    if actor not in _ACTOR_PARAMS:
        actor = "total"
    return SectorRotationResponse(**_engine.sector_rotation(session, actor=actor, weeks=weeks))


@router.get("/stocks", response_model=FlowStockList)
def stock_flow(
    sort: str = Query("total_cum20"),
    limit: int = Query(50, ge=1, le=300),
    session: Session = Depends(get_session),
) -> FlowStockList:
    if sort not in _SORTS:
        sort = "total_cum20"
    return FlowStockList(**_engine.stock_flow_ranking(session, sort=sort, limit=limit))


@router.get("/alerts", response_model=ChipAlertList)
def chip_alerts(session: Session = Depends(get_session)) -> ChipAlertList:
    """最新交易日籌碼異動：投信首買/連買、借券暴增、大戶連增（規則式）。"""
    return ChipAlertList(**_engine.chip_alerts(session))


@router.get("/relation", response_model=InstPriceRelation)
def inst_relation(session: Session = Depends(get_session)) -> InstPriceRelation:
    """法人累積 → 未來報酬 rank-IC / 勝率（讀快取，無則回空殼，請呼叫 recompute）。"""
    row = session.get(models.Setting, "flow_relation")
    if row and isinstance(row.value, dict):
        return InstPriceRelation(**row.value)
    return InstPriceRelation(**_engine._empty_relation(None))


@router.post("/relation/recompute", response_model=InstPriceRelation)
def recompute_relation(session: Session = Depends(get_session_write)) -> InstPriceRelation:
    """重算法人累積→未來報酬量化關係並快取（較重，數十秒）。"""
    latest = session.execute(select(func.max(models.DailyPrice.date))).scalar()
    result = _engine.inst_price_relation(session, generated_at=latest)
    row = session.get(models.Setting, "flow_relation")
    if row is None:
        session.add(models.Setting(key="flow_relation", value=result))
    else:
        row.value = result
    session.flush()
    return InstPriceRelation(**result)
