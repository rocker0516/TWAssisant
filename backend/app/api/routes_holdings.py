"""持股端點（P2）：讀寫。寫入走 Service（HoldingService），讀出整合 ExitEngine 狀態。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ..engines.exit_engine import ExitEngine
from ..services.holding_service import HoldingService
from ..storage import models
from .deps import get_session, get_session_write
from .schemas import (
    HoldingCreate,
    HoldingItem,
    HoldingPatch,
    HoldingsResponse,
    HoldingsSummary,
    ThesisStatus,
    TransactionCreate,
    TransactionDTO,
)

router = APIRouter(prefix="/holdings", tags=["holdings"])

LOT = 1000  # 1 張 = 1000 股
_svc = HoldingService()
_exit = ExitEngine()


def _raise_if_db_locked(exc: OperationalError) -> None:
    """背景 pipeline 卡住 SQLite writer 鎖 → 把難看的 500 翻成 503 + 中文提示。"""
    if "database is locked" in str(exc.orig).lower():
        raise HTTPException(503, "資料更新中（背景補資料），請稍候約 1 分鐘後再試一次") from exc


def _market_date(session: Session) -> date | None:
    return session.execute(select(func.max(models.DailyPrice.date))).scalar()


def _close_change(session: Session, stock_id: str, td: date) -> tuple[float | None, float | None]:
    rows = session.execute(
        select(models.DailyPrice.close)
        .where(models.DailyPrice.stock_id == stock_id, models.DailyPrice.date <= td)
        .order_by(models.DailyPrice.date.desc()).limit(2)
    ).scalars().all()
    if not rows:
        return None, None
    close = rows[0]
    if len(rows) < 2 or not rows[1] or close is None:
        return close, None
    return close, round((close - rows[1]) / rows[1] * 100, 2)


def _eval_thesis(session: Session, h: models.Holding) -> ThesisStatus | None:
    """進場論點是否還成立：進場快照分數 vs 該軌最新分數 + 硬篩狀態。

    broken＝硬篩掉了且分數明顯崩（<進場×0.85）；weakening＝硬篩掉或分數下滑（<×0.9）。
    無快照或無最新評分＝unknown（不誤導）。
    """
    snap = h.entry_snapshot
    if not snap or snap.get("total_score") is None:
        return None
    latest = session.execute(
        select(models.Score)
        .where(models.Score.stock_id == h.stock_id, models.Score.track == h.track)
        .order_by(models.Score.date.desc())
        .limit(1)
    ).scalars().first()
    entry_score = float(snap["total_score"])
    if latest is None or latest.total_score is None:
        return ThesisStatus(status="unknown", entry_score=entry_score,
                            latest_score=None, latest_passed_filter=None,
                            messages=["查無最新評分"])
    cur = float(latest.total_score)
    passed = bool(latest.passed_filter)
    msgs: list[str] = []
    if not passed:
        msgs.append("已不過硬篩")
    if cur < entry_score * 0.9:
        msgs.append(f"分數自進場 {entry_score:.1f} 降至 {cur:.1f}")
    if not passed and cur < entry_score * 0.85:
        status = "broken"
        msgs.append("進場理由已失效，建議重新評估")
    elif msgs:
        status = "weakening"
    else:
        status = "intact"
    return ThesisStatus(status=status, entry_score=round(entry_score, 2),
                        latest_score=round(cur, 2), latest_passed_filter=passed,
                        messages=msgs)


def build_item(session: Session, h: models.Holding, td: date | None) -> HoldingItem:
    pos = _svc.position(session, h)
    stock = session.get(models.Stock, h.stock_id)
    close, change_pct = (_close_change(session, h.stock_id, td) if td else (None, None))

    market_value = unrealized = return_pct = None
    if pos.shares > 0 and pos.avg_cost and close is not None:
        market_value = round(pos.shares * LOT * close)
        unrealized = round(market_value - pos.shares * LOT * pos.avg_cost)
        return_pct = round((close / pos.avg_cost - 1) * 100, 2)

    if pos.shares > 0 and pos.avg_cost and close is not None and td:
        st = _exit.evaluate(session, h, td, avg_cost=pos.avg_cost, close=close)
    else:
        from ..engines.exit_engine import ExitStatus

        st = ExitStatus(level="green", light="🟢", signals=[], hard_stop=None,
                        highest=None, drawdown_pct=None, trail_active=False)

    txns = sorted(h.transactions, key=lambda t: (t.date, t.id or 0))
    return HoldingItem(
        id=h.id, stock_id=h.stock_id, name=stock.name if stock else h.stock_id, track=h.track,
        status=h.status, opened_date=h.opened_date, closed_date=h.closed_date,
        shares=pos.shares, avg_cost=pos.avg_cost, close=close, change_pct=change_pct,
        market_value=market_value, unrealized_pnl=unrealized, return_pct=return_pct,
        realized_pnl=round((h.realized_pnl if h.realized_pnl is not None else pos.realized_pnl) * LOT),
        light=st.light, level=st.level, signals=st.signals, hard_stop=st.hard_stop,
        highest=st.highest, drawdown_pct=st.drawdown_pct, trail_active=st.trail_active,
        stop_loss_override=h.stop_loss_override, trail_trigger_override=h.trail_trigger_override,
        trail_pullback_override=h.trail_pullback_override,
        entry_snapshot=h.entry_snapshot,
        thesis=_eval_thesis(session, h) if h.status == "open" else None,
        note=h.note,
        transactions=[
            TransactionDTO(id=t.id, type=t.type, date=t.date, price=t.price, shares=t.shares,
                           fee=t.fee, tax=t.tax, note=t.note)
            for t in txns
        ],
    )


_LEVEL_ORDER = {"red": 0, "orange": 1, "yellow": 2, "green": 3}


@router.get("", response_model=HoldingsResponse)
def list_holdings(
    status: str = Query("open", pattern="^(open|closed)$"),
    session: Session = Depends(get_session),
) -> HoldingsResponse:
    td = _market_date(session)
    holdings = session.execute(
        select(models.Holding).where(models.Holding.status == status)
    ).scalars().all()
    items = [build_item(session, h, td) for h in holdings]
    # 緊急優先排序（🔴在上）
    items.sort(key=lambda i: (_LEVEL_ORDER.get(i.level, 9), -(i.return_pct or 0)))

    mv = sum(i.market_value or 0 for i in items)
    up = sum(i.unrealized_pnl or 0 for i in items)
    cost = mv - up
    summary = HoldingsSummary(
        count=len(items),
        total_market_value=mv,
        total_unrealized_pnl=up,
        total_return_pct=round(up / cost * 100, 2) if cost else None,
        total_realized_pnl=sum(i.realized_pnl or 0 for i in items),
    )
    return HoldingsResponse(status=status, items=items, summary=summary)


@router.post("", response_model=HoldingItem)
def create_holding(body: HoldingCreate, session: Session = Depends(get_session_write)) -> HoldingItem:
    if session.get(models.Stock, body.stock_id) is None:
        raise HTTPException(404, f"找不到股票 {body.stock_id}")
    try:
        h = _svc.create(
            session, stock_id=body.stock_id, track=body.track, date_=body.date,
            price=body.price, shares=body.shares, fee=body.fee,
            stop_loss_override=body.stop_loss_override,
            trail_trigger_override=body.trail_trigger_override,
            trail_pullback_override=body.trail_pullback_override, note=body.note,
        )
    except OperationalError as e:
        _raise_if_db_locked(e)
        raise
    return build_item(session, h, _market_date(session))


@router.post("/{holding_id}/transactions", response_model=HoldingItem)
def add_transaction(
    holding_id: int, body: TransactionCreate, session: Session = Depends(get_session_write)
) -> HoldingItem:
    if body.type not in ("add", "sell", "buy"):
        raise HTTPException(400, "type 必須為 add / sell")
    try:
        h = _svc.add_transaction(
            session, holding_id, type_=body.type, date_=body.date, price=body.price,
            shares=body.shares, fee=body.fee, tax=body.tax, note=body.note,
        )
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    except OperationalError as e:
        _raise_if_db_locked(e)
        raise
    return build_item(session, h, _market_date(session))


@router.patch("/{holding_id}", response_model=HoldingItem)
def patch_holding(
    holding_id: int, body: HoldingPatch, session: Session = Depends(get_session_write)
) -> HoldingItem:
    h = session.get(models.Holding, holding_id)
    if h is None:
        raise HTTPException(404, f"持股 {holding_id} 不存在")
    for field, val in body.model_dump(exclude_unset=True).items():
        setattr(h, field, val)
    try:
        session.flush()
    except OperationalError as e:
        _raise_if_db_locked(e)
        raise
    return build_item(session, h, _market_date(session))


@router.delete("/{holding_id}")
def delete_holding(holding_id: int, session: Session = Depends(get_session_write)) -> dict:
    h = session.get(models.Holding, holding_id)
    if h is None:
        raise HTTPException(404, f"持股 {holding_id} 不存在")
    try:
        session.delete(h)
        session.flush()
    except OperationalError as e:
        _raise_if_db_locked(e)
        raise
    return {"ok": True}
