"""日迴圈模擬器（FRS §7）——回測與 live paper 共用的唯一執行語意。

時間語意（凍結）：
- T 日收盤後產生訊號（再平衡/防禦）→ T+1 開盤價成交。
- deferred 委託帶到下一個交易日重試；與新訊號同標的同方向時去重（保留先到）。
- NAV 以 T 日收盤估值；停牌股用前收盤 ffill 估值（成交判斷仍用原始價格——
  無開盤價即 deferred，估值與成交是兩件事）。

輸入一律為 date×stock 的 DataFrame（open/close/pct/pct1），無 DB 依賴。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .costs import CostModel
from .engine import (Fill, Order, PortfolioState, deferred_to_orders,
                     execute_day, nav)
from .policy import BaselineParams, plan_defense, plan_rebalance


@dataclass
class SimResult:
    nav: pd.Series                      # 每日收盤 NAV
    fills: pd.DataFrame                 # date + Fill 欄位（含 rejected/deferred）
    final_state: PortfolioState
    positions: dict = field(default_factory=dict)   # date -> {stock: qty}


def _row(df: pd.DataFrame, d) -> dict:
    return df.loc[d].dropna().to_dict()


def _merge_orders(pending: list[Order], new: list[Order]) -> list[Order]:
    """同標的同方向去重（保留先到者——deferred 優先於新訊號）。"""
    seen = set()
    out = []
    for o in [*pending, *new]:
        key = (o.stock_id, o.side)
        if key in seen:
            continue
        seen.add(key)
        out.append(o)
    return out


def run_simulation(open_df: pd.DataFrame, close_df: pd.DataFrame,
                   pct_df: pd.DataFrame, pct1_df: pd.DataFrame | None,
                   params: BaselineParams, initial_cash: float,
                   cost: CostModel | None = None) -> SimResult:
    """跑完整段模擬。pct_df＝再平衡用排名（P5 傳 5D、P1 傳 1D）；
    pct1_df＝防禦用 1D 排名（use_defense=False 可傳 None）。

    再平衡日曆：自第一個「pct_df 有訊號」的日子起算，每 rebalance_every 個
    訊號日再平衡一次。
    """
    cost = cost or CostModel()
    dates = list(close_df.index)
    close_ff = close_df.ffill()

    state = PortfolioState(initial_cash)
    pending: list[Order] = []
    navs: list[float] = []
    fill_rows: list[dict] = []
    positions: dict = {}
    signal_day = -1   # 訊號日計數（只數 pct 有值的日子）

    for i, d in enumerate(dates):
        # 1) 開盤執行昨日委託
        if pending and i > 0:
            prev_d = dates[i - 1]
            state, fills = execute_day(state, pending, _row(open_df, d),
                                       _row(close_df, prev_d), cost)
            fill_rows += [{"date": d, **f.__dict__} for f in fills]
            pending = deferred_to_orders(fills)

        # 2) 收盤估值
        val_px = close_ff.loc[d]
        nav_d = nav(state, val_px[val_px.notna()].to_dict())
        navs.append(nav_d)
        positions[d] = dict(state.positions)

        # 3) 收盤後產生訊號（最後一日不再產生——無次日可成交）
        if i == len(dates) - 1:
            continue
        sig = pct_df.loc[d].dropna()
        if sig.empty:
            continue
        signal_day += 1
        new_orders: list[Order] = []
        if pct1_df is not None and params.use_defense:
            new_orders += plan_defense(state, pct1_df.loc[d].dropna(), params)
        if signal_day % params.rebalance_every == 0:
            # 防禦單優先（同標的去重時先到先贏），再平衡以防禦後的視角規劃
            defended = {o.stock_id for o in new_orders}
            planning = state.copy()
            for s in defended:
                planning.positions.pop(s, None)
            new_orders += plan_rebalance(planning, nav_d, val_px.to_dict(),
                                         sig, params)
        pending = _merge_orders(pending, new_orders)

    fills_df = pd.DataFrame(fill_rows) if fill_rows else pd.DataFrame(
        columns=["date", "stock_id", "side", "qty", "price", "fee", "tax",
                 "status", "reason"])
    return SimResult(nav=pd.Series(navs, index=pd.Index(dates, name="date")),
                     fills=fills_df, final_state=state, positions=positions)
