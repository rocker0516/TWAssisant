"""Baseline v1 規則策略（FRS §5 修訂版，參數凍結 2026-09-18）。

P5 主組合：每 20 個交易日再平衡（5D pct_rank）；買進門檻 rank ≤ K_IN=20、
續抱門檻 rank ≤ K_HOLD=200（寬緩衝降換手）；等權目標 5%／檔；再平衡不對
存活持股加減碼（只處理進出，允許權重漂移——換手優先）。
防禦出場：任一日持股 1D pct_rank ≤ 0.2 → 次日出場，不等再平衡日——
低頻節奏下唯一的盤中風控，也是「避弱型 edge」的直接兌現。

版本史：
- v0（reb5/hold60）於 dev 被成本否決：年換手 26.8×、成本拖累 37pp、
  防禦 3 年觸發 604 次成雜訊源。dev 調參證據（頻率與防禦兩槓桿各自單調）
  見 data/level2_backtest_dev.json（2026-09-18 sweep）。
- v1（reb20/hold200/def0.2）：dev 超額 −5.2%（t=−0.29）、換手 7.9×、
  MDD −15.2% vs 大盤 −31.6%；vs universe 等權淨 +16.5pp。

P1 對照組合：每日再平衡 1D Top-20 等權。dev 已判死刑（1D alpha 在隔夜
跳空、次日開盤買拿不到＋成本 102pp），保留只為 holdout 完整入檔。

持股缺席當日排名（跌出 tradable universe：ADV 不足或處置）→ 視為 rank=∞，
於下一個再平衡日出場；防禦規則對缺席者不觸發（無 1D 排名可判）。

任何參數改動＝policy_version 變更＋戰績歸零（FRS §5）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

import pandas as pd

from .engine import BUY, SELL, Order, PortfolioState

POLICY_VERSION = "baseline_v1"


@dataclass(frozen=True)
class BaselineParams:
    target_n: int = 20
    k_in: int = 20
    k_hold: int = 200
    rebalance_every: int = 20
    defense_pct: float = 0.2      # 1D pct_rank ≤ 此值 → 防禦出場
    use_defense: bool = True


P5_PARAMS = BaselineParams()
P1_PARAMS = BaselineParams(k_hold=20, rebalance_every=1, use_defense=False)


def _ranks(pct: pd.Series) -> pd.Series:
    """pct_rank（1=最強）→ 名次（1=最強）。同分以股號序穩定切割。"""
    return pct.sort_index().rank(ascending=False, method="first").astype(int)


def select_targets(held: set[str], pct: pd.Series,
                   params: BaselineParams) -> set[str]:
    """再平衡日的目標持股集合：續抱（rank ≤ k_hold）＋新進（rank ≤ k_in 依序補滿）。"""
    rank = _ranks(pct.dropna())
    keep = {s for s in held if s in rank.index and rank[s] <= params.k_hold}
    slots = params.target_n - len(keep)
    if slots <= 0:
        return keep
    candidates = rank[rank <= params.k_in].sort_values().index
    adds = [s for s in candidates if s not in keep][:slots]
    return keep | set(adds)


def plan_rebalance(state: PortfolioState, nav_value: float,
                   ref_px: Mapping[str, float], pct: pd.Series,
                   params: BaselineParams) -> list[Order]:
    """產生再平衡委託：先賣出局者，再依 rank 順序買新進者（等權 nav/target_n）。

    ref_px＝訊號日收盤價（僅供試算股數；實際以次日開盤成交）。
    買單依 rank 排序送出——現金不足時引擎會先滿足強者。
    """
    held = {s for s, q in state.positions.items() if q > 0}
    targets = select_targets(held, pct, params)
    orders = [Order(s, SELL, state.positions[s], "rebalance")
              for s in sorted(held - targets)]
    target_value = nav_value / params.target_n
    rank = _ranks(pct.dropna())
    for s in sorted(targets - held, key=lambda x: rank.get(x, 10 ** 9)):
        px = ref_px.get(s)
        if px is None or px <= 0 or not math.isfinite(px):
            continue
        qty = int(target_value / px)
        if qty > 0:
            orders.append(Order(s, BUY, qty, "rebalance"))
    return orders


def plan_defense(state: PortfolioState, pct1: pd.Series,
                 params: BaselineParams) -> list[Order]:
    """防禦出場：持股 1D pct_rank ≤ defense_pct → 全數賣出。"""
    if not params.use_defense:
        return []
    out = []
    for s, q in sorted(state.positions.items()):
        if q > 0 and s in pct1.index and pd.notna(pct1[s]) \
                and pct1[s] <= params.defense_pct:
            out.append(Order(s, SELL, q, "defense"))
    return out
