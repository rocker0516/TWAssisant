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


class HoldingService:
    def create(
        self,
        session: Session,
        *,
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
    ) -> models.Holding:
        holding = models.Holding(
            stock_id=stock_id,
            track=track,
            status="open",
            opened_date=date_,
            stop_loss_override=stop_loss_override,
            trail_trigger_override=trail_trigger_override,
            trail_pullback_override=trail_pullback_override,
            note=note,
        )
        session.add(holding)
        session.flush()
        session.add(
            models.Transaction(
                holding_id=holding.id, type="buy", date=date_, price=price, shares=shares, fee=fee
            )
        )
        session.flush()
        return holding

    def add_transaction(
        self,
        session: Session,
        holding_id: int,
        *,
        type_: str,
        date_: date,
        price: float,
        shares: int,
        fee: float | None = None,
        tax: float | None = None,
        note: str | None = None,
    ) -> models.Holding:
        holding = session.get(models.Holding, holding_id)
        if holding is None:
            raise ValueError(f"持股 {holding_id} 不存在")
        session.add(
            models.Transaction(
                holding_id=holding_id, type=type_, date=date_, price=price,
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
