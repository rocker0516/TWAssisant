"""委託執行引擎——純函式核心（FRS §4/§7/§9）。

一天的執行 = execute_day(state, orders, open/prev_close, cost) → (新 state, fills)。
規則（FRS §4，凍結）：
- 次一交易日開盤價成交（MOO）。
- 買單開盤即漲停 → rejected（不追）；賣單開盤跌停 → deferred（順延重試）。
- 個股無開盤價（暫停交易/無成交）→ deferred。
- 現金不足：買量向下修到付得起（含手續費），修到 0 → rejected。
- 先賣後買（賣出釋放的現金當日可用）。

重放一致性（FRS §7）：任一時點的帳戶狀態必可由 fills 序列 replay() 重建，
live 與回測共用本模組——不得有第二套會計實作。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .costs import CostModel, down_limit, up_limit

BUY = "buy"
SELL = "sell"

FILLED = "filled"
REJECTED = "rejected"
DEFERRED = "deferred"


@dataclass(frozen=True)
class Order:
    stock_id: str
    side: str            # BUY / SELL
    qty: int             # 股數（支援零股）
    reason: str          # rebalance / defense / entry ...


@dataclass(frozen=True)
class Fill:
    stock_id: str
    side: str
    qty: int
    price: float
    fee: float
    tax: float
    status: str          # FILLED / REJECTED / DEFERRED
    reason: str          # 原委託 reason；被拒/順延時附加 ':limit_up' 等


@dataclass
class PortfolioState:
    cash: float
    positions: dict[str, int] = field(default_factory=dict)

    def copy(self) -> "PortfolioState":
        return PortfolioState(self.cash, dict(self.positions))


def nav(state: PortfolioState, close_px: Mapping[str, float]) -> float:
    """收盤淨值。持股當日無收盤價 → 以缺值前最後可得價格為準是資料層的責任，
    這裡直接 KeyError 讓上游知道資料破洞（不靜默塞 0）。"""
    return state.cash + sum(qty * close_px[sid]
                            for sid, qty in state.positions.items() if qty)


def execute_day(state: PortfolioState, orders: list[Order],
                open_px: Mapping[str, float], prev_close: Mapping[str, float],
                cost: CostModel) -> tuple[PortfolioState, list[Fill]]:
    """執行一天的委託。回傳新 state 與全部 fills（含 rejected/deferred）。

    deferred 的委託由呼叫端收集後帶到下一個交易日（simulate 負責）。
    """
    st = state.copy()
    fills: list[Fill] = []
    sells = [o for o in orders if o.side == SELL]
    buys = [o for o in orders if o.side == BUY]

    for o in sells:
        px = open_px.get(o.stock_id)
        pc = prev_close.get(o.stock_id)
        held = st.positions.get(o.stock_id, 0)
        if held <= 0:
            fills.append(Fill(o.stock_id, SELL, 0, 0.0, 0.0, 0.0,
                              REJECTED, f"{o.reason}:not_held"))
            continue
        qty = min(o.qty, held)
        if px is None or pc is None:
            fills.append(Fill(o.stock_id, SELL, qty, 0.0, 0.0, 0.0,
                              DEFERRED, f"{o.reason}:no_trade"))
            continue
        if px <= down_limit(pc):
            fills.append(Fill(o.stock_id, SELL, qty, 0.0, 0.0, 0.0,
                              DEFERRED, f"{o.reason}:limit_down"))
            continue
        fee = cost.sell_fee(px, qty)
        tax = cost.sell_tax(px, qty)
        st.cash += px * qty - fee - tax
        st.positions[o.stock_id] = held - qty
        if st.positions[o.stock_id] == 0:
            del st.positions[o.stock_id]
        fills.append(Fill(o.stock_id, SELL, qty, px, fee, tax, FILLED, o.reason))

    for o in buys:
        px = open_px.get(o.stock_id)
        pc = prev_close.get(o.stock_id)
        if px is None or pc is None:
            fills.append(Fill(o.stock_id, BUY, o.qty, 0.0, 0.0, 0.0,
                              DEFERRED, f"{o.reason}:no_trade"))
            continue
        if px >= up_limit(pc):
            fills.append(Fill(o.stock_id, BUY, o.qty, 0.0, 0.0, 0.0,
                              REJECTED, f"{o.reason}:limit_up"))
            continue
        qty = _affordable_qty(st.cash, px, o.qty, cost)
        if qty <= 0:
            fills.append(Fill(o.stock_id, BUY, o.qty, 0.0, 0.0, 0.0,
                              REJECTED, f"{o.reason}:insufficient_cash"))
            continue
        fee = cost.buy_fee(px, qty)
        st.cash -= px * qty + fee
        st.positions[o.stock_id] = st.positions.get(o.stock_id, 0) + qty
        fills.append(Fill(o.stock_id, BUY, qty, px, fee, 0.0, FILLED, o.reason))

    return st, fills


def _affordable_qty(cash: float, price: float, qty: int, cost: CostModel) -> int:
    """買量向下修到「股款＋手續費」付得起。手續費對 qty 單調 → 直接解再驗證。"""
    if qty <= 0 or price <= 0:
        return 0
    q = min(qty, int(cash / (price * (1 + cost.fee_rate))))
    while q > 0 and price * q + cost.buy_fee(price, q) > cash:
        q -= 1
    return q


def deferred_to_orders(fills: list[Fill]) -> list[Order]:
    """把 deferred fills 轉回次日委託（reason 去掉附加註記，保留原因鏈首段）。"""
    out = []
    for f in fills:
        if f.status == DEFERRED:
            out.append(Order(f.stock_id, f.side, f.qty, f.reason.split(":")[0]))
    return out


def replay(initial_cash: float, fills: list[Fill]) -> PortfolioState:
    """由 fills 序列重建帳戶狀態（重放一致性測試的基準實作）。"""
    st = PortfolioState(initial_cash)
    for f in fills:
        if f.status != FILLED:
            continue
        if f.side == BUY:
            st.cash -= f.price * f.qty + f.fee
            st.positions[f.stock_id] = st.positions.get(f.stock_id, 0) + f.qty
        else:
            st.cash += f.price * f.qty - f.fee - f.tax
            st.positions[f.stock_id] = st.positions.get(f.stock_id, 0) - f.qty
            if st.positions[f.stock_id] == 0:
                del st.positions[f.stock_id]
    return st
