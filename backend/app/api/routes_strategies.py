"""回測實驗室 API（spec 2026-08-20-backtest-lab ③④⑤）。

所有使用者資料經 UserData；查無回 404。當日清單以
(策略id, 條件雜湊, 日期) 進程內快取——條件一改雜湊即變，天然失效。
"""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..services import strategy_engine as se
from ..storage import models
from ..storage.user_data import UserData
from . import schemas
from .deps import get_session, get_user_data, get_user_data_write

router = APIRouter(prefix="/lab/strategies", tags=["strategies"])

_daily_cache: dict[tuple[int, str, str], schemas.StrategyDailyResponse] = {}


def _dto(st: models.UserStrategy) -> schemas.StrategyDTO:
    return schemas.StrategyDTO(
        id=st.id, name=st.name, conditions=st.conditions or [],
        sort_field=st.sort_field, sort_desc=st.sort_desc, top_n=st.top_n,
        target_pct=st.target_pct, horizon_days=st.horizon_days,
        stop_pct=st.stop_pct, is_active=st.is_active)


def _cond_hash(st: models.UserStrategy) -> str:
    raw = json.dumps([st.conditions, st.sort_field, st.sort_desc, st.top_n],
                     sort_keys=True, default=str)
    return hashlib.md5(raw.encode()).hexdigest()[:12]


@router.get("/fields", response_model=list[schemas.FieldInfo])
def fields() -> list[schemas.FieldInfo]:
    return [schemas.FieldInfo(**m) for m in se.registry_meta()]


@router.get("/", response_model=list[schemas.StrategyDTO])
def list_strategies(ud: UserData = Depends(get_user_data)):
    return [_dto(s) for s in ud.strategies()]


@router.post("/", response_model=schemas.StrategyDTO)
def create_strategy(body: schemas.StrategyCreate,
                    ud: UserData = Depends(get_user_data_write)):
    if len(ud.strategies()) >= 20:
        raise HTTPException(400, "策略數量已達上限（20）")
    st = ud.create_strategy(**body.model_dump())
    return _dto(st)


@router.patch("/{sid}", response_model=schemas.StrategyDTO)
def patch_strategy(sid: int, body: schemas.StrategyPatch,
                   ud: UserData = Depends(get_user_data_write)):
    st = ud.strategy(sid)
    if st is None:
        raise HTTPException(404, "not found")
    data = body.model_dump(exclude_unset=True, exclude={"clear_stop"})
    if "conditions" in data:
        data["conditions"] = [dict(c) for c in data["conditions"]]
        try:
            se._validate(data["conditions"])  # 寫入前擋掉未知欄位/op
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    for k, v in data.items():
        setattr(st, k, v)
    if body.clear_stop:
        st.stop_pct = None
    return _dto(st)


@router.delete("/{sid}")
def delete_strategy(sid: int, ud: UserData = Depends(get_user_data_write)):
    if not ud.delete_strategy(sid):
        raise HTTPException(404, "not found")
    return {"ok": True}


@router.post("/{sid}/activate", response_model=schemas.StrategyDTO)
def activate(sid: int, ud: UserData = Depends(get_user_data_write)):
    st = ud.set_active_strategy(sid)
    if st is None:
        raise HTTPException(404, "not found")
    return _dto(st)


@router.post("/deactivate")
def deactivate(ud: UserData = Depends(get_user_data_write)):
    st = ud.active_strategy()
    if st is not None:
        st.is_active = False
    return {"ok": True}


@router.post("/{sid}/backtest", response_model=schemas.BacktestResponse)
def backtest(sid: int, body: schemas.BacktestRequest,
             ud: UserData = Depends(get_user_data),
             session: Session = Depends(get_session)):
    st = ud.strategy(sid)
    if st is None:
        raise HTTPException(404, "not found")
    if not st.conditions:
        raise HTTPException(400, "策略沒有任何條件")
    if body.end <= body.start:
        raise HTTPException(400, "結束日需晚於起始日")
    if (body.end - body.start).days > 366:
        raise HTTPException(400, "回測範圍上限 12 個月")
    r = se.run_backtest(
        session, st.conditions, st.sort_field, st.sort_desc, st.top_n,
        st.target_pct, st.horizon_days, st.stop_pct, body.start, body.end)
    return schemas.BacktestResponse(**r.__dict__)


@router.get("/active/daily", response_model=schemas.StrategyDailyResponse)
def active_daily(ud: UserData = Depends(get_user_data),
                 session: Session = Depends(get_session)):
    st = ud.active_strategy()
    if st is None or not st.conditions:
        return schemas.StrategyDailyResponse(strategy=None, date=None, items=[])
    latest = session.execute(
        select(models.DailyPrice.date).order_by(models.DailyPrice.date.desc()).limit(1)
    ).scalar()
    if latest is None:
        return schemas.StrategyDailyResponse(strategy=_dto(st), date=None, items=[])
    key = (st.id, _cond_hash(st), latest.isoformat())
    if key in _daily_cache:
        return _daily_cache[key]

    cands = se.evaluate(session, st.conditions, [latest]).get(latest, [])
    sort_s = se.FIELD_REGISTRY[st.sort_field].loader(session, [latest])
    cands = sorted(cands, key=lambda s: sort_s.get((s, latest), float("-inf")),
                   reverse=st.sort_desc)[: st.top_n]
    rows = dict(session.execute(
        select(models.DailyPrice.stock_id, models.DailyPrice.close)
        .where(models.DailyPrice.stock_id.in_(cands),
               models.DailyPrice.date == latest)).all()) if cands else {}
    names = dict(session.execute(
        select(models.Stock.id, models.Stock.name)
        .where(models.Stock.id.in_(cands))).all()) if cands else {}
    resp = schemas.StrategyDailyResponse(
        strategy=_dto(st), date=latest.isoformat(),
        items=[schemas.StrategyDailyItem(
            stock_id=s, name=names.get(s, s), close=rows.get(s),
            sort_value=sort_s.get((s, latest))) for s in cands])
    _daily_cache.clear()  # 只留最新一份，避免無界成長
    _daily_cache[key] = resp
    return resp
