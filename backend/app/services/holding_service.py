"""HoldingService（架構⑥）：持股寫入業務邏輯。

成本不存欄位，由 transactions 重算均價（支援加碼算平均成本、分批賣部分張數）。
賣到 0 張 → 結算實現損益、holding 轉 closed。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..storage import models


@dataclass
class Position:
    shares: int  # 目前持有張數
    avg_cost: float | None  # 平均成本（含手續費）
    realized_pnl: float  # 已實現損益（分批賣累計）
    total_buy_shares: int  # 累計買進張數（算報酬率分母參考）


def position_from_txns(txns: list[models.Transaction]) -> Position:
    """以平均成本法重算部位。txns 需依時間升冪。"""
    shares = 0
    cost = 0.0  # 目前持倉總成本
    realized = 0.0
    bought = 0
    for t in sorted(txns, key=lambda x: (x.date, x.id or 0)):
        if t.type in ("buy", "add"):
            shares += t.shares
            cost += t.shares * t.price + (t.fee or 0)
            bought += t.shares
        elif t.type == "sell" and shares > 0:
            avg = cost / shares
            sold = min(t.shares, shares)
            realized += sold * (t.price - avg) - (t.fee or 0) - (t.tax or 0)
            cost -= avg * sold
            shares -= sold
    avg_cost = round(cost / shares, 2) if shares > 0 else None
    return Position(shares=shares, avg_cost=avg_cost, realized_pnl=round(realized, 0), total_buy_shares=bought)


def _capture_entry_snapshot(
    session: Session, stock_id: str, track: str, date_: date
) -> dict | None:
    """凍結建倉當下該軌最新 Score（≤ 建倉日），供之後論點對照。查無評分回 None。"""
    sc = session.execute(
        select(models.Score)
        .where(
            models.Score.stock_id == stock_id,
            models.Score.track == track,
            models.Score.date <= date_,
        )
        .order_by(models.Score.date.desc())
        .limit(1)
    ).scalars().first()
    if sc is None:
        return None
    close = session.execute(
        select(models.DailyPrice.close)
        .where(models.DailyPrice.stock_id == stock_id, models.DailyPrice.date <= date_)
        .order_by(models.DailyPrice.date.desc())
        .limit(1)
    ).scalar()
    return {
        "score_date": sc.date.isoformat(),
        "total_score": sc.total_score,
        "passed_filter": sc.passed_filter,
        "passed_styles": sc.passed_styles,
        "reasons": sc.reasons,
        "buy_low": sc.buy_low,
        "buy_high": sc.buy_high,
        "stop_loss": sc.stop_loss,
        "close": close,
    }


# stop_pct=None＝波段軌不設停損（2026-08-24 定版，見 engines/stoploss.py docstring）：
# −8% 停損實測把命中率打掉 25pp，風控改由 horizon_days 到期重審承擔。
WAVE_THESIS_DEFAULTS = {"target_pct": 10.0, "horizon_days": 10, "stop_pct": None}


def _capture_thesis(session: Session, *, user_id: int, track: str,
                    date_: date, strategy_id: int | None) -> dict | None:
    """波段建倉論點快照。strategy 來源凍結該策略參數與條件；其餘用全域預設。"""
    if track != "wave":
        return None
    base = {"clock_start": date_.isoformat(), "reaudit_count": 0, "state": "active"}
    if strategy_id is not None:
        st = session.get(models.UserStrategy, strategy_id)
        if st is not None and st.user_id == user_id:
            return {**base, "source": "strategy", "strategy_id": st.id,
                    "conditions": list(st.conditions or []),
                    "target_pct": st.target_pct, "horizon_days": st.horizon_days,
                    # 策略明示的 stop_pct 照走（使用者在實驗室自己設的）；沒設就等於無停損
                    "stop_pct": st.stop_pct}
    row = session.get(models.Setting, "exit")
    cfg = (row.value or {}).get("wave_defaults", {}) if row and isinstance(row.value, dict) else {}
    return {**base, "source": "manual", **{**WAVE_THESIS_DEFAULTS, **{k: v for k, v in cfg.items() if v is not None}}}


class HoldingService:
    def create(
        self,
        session: Session,
        *,
        user_id: int,
        stock_id: str,
        track: str,
        date_: date,
        price: float,
        shares: int,
        fee: float | None = None,
        stop_loss_override: float | None = None,
        trail_trigger_override: float | None = None,
        trail_pullback_override: float | None = None,
        note: str | None = None,
        strategy_id: int | None = None,
    ) -> models.Holding:
        holding = models.Holding(
            user_id=user_id,
            stock_id=stock_id,
            track=track,
            status="open",
            opened_date=date_,
            entry_snapshot=_capture_entry_snapshot(session, stock_id, track, date_),
            thesis=_capture_thesis(session, user_id=user_id, track=track, date_=date_, strategy_id=strategy_id),
            stop_loss_override=stop_loss_override,
            trail_trigger_override=trail_trigger_override,
            trail_pullback_override=trail_pullback_override,
            note=note,
        )
        session.add(holding)
        session.flush()
        session.add(
            models.Transaction(
                user_id=user_id, holding_id=holding.id,
                type="buy", date=date_, price=price, shares=shares, fee=fee,
            )
        )
        session.flush()
        return holding

    def add_transaction(
        self,
        session: Session,
        holding: models.Holding,
        *,
        type_: str,
        date_: date,
        price: float,
        shares: int,
        fee: float | None = None,
        tax: float | None = None,
        note: str | None = None,
    ) -> models.Holding:
        """收 Holding 物件而非 id：ownership 檢查在取得物件那一步（UserData）
        就完成了——這裡再收 id 重查，等於留一條繞過 scope 的路。"""
        session.add(
            models.Transaction(
                user_id=holding.user_id, holding_id=holding.id,
                type=type_, date=date_, price=price,
                shares=shares, fee=fee, tax=tax, note=note,
            )
        )
        session.flush()
        self._refresh_status(session, holding, on_date=date_)
        return holding

    def _refresh_status(self, session: Session, holding: models.Holding, on_date: date) -> None:
        pos = self.position(session, holding)
        if pos.shares <= 0:
            holding.status = "closed"
            holding.closed_date = on_date
            holding.realized_pnl = pos.realized_pnl
        else:
            holding.status = "open"
            holding.closed_date = None

    def position(self, session: Session, holding: models.Holding) -> Position:
        txns = session.execute(
            select(models.Transaction).where(models.Transaction.holding_id == holding.id)
        ).scalars().all()
        return position_from_txns(list(txns))
