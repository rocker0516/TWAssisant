"""Level 2 live paper 帳戶端點（FRS v1.1 §11/§14）。

主 KPI＝成本後絕對報酬；硬約束＝MDD ≤ 大盤同期 MDD；vs 加權指數為診斷欄
（誠實揭露、不作及格線——§14 使用者核可之修訂）。數字一律後端計算
（metrics 與回測共用），前端只 render。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

import pandas as pd

from ..research.level2 import metrics as mt
from ..storage import models
from .deps import get_session

router = APIRouter(prefix="/level2", tags=["level2"])

ACCOUNT_NAME = "P5_live"   # 與 scripts/level2_paper.py 一致


class NavPoint(BaseModel):
    date: str
    nav: float
    benchmark: float | None   # 同起點指數化後的大盤（診斷欄）


class Level2Summary(BaseModel):
    account: str
    policy_version: str
    start_date: str | None
    days: int
    initial_cash: float
    nav: float | None
    cash: float | None
    return_pct: float | None          # 主 KPI：絕對報酬
    mdd_pct: float | None
    bench_mdd_pct: float | None
    mdd_within_bench: bool | None     # 硬約束
    excess_vs_bench_pct: float | None  # 診斷欄
    total_costs: float
    n_fills: int
    n_defense_exits: int
    series: list[NavPoint]


class PositionRow(BaseModel):
    stock_id: str
    name: str | None
    qty: int
    close: float | None
    market_value: float | None
    weight_pct: float | None


class OrderRow(BaseModel):
    created_date: str
    stock_id: str
    side: str
    qty: int
    reason: str
    status: str
    trade_date: str | None
    price: float | None


def _account(s: Session) -> models.Level2Account:
    acct = s.scalar(select(models.Level2Account)
                    .where(models.Level2Account.name == ACCOUNT_NAME))
    if acct is None:
        raise HTTPException(404, "帳戶尚未建立（首次 21:30 排程後出現）")
    return acct


@router.get("/summary", response_model=Level2Summary)
def summary(s: Session = Depends(get_session)) -> Level2Summary:
    acct = _account(s)
    rows = s.execute(select(models.Level2Nav)
                     .where(models.Level2Nav.account_id == acct.id)
                     .order_by(models.Level2Nav.date)).scalars().all()
    fills = s.execute(select(models.Level2Order)
                      .where(models.Level2Order.account_id == acct.id,
                             models.Level2Order.status == "filled")).scalars().all()
    costs = sum((f.fee or 0) + (f.tax or 0) for f in fills)
    n_def = sum(1 for f in fills if f.reason == "defense")
    base = Level2Summary(
        account=acct.name, policy_version=acct.policy_version,
        start_date=str(acct.start_date) if acct.start_date else None,
        days=len(rows), initial_cash=acct.initial_cash,
        nav=None, cash=None, return_pct=None, mdd_pct=None,
        bench_mdd_pct=None, mdd_within_bench=None, excess_vs_bench_pct=None,
        total_costs=round(costs, 0), n_fills=len(fills),
        n_defense_exits=n_def, series=[])
    if not rows:
        return base
    nav = pd.Series([r.nav for r in rows], index=[str(r.date) for r in rows])
    bench = pd.Series([r.benchmark_close for r in rows],
                      index=nav.index, dtype=float).ffill()
    bench_idx = (bench / bench.iloc[0] * nav.iloc[0]
                 if bench.notna().any() else None)
    base.nav = round(float(nav.iloc[-1]), 0)
    base.cash = round(rows[-1].cash, 0)
    base.return_pct = round(float(nav.iloc[-1] / acct.initial_cash - 1) * 100, 2)
    base.mdd_pct = round(mt.max_drawdown(nav) * 100, 2)
    if bench_idx is not None:
        base.bench_mdd_pct = round(mt.max_drawdown(bench_idx) * 100, 2)
        base.mdd_within_bench = bool(mt.max_drawdown(nav)
                                     >= mt.max_drawdown(bench_idx))
        base.excess_vs_bench_pct = round(
            (float(nav.iloc[-1] / nav.iloc[0]
                   - bench_idx.iloc[-1] / bench_idx.iloc[0])) * 100, 2)
    base.series = [NavPoint(date=d, nav=round(float(nav[d]), 0),
                            benchmark=(round(float(bench_idx[d]), 0)
                                       if bench_idx is not None else None))
                   for d in nav.index]
    return base


@router.get("/positions", response_model=list[PositionRow])
def positions(s: Session = Depends(get_session)) -> list[PositionRow]:
    acct = _account(s)
    last = s.scalar(select(models.Level2Position.date)
                    .where(models.Level2Position.account_id == acct.id)
                    .order_by(models.Level2Position.date.desc()).limit(1))
    if last is None:
        return []
    rows = s.execute(select(models.Level2Position, models.Stock.name)
                     .join(models.Stock,
                           models.Stock.id == models.Level2Position.stock_id,
                           isouter=True)
                     .where(models.Level2Position.account_id == acct.id,
                            models.Level2Position.date == last)).all()
    total = sum(p.market_value or 0 for p, _ in rows) + acct.cash
    return [PositionRow(
        stock_id=p.stock_id, name=n, qty=p.qty, close=p.close,
        market_value=p.market_value,
        weight_pct=(round(p.market_value / total * 100, 2)
                    if p.market_value and total else None))
        for p, n in sorted(rows, key=lambda x: -(x[0].market_value or 0))]


@router.get("/orders", response_model=list[OrderRow])
def orders(s: Session = Depends(get_session)) -> list[OrderRow]:
    acct = _account(s)
    rows = s.execute(select(models.Level2Order)
                     .where(models.Level2Order.account_id == acct.id)
                     .order_by(models.Level2Order.id.desc())
                     .limit(50)).scalars().all()
    return [OrderRow(created_date=str(r.created_date), stock_id=r.stock_id,
                     side=r.side, qty=r.qty, reason=r.reason, status=r.status,
                     trade_date=str(r.trade_date) if r.trade_date else None,
                     price=r.price) for r in rows]
