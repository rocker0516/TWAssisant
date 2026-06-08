"""ExitEngine（架構③）：持股出場評估。

evaluate() 把單檔資料打包成 StockContext，跑可插拔 ExitSignal，_aggregate 聚合成
分級狀態燈（多訊號整合成一個結論，不洗版）。run() 為 ExitStep：日更持有最高價。
供 API（持股頁即時狀態）與 NotifyStep（Discord 🔴🟠 提醒）共用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..storage import models
from .base import BaseEngine
from .context import StockContext
from .exit_signals import ALL_SIGNALS, Hit, Position, Sev
from .stoploss import _CAP  # 共用停損上限

_LIGHT = {"red": "🔴", "orange": "🟠", "yellow": "🟡", "green": "🟢"}


@dataclass
class ExitStatus:
    level: str  # red/orange/yellow/green
    light: str  # 🔴🟠🟡🟢
    signals: list[str] = field(default_factory=list)
    hard_stop: float | None = None
    highest: float | None = None
    drawdown_pct: float | None = None
    trail_active: bool = False


def _aggregate(hits: list[Hit]) -> str:
    if any(h.sev == Sev.CRITICAL for h in hits):
        return "red"
    if any(h.sev == Sev.WARN for h in hits):
        return "orange"
    early = sum(1 for h in hits if h.sev == Sev.EARLY)
    if early >= 2:
        return "orange"  # 多訊號
    if early == 1:
        return "yellow"
    return "green"


def _latest(session: Session, model, cols, order, stock_id: str) -> pd.Series | None:
    row = session.execute(
        select(model).where(model.stock_id == stock_id).order_by(*order).limit(1)
    ).scalars().first()
    return pd.Series({c: getattr(row, c) for c in cols}) if row else None


def _build_context(session: Session, stock_id: str, td: date) -> StockContext | None:
    stock = session.get(models.Stock, stock_id)
    if stock is None:
        return None

    def frame(model, cols):
        rows = session.execute(
            select(model).where(model.stock_id == stock_id, model.date <= td).order_by(model.date)
        ).scalars().all()
        return pd.DataFrame([{c: getattr(r, c) for c in cols} for r in rows]) if rows else pd.DataFrame(columns=cols)

    prices = frame(models.DailyPrice, ["stock_id", "date", "open", "high", "low", "close", "volume"])
    if prices.empty:
        return None
    inds = frame(models.Indicator, [
        "stock_id", "date", "ma5", "ma10", "ma20", "ma60", "vol_ma5", "vol_ma20",
        "kd_k", "kd_d", "macd", "macd_signal", "macd_hist", "atr14", "bias_20", "bias_60",
    ])
    inst = frame(models.Institutional, ["stock_id", "date", "foreign_net", "trust_net", "dealer_net", "total_net"])
    events = session.execute(
        select(models.Event).where(
            models.Event.stock_id == stock_id,
            models.Event.is_risk.is_(True),
            models.Event.date >= td - timedelta(days=10),
        ).order_by(models.Event.date.desc())
    ).scalars().all()
    return StockContext(
        stock=stock, date=td, prices=prices, inds=inds, inst=inst,
        valuation=_latest(session, models.Valuation, ["pe", "pb", "dividend_yield"],
                          [models.Valuation.date.desc()], stock_id),
        revenue=_latest(session, models.RevenueMonthly, ["revenue", "yoy", "mom"],
                        [models.RevenueMonthly.year.desc(), models.RevenueMonthly.month.desc()], stock_id),
        financials=_latest(session, models.FinancialQuarter, ["eps", "gross_margin", "op_margin", "net_margin", "roe"],
                           [models.FinancialQuarter.year.desc(), models.FinancialQuarter.quarter.desc()], stock_id),
        events=list(events),
    )


class ExitEngine(BaseEngine):
    name = "exit"

    def highest_since(self, session: Session, stock_id: str, since: date | None, td: date) -> float | None:
        stmt = select(models.DailyPrice.high).where(
            models.DailyPrice.stock_id == stock_id, models.DailyPrice.date <= td
        )
        if since is not None:
            stmt = stmt.where(models.DailyPrice.date >= since)
        highs = [h for h in session.execute(stmt).scalars().all() if h is not None]
        return max(highs) if highs else None

    def evaluate(
        self, session: Session, holding: models.Holding, td: date, *, avg_cost: float, close: float
    ) -> ExitStatus:
        highest = self.highest_since(session, holding.stock_id, holding.opened_date, td) or close
        highest = max(highest, close)
        pos = Position(shares=1, avg_cost=avg_cost, highest=highest, close=close)
        ctx = _build_context(session, holding.stock_id, td)

        hits: list[Hit] = []
        if ctx is not None:
            for sig in ALL_SIGNALS:
                if sig.applies(holding.track):
                    hits.extend(sig.check(holding, pos, ctx))

        level = _aggregate(hits)
        cap = _CAP.get(holding.track, 0.08)
        hard_stop = holding.stop_loss_override or round(avg_cost * (1 - cap), 2)
        return ExitStatus(
            level=level,
            light=_LIGHT[level],
            signals=[h.message for h in hits],
            hard_stop=hard_stop,
            highest=round(highest, 2),
            drawdown_pct=round(pos.drawdown * 100, 2),
            trail_active=pos.peak_return >= (holding.trail_trigger_override or 0.10),
        )

    def run(self, session: Session, trading_date: date) -> dict:
        """ExitStep：日更所有持有中部位的最高價（移動停利用）。"""
        holdings = session.execute(
            select(models.Holding).where(models.Holding.status == "open")
        ).scalars().all()
        updated = 0
        for h in holdings:
            hp = self.highest_since(session, h.stock_id, h.opened_date, trading_date)
            if hp is not None:
                h.highest_price = hp
                updated += 1
        session.flush()
        return {"status": "ok", "open_holdings": len(holdings), "updated_highest": updated}
