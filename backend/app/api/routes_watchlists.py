"""觀察清單端點（P6）：與持股對稱（盯進場）。多組清單 + 進場狀態燈 + 一鍵轉持股。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..services.holding_service import HoldingService
from ..services.settings_service import SettingsService
from ..storage import models
from .deps import get_session, get_session_write
from .schemas import (
    ToHolding,
    WatchlistCreate,
    WatchlistDTO,
    WatchlistItemCreate,
    WatchlistItemDTO,
    WatchlistsResponse,
)

router = APIRouter(tags=["watchlists"])
_settings = SettingsService()
_holding = HoldingService()


def _market_date(session: Session) -> date | None:
    return session.execute(select(func.max(models.DailyPrice.date))).scalar()


def _build_item(session: Session, it: models.WatchlistItem, td: date | None, thresholds: dict) -> WatchlistItemDTO:
    stock = session.get(models.Stock, it.stock_id)
    rows = session.execute(
        select(models.DailyPrice.close)
        .where(models.DailyPrice.stock_id == it.stock_id, models.DailyPrice.date <= td)
        .order_by(models.DailyPrice.date.desc()).limit(2)
    ).scalars().all() if td else []
    close = rows[0] if rows else None
    change_pct = round((rows[0] - rows[1]) / rows[1] * 100, 2) if len(rows) > 1 and rows[1] else None

    w = session.get(models.Score, {"stock_id": it.stock_id, "date": td, "track": "wave"}) if td else None
    ll = session.get(models.Score, {"stock_id": it.stock_id, "date": td, "track": "long"}) if td else None
    wave_score = w.total_score if w else None
    long_score = ll.total_score if ll else None
    passed = bool((w and w.passed) or (ll and ll.passed))
    best = max([s for s in (wave_score, long_score) if s is not None], default=None)
    wave_th = thresholds.get("wave", 70)

    to_target = it.target_price is not None and close is not None and close >= it.target_price
    near_target = it.target_price is not None and close is not None and close >= it.target_price * 0.97
    has_risk = session.execute(
        select(func.count()).select_from(models.Event)
        .where(models.Event.stock_id == it.stock_id, models.Event.is_risk.is_(True))
    ).scalar_one() > 0

    reminders: list[str] = []
    if passed:
        reminders.append("評分達門檻")
    if to_target:
        reminders.append("到目標價")
    if has_risk:
        reminders.append("重大消息")

    if passed or to_target:
        light = "green"
    elif (best is not None and best >= wave_th - 5) or near_target:
        light = "yellow"
    else:
        light = "white"

    return WatchlistItemDTO(
        id=it.id, stock_id=it.stock_id, name=stock.name if stock else it.stock_id,
        added_price=it.added_price, target_price=it.target_price, added_date=it.added_date,
        reason=it.reason, note=it.note, close=close, change_pct=change_pct,
        wave_score=wave_score, long_score=long_score, light=light, reminders=reminders,
    )


_LIGHT_ORDER = {"green": 0, "yellow": 1, "white": 2}


def _thresholds(session: Session) -> dict[str, float]:
    # 波段軌改 top_pct 制後已無 threshold 鍵：門檻分數 = 100 − top_pct；長線軌仍為 threshold
    scoring = _settings.get(session, "scoring")
    return {
        "wave": 100 - scoring.get("wave", {}).get("top_pct", 20),
        "long": scoring.get("long", {}).get("threshold", 70),
    }


@router.get("/watchlists", response_model=WatchlistsResponse)
def list_watchlists(session: Session = Depends(get_session)) -> WatchlistsResponse:
    td = _market_date(session)
    thresholds = _thresholds(session)
    lists = session.execute(select(models.Watchlist).order_by(models.Watchlist.id)).scalars().all()
    out = []
    for wl in lists:
        items = [_build_item(session, it, td, thresholds) for it in wl.items]
        items.sort(key=lambda i: _LIGHT_ORDER.get(i.light, 9))
        out.append(WatchlistDTO(id=wl.id, name=wl.name, items=items))
    return WatchlistsResponse(watchlists=out)


@router.post("/watchlists", response_model=WatchlistDTO)
def create_watchlist(body: WatchlistCreate, session: Session = Depends(get_session_write)) -> WatchlistDTO:
    wl = models.Watchlist(name=body.name)
    session.add(wl)
    session.flush()
    return WatchlistDTO(id=wl.id, name=wl.name, items=[])


@router.delete("/watchlists/{wl_id}")
def delete_watchlist(wl_id: int, session: Session = Depends(get_session_write)) -> dict:
    wl = session.get(models.Watchlist, wl_id)
    if wl is None:
        raise HTTPException(404, "清單不存在")
    session.delete(wl)
    return {"ok": True}


@router.post("/watchlists/{wl_id}/items", response_model=WatchlistItemDTO)
def add_item(wl_id: int, body: WatchlistItemCreate, session: Session = Depends(get_session_write)) -> WatchlistItemDTO:
    if session.get(models.Watchlist, wl_id) is None:
        raise HTTPException(404, "清單不存在")
    if session.get(models.Stock, body.stock_id) is None:
        raise HTTPException(404, f"找不到股票 {body.stock_id}")
    it = models.WatchlistItem(
        watchlist_id=wl_id, stock_id=body.stock_id, target_price=body.target_price,
        added_price=body.added_price, added_date=body.added_date or date.today(),
        reason=body.reason, note=body.note,
    )
    session.add(it)
    session.flush()
    td = _market_date(session)
    thresholds = _thresholds(session)
    return _build_item(session, it, td, thresholds)


@router.delete("/watchlist-items/{item_id}")
def delete_item(item_id: int, session: Session = Depends(get_session_write)) -> dict:
    it = session.get(models.WatchlistItem, item_id)
    if it is None:
        raise HTTPException(404, "項目不存在")
    session.delete(it)
    return {"ok": True}


@router.post("/watchlist-items/{item_id}/to-holding")
def item_to_holding(item_id: int, body: ToHolding, session: Session = Depends(get_session_write)) -> dict:
    it = session.get(models.WatchlistItem, item_id)
    if it is None:
        raise HTTPException(404, "項目不存在")
    h = _holding.create(
        session, stock_id=it.stock_id, track=body.track, date_=body.date,
        price=body.price, shares=body.shares,
    )
    session.delete(it)  # 轉持股後從觀察清單移除
    return {"ok": True, "holding_id": h.id}
