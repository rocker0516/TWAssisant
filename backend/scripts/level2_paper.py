"""Level 2 live paper pipeline（FRS v1.1 §10/§14，KPI＝絕對報酬＋MDD 約束）。

每交易日 D（21:30，Level1PredictStep 之後）：
  1) 執行前一訊號日產生的 pending/deferred 委託（D 開盤價、D-1 收盤判漲跌停）
  2) 收盤估值 → level2_positions / level2_nav（停牌股沿用前值）
  3) 由 level2_predictions?（否）level1_predictions（D 日、CURRENT_MODEL_VERSION）
     產生防禦/再平衡委託（baseline_v1 凍結參數）→ 寫 pending
帳戶自首次執行日起跑，**不回補歷史**（回補＝事後模型假戰績，Level 1 條款繼承）。
關機漏日由 catch-up 逐日補：委託仍源自各日當時的 ledger，成交用各日真實開盤價。

真相在 level2_orders（append-only）；accounts.cash / positions 快照為便利欄位，
verify_replay() 以 engine.replay 重建並比對（測試釘死＋ --verify CLI）。

用法（cwd=backend）：
  .venv/Scripts/python.exe -m scripts.level2_paper            # catch-up 到最新交易日
  .venv/Scripts/python.exe -m scripts.level2_paper --verify   # 重放一致性檢查
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.research.level2 import engine as eg  # noqa: E402
from app.research.level2.costs import CostModel  # noqa: E402
from app.research.level2.policy import (P5_PARAMS, POLICY_VERSION,  # noqa: E402
                                        plan_defense, plan_rebalance)
from app.storage import models  # noqa: E402

ACCOUNT_NAME = "P5_live"
INITIAL_CASH = 1_000_000.0
# 必須與 scripts/level1_predict.py 的 MODEL_VERSION 一致（同 routes_level1 慣例）
CURRENT_MODEL_VERSION = "l1_lgbm_v2"
COST = CostModel()


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def get_or_create_account(s: Session) -> models.Level2Account:
    acct = s.scalar(select(models.Level2Account)
                    .where(models.Level2Account.name == ACCOUNT_NAME))
    if acct is None:
        acct = models.Level2Account(
            name=ACCOUNT_NAME, policy_version=POLICY_VERSION,
            initial_cash=INITIAL_CASH, cash=INITIAL_CASH)
        s.add(acct)
        s.flush()
    return acct


def trading_days_after(s: Session, after: date | None) -> list[date]:
    q = select(models.MarketIndex.date).order_by(models.MarketIndex.date)
    if after is not None:
        q = q.where(models.MarketIndex.date > after)
    return list(s.scalars(q))


def _prices_at(s: Session, d: date, col: str) -> dict[str, float]:
    rows = s.execute(select(models.DailyPrice.stock_id,
                            getattr(models.DailyPrice, col))
                     .where(models.DailyPrice.date == d)).all()
    return {sid: v for sid, v in rows if v is not None}


def _last_positions(s: Session, acct_id: int) -> tuple[dict[str, int],
                                                       dict[str, float]]:
    """最近一個快照日的持倉與收盤（收盤供停牌股 carry-forward 估值）。"""
    last = s.scalar(select(models.Level2Position.date)
                    .where(models.Level2Position.account_id == acct_id)
                    .order_by(models.Level2Position.date.desc()).limit(1))
    if last is None:
        return {}, {}
    rows = s.execute(select(models.Level2Position)
                     .where(models.Level2Position.account_id == acct_id,
                            models.Level2Position.date == last)).scalars()
    qty, px = {}, {}
    for r in rows:
        qty[r.stock_id] = r.qty
        if r.close is not None:
            px[r.stock_id] = r.close
    return qty, px


def _ledger_pct(s: Session, d: date, horizon: int) -> pd.Series:
    rows = s.execute(
        select(models.Level1Prediction.stock_id,
               models.Level1Prediction.pct_rank)
        .where(models.Level1Prediction.prediction_date == d,
               models.Level1Prediction.horizon == horizon,
               models.Level1Prediction.model_version == CURRENT_MODEL_VERSION)
    ).all()
    return pd.Series({sid: p for sid, p in rows}, dtype=float)


def process_day(s: Session, acct: models.Level2Account, d: date,
                prev_d: date | None) -> dict:
    """處理單一交易日：執行委託 → 估值入帳 → 產生次日委託。"""
    qty0, carry_px = _last_positions(s, acct.id)
    state = eg.PortfolioState(acct.cash, dict(qty0))

    # 1) 執行 pending / deferred
    rows = list(s.execute(
        select(models.Level2Order)
        .where(models.Level2Order.account_id == acct.id,
               models.Level2Order.status.in_(("pending", "deferred")))
        .order_by(models.Level2Order.id)).scalars())
    n_filled = 0
    if rows and prev_d is not None:
        open_px = _prices_at(s, d, "open")
        prev_close = _prices_at(s, prev_d, "close")
        orders = [eg.Order(r.stock_id, r.side, r.qty, r.reason.split(":")[0])
                  for r in rows]
        state, fills = eg.execute_day(state, orders, open_px, prev_close, COST)
        by_key = {(f.stock_id, f.side): f for f in fills}
        for r in rows:
            f = by_key.get((r.stock_id, r.side))
            if f is None:
                continue
            r.status = f.status
            r.reason = f.reason
            if f.status == eg.FILLED:
                r.trade_date, r.price, r.fee, r.tax = d, f.price, f.fee, f.tax
                r.qty = f.qty          # 現金不足降量/部分持股：記實際成交量
                n_filled += 1
            elif f.status == eg.REJECTED:
                r.trade_date = d

    # 2) 收盤估值（停牌股 carry-forward 前收盤）
    close_px = _prices_at(s, d, "close")
    val_px = {sid: close_px.get(sid, carry_px.get(sid))
              for sid in state.positions}
    missing = [sid for sid, v in val_px.items() if v is None]
    if missing:
        raise SystemExit(f"持股 {missing} 無任何可用收盤價——資料破洞，停機檢查")
    nav = state.cash + sum(q * val_px[sid]
                           for sid, q in state.positions.items())
    for sid, q in state.positions.items():
        s.add(models.Level2Position(account_id=acct.id, date=d, stock_id=sid,
                                    qty=q, close=val_px[sid],
                                    market_value=q * val_px[sid]))
    bench = s.scalar(select(models.MarketIndex.close)
                     .where(models.MarketIndex.date == d))

    # 3) 產生次日委託（ledger 無當日列＝Level 1 缺席，不產生訊號）
    pct5 = _ledger_pct(s, d, 5)
    pct1 = _ledger_pct(s, d, 1)
    had_signal = not pct5.empty
    n_new = 0
    if had_signal:
        new_orders = plan_defense(state, pct1, P5_PARAMS)
        if acct.rebalance_counter % P5_PARAMS.rebalance_every == 0:
            defended = {o.stock_id for o in new_orders}
            planning = state.copy()
            for sid in defended:
                planning.positions.pop(sid, None)
            ref_px = {sid: close_px[sid] for sid in pct5.index
                      if sid in close_px}
            new_orders += plan_rebalance(planning, nav, ref_px, pct5,
                                         P5_PARAMS)
        still_open = {(r.stock_id, r.side) for r in rows
                      if r.status == "deferred"}
        for o in new_orders:
            if (o.stock_id, o.side) in still_open:
                continue   # 順延單優先（simulate._merge_orders 同語意）
            s.add(models.Level2Order(account_id=acct.id, created_date=d,
                                     stock_id=o.stock_id, side=o.side,
                                     qty=o.qty, reason=o.reason))
            n_new += 1
        acct.rebalance_counter += 1

    s.add(models.Level2Nav(account_id=acct.id, date=d, nav=nav,
                           cash=state.cash, invested=nav - state.cash,
                           benchmark_close=bench, had_signal=had_signal))
    acct.cash = state.cash
    if acct.start_date is None:
        acct.start_date = d
    acct.last_processed = d
    return {"date": str(d), "nav": round(nav, 0), "filled": n_filled,
            "new_orders": n_new, "had_signal": had_signal}


def run_catchup(s: Session) -> list[dict]:
    """處理帳戶尚未入帳的所有交易日（首次執行＝只跑最新一日，不回補歷史）。"""
    acct = get_or_create_account(s)
    days = trading_days_after(s, acct.last_processed)
    if acct.last_processed is None:
        days = days[-1:]   # 帳戶生日：從最新交易日起跑（假戰績條款）
    out = []
    prev = acct.last_processed
    for d in days:
        out.append(process_day(s, acct, d, prev))
        prev = d
    return out


def verify_replay(s: Session) -> bool:
    """orders 重放 == 帳戶快照（現金與持倉逐檔一致）。"""
    acct = s.scalar(select(models.Level2Account)
                    .where(models.Level2Account.name == ACCOUNT_NAME))
    if acct is None:
        return True
    rows = s.execute(select(models.Level2Order)
                     .where(models.Level2Order.account_id == acct.id,
                            models.Level2Order.status == "filled")
                     .order_by(models.Level2Order.trade_date,
                               models.Level2Order.id)).scalars()
    fills = [eg.Fill(r.stock_id, r.side, r.qty, r.price, r.fee, r.tax or 0.0,
                     eg.FILLED, r.reason) for r in rows]
    st = eg.replay(acct.initial_cash, fills)
    qty0, _ = _last_positions(s, acct.id)
    ok = abs(st.cash - acct.cash) < 1e-6 and st.positions == qty0
    if not ok:
        _log(f"重放不一致：replay cash={st.cash:.2f} vs acct={acct.cash:.2f}；"
             f"positions diff={set(st.positions.items()) ^ set(qty0.items())}")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    from app.storage.database import SessionLocal, init_db
    init_db()
    with SessionLocal() as s:
        if args.verify:
            ok = verify_replay(s)
            _log(f"重放一致性：{'OK' if ok else 'FAILED'}")
            raise SystemExit(0 if ok else 1)
        results = run_catchup(s)
        s.commit()
        for r in results:
            _log(str(r))
        if not results:
            _log("無新交易日可處理")
        if not verify_replay(s):
            raise SystemExit("重放一致性失敗——停機檢查")


if __name__ == "__main__":
    main()
