"""Barrier / first-hit 引擎（FRS §4、§5、§20.3；附錄 A）。

- benchmark_entry = Open[t+1]，entry_status ∈ {FILLED, PRICE_LIMIT_CONSTRAINT, NO_MARKET_DATA}。
- 路徑日 k = 1..K，k=1 為進場日本身（開盤進場後當日高低點算數）。
- target_hit_k: High_k >= entry×(1+tp)；stop_hit_k: Low_k <= entry×(1−sp)。
- event：TARGET / STOP / STOP_AMBIGUOUS（同日雙觸）/ TIMEOUT / NOT_ENTERED / PENDING（未成熟）。
- 中途缺列的 k 跳過；進場後資料提前結束（下市）→ path_truncated，以最後有收盤的 k 結算，
  之後的 return_k 沿用該收盤（部位視為被迫結清）。
- 所有 t+k 皆為矩陣列位置（= calendar 位置），不做曆日加減。
"""

from __future__ import annotations

from enum import IntEnum

import numpy as np
import pandas as pd

from ..config import LabelConfig
from ..data.quality import limits_from_prev_close


_REL_TOL = 1e-9


class EntryStatus(IntEnum):
    PENDING = -1                 # t+1 尚未到（calendar 最後一列）：生產當日的訊號
    FILLED = 0
    PRICE_LIMIT_CONSTRAINT = 1
    NO_MARKET_DATA = 2


class Event(IntEnum):
    PENDING = -1
    NOT_ENTERED = 0
    TARGET = 1
    STOP = 2
    STOP_AMBIGUOUS = 3
    TIMEOUT = 4


def entry_and_status(open_: pd.DataFrame, close: pd.DataFrame, tol: float,
                     ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(benchmark_entry_price, entry_status)，皆對齊 signal_date t。最後一列（無 t+1）為 NaN / NO_MARKET_DATA。"""
    nxt_open = open_.shift(-1)
    up, _ = limits_from_prev_close(close)      # 列 t+1 = up_limit(C_t)
    up_next = up.shift(-1)                      # 移回 t 列
    at_limit = nxt_open >= up_next * (1 - tol)
    status = pd.DataFrame(int(EntryStatus.FILLED), index=close.index, columns=close.columns, dtype="int8")
    status = status.mask(at_limit.fillna(False), int(EntryStatus.PRICE_LIMIT_CONSTRAINT))
    status = status.mask(nxt_open.isna(), int(EntryStatus.NO_MARKET_DATA))
    status.iloc[-1] = int(EntryStatus.PENDING)
    status = status.astype("int8")
    entry = nxt_open.where(status == int(EntryStatus.FILLED))
    return entry, status


def run_barriers(m: dict[str, pd.DataFrame], cfg: LabelConfig) -> dict[str, pd.DataFrame]:
    """→ 以 signal_date 為列的 outcome 矩陣字典（float32 / int8）。"""
    open_, high, low, close = m["open"], m["high"], m["low"], m["close"]
    n, ncol = close.shape
    K = cfg.max_horizon
    entry, status = entry_and_status(open_, close, cfg.limit_tolerance)
    E = entry.to_numpy(dtype=float)
    H = high.to_numpy(dtype=float); L = low.to_numpy(dtype=float); C = close.to_numpy(dtype=float)
    # 相對容忍：100×1.10 在浮點是 110.00000000000001，High=110 必須算碰到。
    tp = E * (1 + cfg.target_pct) * (1 - _REL_TOL)
    sp = E * (1 - cfg.stop_pct) * (1 + _REL_TOL)

    tday = np.zeros((n, ncol), dtype=np.int8)        # 0 = 未觸
    sday = np.zeros((n, ncol), dtype=np.int8)
    run_hi = np.full((n, ncol), np.nan); run_lo = np.full((n, ncol), np.nan)
    last_close = np.full((n, ncol), np.nan)
    avail = np.zeros((n, ncol), dtype=np.int8)
    mfe = {}; mae = {}; ret = {}
    mae_before_target = np.full((n, ncol), np.nan)

    def shift_back(A, k):
        out = np.full_like(A, np.nan)
        if k < n:
            out[: n - k] = A[k:]
        return out

    present_at_K = np.zeros((n, ncol), dtype=bool)
    for k in range(1, K + 1):
        hk, lk, ck = shift_back(H, k), shift_back(L, k), shift_back(C, k)
        present = np.isfinite(ck)
        if k == K:
            present_at_K = present
        avail += present.astype(np.int8)
        run_hi = np.fmax(run_hi, hk); run_lo = np.fmin(run_lo, lk)
        last_close = np.where(present, ck, last_close)
        hit_t = present & (hk >= tp) & (tday == 0)
        hit_s = present & (lk <= sp) & (sday == 0)
        tday[hit_t] = k; sday[hit_s] = k
        # mae_before_target：target 首觸當日（含）之前的最低點
        mae_before_target = np.where(hit_t, run_lo / E - 1, mae_before_target)
        if k in cfg.horizons:
            mfe[k] = (run_hi / E - 1).astype(np.float32)
            mae[k] = (run_lo / E - 1).astype(np.float32)
        if k in cfg.return_horizons:
            ret[k] = (last_close / E - 1).astype(np.float32)

    matured = (np.arange(n) + K <= n - 1)[:, None] & np.ones((1, ncol), dtype=bool)
    entered = np.isfinite(E)
    ev = np.full((n, ncol), int(Event.TIMEOUT), dtype=np.int8)
    t_inf = np.where(tday == 0, 127, tday); s_inf = np.where(sday == 0, 127, sday)
    ev[t_inf < s_inf] = int(Event.TARGET)
    ev[s_inf < t_inf] = int(Event.STOP)
    ev[(t_inf == s_inf) & (tday != 0)] = int(Event.STOP_AMBIGUOUS)
    ev[~matured & entered] = int(Event.PENDING)
    ev[~entered] = int(Event.NOT_ENTERED)
    ev[status.to_numpy() == int(EntryStatus.PENDING)] = int(Event.PENDING)

    truncated = entered & matured & ~present_at_K          # 路徑末端無列（下市／長期停牌）
    nan_out = ~entered | ~matured
    mfe_bt = np.where(nan_out | (ev != int(Event.TARGET)), np.nan, mae_before_target)

    def mat(a, dtype="float32"):
        return pd.DataFrame(a, index=close.index, columns=close.columns).astype(dtype)

    def masked(a):
        return mat(np.where(nan_out, np.nan, a))

    out = {
        "benchmark_entry_price": mat(E),
        "entry_status": status,
        "entry_executable": mat(entered.astype(np.int8), "int8"),
        "event_type": mat(ev, "int8"),
        "target_first_hit_day": masked(np.where(tday == 0, np.nan, tday)),
        "stop_first_hit_day": masked(np.where(sday == 0, np.nan, sday)),
        "mae_before_target": mat(mfe_bt),
        "path_days_available": mat(np.where(entered, avail, 0), "int8"),
        "path_truncated": mat(truncated.astype(np.int8), "int8"),
        "matured": mat(matured.astype(np.int8), "int8"),
    }
    for k in cfg.horizons:
        out[f"mfe_{k}d"] = masked(mfe[k]); out[f"mae_{k}d"] = masked(mae[k])
    for k in cfg.return_horizons:
        out[f"return_{k}d"] = masked(ret[k])
    return out
