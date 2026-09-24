"""成本模型與台股價格制度（FRS §3/§4）。

- 手續費 0.1425%（買賣各收、不假設折扣）、最低 20 元／筆；證交稅 0.3%（賣出）。
- 漲跌停 ±10%，以 tick 規則貼齊：漲停向下貼、跌停向上貼（TWSE 制度）。
- 滑價假設 0（universe 已限 ADV20 ≥ 5,000 萬），參數保留。

任何參數改動＝CostModel 版本變更（FRS §3 凍結條款）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    fee_rate: float = 0.001425
    min_fee: float = 20.0
    tax_rate: float = 0.003
    slippage: float = 0.0

    def buy_fee(self, price: float, qty: int) -> float:
        return max(self.min_fee, price * qty * self.fee_rate)

    def sell_fee(self, price: float, qty: int) -> float:
        return max(self.min_fee, price * qty * self.fee_rate)

    def sell_tax(self, price: float, qty: int) -> float:
        return price * qty * self.tax_rate


def tick_size(price: float) -> float:
    """台股升降單位（現股）。"""
    if price < 10:
        return 0.01
    if price < 50:
        return 0.05
    if price < 100:
        return 0.1
    if price < 500:
        return 0.5
    if price < 1000:
        return 1.0
    return 5.0


def _round_down_tick(price: float) -> float:
    t = tick_size(price)
    # 先除後乘的浮點誤差用 round 收斂（tick 皆為 0.01 的倍數）
    return round(math.floor(price / t + 1e-9) * t, 2)


def _round_up_tick(price: float) -> float:
    t = tick_size(price)
    return round(math.ceil(price / t - 1e-9) * t, 2)


def up_limit(prev_close: float) -> float:
    """漲停價：prev_close × 1.1 向下貼 tick。tick 以漲停價位所在區間為準。"""
    raw = prev_close * 1.10
    # tick 區間跨界（如 prev=95 → raw=104.5 落在 100+ 區間）：以 raw 的區間取 tick
    return _round_down_tick(raw)


def down_limit(prev_close: float) -> float:
    """跌停價：prev_close × 0.9 向上貼 tick。"""
    raw = prev_close * 0.90
    return _round_up_tick(raw)
