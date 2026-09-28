"""Level 1 v3 Target（設計 2026-09-28 §1）：隔日開盤進場的絕對淨報酬。

Entry(i,t)   = O(i,t+1)
fill(i,t)    = FILLED / LIMIT_UP_UNFILLED（O(t+1) ≥ up_limit(C(t))）/ NO_TRADE（O(t+1) 缺）
R_net(i,t,N) = C(i,t+N) / Entry(i,t) − 1 − COST_RT

- 未成交列標籤 NaN：不進訓練、不進評估分母；ledger 另記 fill_status。
- 跌停開盤視為成交（買得到，且是模型該學會避開的）。
- 已明講的副作用：「昨日鎖漲停、今日又跳空漲停」被排除 → 模型系統性看衰漲停股。
  這是設計出來的可執行性偏誤，不是模型發現。
- fill 與 horizon 無關（只看 t+1 開盤），所以獨立回傳一份。
- close/open 未還原權息（延續 v2 已知限制）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.research.level2.costs import up_limit

COST_RT = 0.00585            # 0.585%/RT，Level 2 富果化成本模型
HORIZONS_V3 = (1, 5, 10)
FILLED, LIMIT_UP_UNFILLED, NO_TRADE = 0, 1, 2

_up_limit_vec = np.vectorize(up_limit, otypes=[float])


def limit_up_from_prev(prev_close: pd.DataFrame) -> pd.DataFrame:
    """以前一日收盤算漲停價矩陣（tick 貼齊由 costs.up_limit 單一來源負責）。"""
    vals = prev_close.to_numpy(dtype=float)
    out = np.full_like(vals, np.nan)
    ok = np.isfinite(vals)
    out[ok] = _up_limit_vec(vals[ok])
    return pd.DataFrame(out, index=prev_close.index, columns=prev_close.columns)


def entry_and_fill(open_: pd.DataFrame, close: pd.DataFrame,
                   ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(Entry, fill)。兩者皆對齊決策日 t；Entry 值為 O(t+1)，未成交為 NaN。"""
    nxt_open = open_.shift(-1)
    lim_next = limit_up_from_prev(close)                 # C(t) → t+1 的漲停價
    at_limit = nxt_open >= (lim_next - 1e-9)
    fill = pd.DataFrame(FILLED, index=close.index, columns=close.columns, dtype="int8")
    fill = fill.mask(at_limit, LIMIT_UP_UNFILLED).mask(nxt_open.isna(), NO_TRADE)
    fill = fill.astype("int8")
    entry = nxt_open.where(fill == FILLED)
    return entry, fill


def net_returns(entry: pd.DataFrame, close: pd.DataFrame, horizon: int,
                cost: float = COST_RT) -> pd.DataFrame:
    """C(t+N) / Entry − 1 − cost。Entry NaN（未成交）自然傳播為 NaN。"""
    return close.shift(-horizon) / entry - 1 - cost


def build_targets_v3(open_: pd.DataFrame, close: pd.DataFrame, in_universe: pd.DataFrame,
                     horizons: tuple[int, ...] = HORIZONS_V3, cost: float = COST_RT,
                     ) -> tuple[dict[int, dict[str, pd.DataFrame]], pd.DataFrame, pd.DataFrame]:
    """→ ({N: {"net": R_net 矩陣（已套 U_t, float32）}}, entry, fill)。"""
    entry, fill = entry_and_fill(open_, close)
    out: dict[int, dict[str, pd.DataFrame]] = {}
    for n in horizons:
        out[n] = {"net": net_returns(entry, close, n, cost).where(in_universe).astype("float32")}
    return out, entry, fill
