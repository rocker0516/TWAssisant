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
from . import exit_signals
from .exit_signals import ALL_SIGNALS, Hit, Position, Sev
from .thesis_engine import evaluate_thesis
from ..services.strategy_engine import evaluate as eval_conditions, trading_dates
from ..services.holding_service import HoldingService, WAVE_THESIS_DEFAULTS

_LIGHT = {"red": "🔴", "orange": "🟠", "yellow": "🟡", "green": "🟢"}

_ORANGE_UP = {"green": "orange", "yellow": "orange"}

_svc = HoldingService()


@dataclass
class ExitStatus:
    level: str  # red/orange/yellow/green
    light: str  # 🔴🟠🟡🟢
    signals: list[str] = field(default_factory=list)
    hard_stop: float | None = None
    highest: float | None = None
    drawdown_pct: float | None = None
    trail_active: bool = False
    thesis_state: str | None = None
    days_left: int | None = None
    reaudit_count: int | None = None
    target_price: float | None = None
    stop_price: float | None = None
    horizon_days: int | None = None


def _reaudit_max(session: Session) -> int:
    row = session.get(models.Setting, "exit")
    v = (row.value or {}) if row and isinstance(row.value, dict) else {}
    return int(v.get("reaudit_max", 2))


def _days_elapsed(session: Session, clock_start: date, td: date) -> int:
    return max(1, len(trading_dates(session, clock_start, td)))


def _low_today(session: Session, stock_id: str, td: date) -> float | None:
    return session.execute(
        select(models.DailyPrice.low).where(
            models.DailyPrice.stock_id == stock_id, models.DailyPrice.date == td)
    ).scalar()


def _reaudit_pass(session: Session, h: models.Holding, td: date) -> bool:
    t = h.thesis
    if t.get("source") == "strategy" and t.get("conditions"):
        hit = eval_conditions(session, t["conditions"], [td])
        return h.stock_id in hit.get(td, [])
    sc = session.execute(
        select(models.Score).where(
            models.Score.stock_id == h.stock_id, models.Score.track == "wave",
            models.Score.date <= td).order_by(models.Score.date.desc()).limit(1)
    ).scalars().first()
    return bool(sc and sc.passed_filter)


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
    score_rows = session.execute(
        select(models.Score).where(
            models.Score.stock_id == stock_id, models.Score.track == "long",
            models.Score.date <= td,
        ).order_by(models.Score.date.desc()).limit(5)
    ).scalars().all()
    long_scores = [s.total_score for s in score_rows if s.total_score is not None]
    long_passed_filter = score_rows[0].passed_filter if score_rows else None

    return StockContext(
        stock=stock, date=td, prices=prices, inds=inds, inst=inst,
        valuation=_latest(session, models.Valuation, ["pe", "pb", "dividend_yield"],
                          [models.Valuation.date.desc()], stock_id),
        revenue=_latest(session, models.RevenueMonthly, ["revenue", "yoy", "mom"],
                        [models.RevenueMonthly.year.desc(), models.RevenueMonthly.month.desc()], stock_id),
        financials=_latest(session, models.FinancialQuarter, ["eps", "gross_margin", "op_margin", "net_margin", "roe"],
                           [models.FinancialQuarter.year.desc(), models.FinancialQuarter.quarter.desc()], stock_id),
        events=list(events),
        long_scores=long_scores,
        long_passed_filter=long_passed_filter,
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

    def _load_exit_config(self, session: Session) -> None:
        row = session.get(models.Setting, "exit")
        if row and isinstance(row.value, dict):
            exit_signals.set_config(row.value)

    def evaluate(
        self, session: Session, holding: models.Holding, td: date, *, avg_cost: float, close: float
    ) -> ExitStatus:
        self._load_exit_config(session)
        if holding.track == "wave" and holding.thesis:
            return self._evaluate_wave(session, holding, td, avg_cost=avg_cost, close=close)
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
        cap = exit_signals._ACTIVE.get(holding.track, exit_signals._ACTIVE["wave"])["stop_cap"]
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

    def _evaluate_wave(
        self, session: Session, holding: models.Holding, td: date, *, avg_cost: float, close: float
    ) -> ExitStatus:
        thesis = holding.thesis
        sid = holding.stock_id
        clock_start = date.fromisoformat(thesis["clock_start"])
        hi = self.highest_since(session, sid, clock_start, td) or close
        hi = max(hi, close)
        lo = _low_today(session, sid, td)
        days = _days_elapsed(session, clock_start, td)
        ev = evaluate_thesis(
            thesis, avg_cost=avg_cost, hi_since_clock=hi, lo_today=lo,
            days_elapsed=days, reaudit_max=_reaudit_max(session),
        )

        level = ev.level
        messages = list(ev.messages)
        events = session.execute(
            select(models.Event).where(
                models.Event.stock_id == sid,
                models.Event.is_risk.is_(True),
                models.Event.category == "處置警示",
                models.Event.date >= td - timedelta(days=10),
            ).order_by(models.Event.date.desc())
        ).scalars().all()
        if events:
            level = _ORANGE_UP.get(level, level)
            messages.extend(f"處置警示：{e.title}" for e in events)

        return ExitStatus(
            level=level,
            light=_LIGHT[level],
            signals=messages,
            hard_stop=ev.stop_price,
            highest=round(hi, 2),
            drawdown_pct=round((close / hi - 1) * 100, 2) if hi else None,
            trail_active=False,
            thesis_state=ev.state,
            days_left=ev.days_left,
            reaudit_count=ev.reaudit_count,
            target_price=ev.target_price,
            stop_price=ev.stop_price,
            horizon_days=int(thesis["horizon_days"]) if thesis.get("horizon_days") is not None else None,
        )

    def run(self, session: Session, trading_date: date) -> dict:
        """ExitStep：日更所有持有中部位的最高價（移動停利用）；波段持股再做論點狀態轉移/重審/舊倉補快照。"""
        holdings = session.execute(
            select(models.Holding).where(models.Holding.status == "open")
        ).scalars().all()
        updated = 0
        for h in holdings:
            hp = self.highest_since(session, h.stock_id, h.opened_date, trading_date)
            if hp is not None:
                h.highest_price = hp
                updated += 1

            if h.track != "wave":
                continue
            if h.thesis is None:  # 一次性遷移：舊持股補預設快照
                h.thesis = {**WAVE_THESIS_DEFAULTS, "source": "manual",
                            "clock_start": trading_date.isoformat(),
                            "reaudit_count": 0, "state": "active"}
                continue
            if h.thesis.get("state") in ("fulfilled", "expired", "refuted"):
                continue

            pos = _svc.position(session, h)
            if pos.shares <= 0 or pos.avg_cost is None:
                continue

            clock_start = date.fromisoformat(h.thesis["clock_start"])
            hi = self.highest_since(session, h.stock_id, clock_start, trading_date)
            hi = max(hi, pos.avg_cost) if hi is not None else pos.avg_cost
            lo = _low_today(session, h.stock_id, trading_date)
            days = _days_elapsed(session, clock_start, trading_date)
            rmax = _reaudit_max(session)

            ev = evaluate_thesis(h.thesis, avg_cost=pos.avg_cost, hi_since_clock=hi,
                                 lo_today=lo, days_elapsed=days, reaudit_max=rmax)
            if ev.state in ("refuted", "fulfilled", "expired"):
                h.thesis = {**h.thesis, "state": ev.state,
                            "settled_date": trading_date.isoformat()}
            elif ev.state == "awaiting_reaudit":
                if _reaudit_pass(session, h, trading_date):
                    h.thesis = {**h.thesis, "clock_start": trading_date.isoformat(),
                                "reaudit_count": int(h.thesis.get("reaudit_count", 0)) + 1}
                else:
                    h.thesis = {**h.thesis, "state": "expired",
                                "settled_date": trading_date.isoformat()}
        session.flush()
        return {"status": "ok", "open_holdings": len(holdings), "updated_highest": updated}
