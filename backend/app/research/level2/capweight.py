"""市值加權頂分位組合 captop_v0（Level 2.1 設計 §1）。

target = { pct5 ≥ THRESHOLD } 依市值取前 TOP_N，市值加權；每 REBALANCE_EVERY
日再平衡到目標權重（含既有持股加減碼）。|Δ部位| < MIN_TRADE 不下單——
100 萬帳戶 20 元低消下微調單成本失控，此為結構參數。無防禦規則
（Step 0 已證弱分位對市值加權組合無肉）。

執行語意（MOO/漲跌停/低消）全數沿用 engine；本模組只產委託。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

import pandas as pd

from .engine import BUY, SELL, Order, PortfolioState

CAPTOP_VERSION = "captop_v0"


@dataclass(frozen=True)
class CapTopParams:
    threshold: float = 0.8
    top_n: int = 30
    rebalance_every: int = 20
    min_trade: float = 10_000.0
    # simulate 介面相容欄位（本策略不用防禦）
    use_defense: bool = False
    defense_pct: float = 0.0


def plan_captop(state: PortfolioState, nav_value: float,
                ref_px: Mapping[str, float], pct: pd.Series,
                caps: pd.Series, params: CapTopParams) -> list[Order]:
    """產生再平衡委託：出局者全賣、目標者加減碼到市值權重（微量單跳過）。

    caps＝訊號日市值（僅 U_t 內有值）；pct＝當日 5D pct_rank。
    買單依目標權重由大到小送出——現金不足時引擎先滿足大權重。
    """
    elig = pct[pct >= params.threshold].dropna().index
    c = caps.reindex(elig).dropna()
    target = c.sort_values(ascending=False).index[: params.top_n]
    w = c[target] / c[target].sum() if len(target) else pd.Series(dtype=float)

    held = {s: q for s, q in state.positions.items() if q > 0}
    orders = [Order(s, SELL, held[s], "rebalance")
              for s in sorted(set(held) - set(target))]

    deltas: list[tuple[str, int, float]] = []
    for s in target:
        px = ref_px.get(s)
        if px is None or px <= 0 or not math.isfinite(px):
            continue
        delta = int(nav_value * w[s] / px) - held.get(s, 0)
        if abs(delta) * px < params.min_trade:
            continue
        deltas.append((s, delta, float(w[s])))
    orders += [Order(s, SELL, -d, "rebalance")
               for s, d, _ in sorted(deltas) if d < 0]
    orders += [Order(s, BUY, d, "rebalance")
               for s, d, _ in sorted(deltas, key=lambda x: -x[2]) if d > 0]
    return orders
