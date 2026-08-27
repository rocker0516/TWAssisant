"""格子評估器：ctrl（控 (date, atr_bucket) 桶命中增量）／t／fold／holdout／routing_delta。

方法論同 backend/scripts/mega_mine2.py 的 class Evaluator（L252-311），差別是
本版本吃一個「scope」（格子的母體遮罩：某類股群 x 某情境），mask 相對 scope
內同日同 ATR 桶的期望計算 ctrl，而非相對整個挖掘窗。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

_MIN_DAYS = 60
_MIN_PICKS = 2


def _daily_t(a: np.ndarray) -> float:
    """日層級序列單樣本 t（同 mega_mine2.py:294-299 的 _t）。"""
    a = a[~np.isnan(a)]
    if len(a) < 3:
        return float("nan")
    sd = a.std(ddof=1)
    return float(a.mean() / (sd / np.sqrt(len(a)))) if sd > 0 else float("nan")


class CellEvaluator:
    """向量化日層級評估：命中、ATR 桶控波動增量、t 值、fold、holdout。"""

    def __init__(self, df: pd.DataFrame, target_col: str = "exc_hit20", atr_col: str = "atr_bucket"):
        self.df = df
        self.target_col = target_col
        self.atr_col = atr_col
        self.hit = df[target_col].to_numpy(dtype=float)
        self.dates = df["date"].to_numpy()
        self.atr = df[atr_col].to_numpy()
        self.fold = df["fold"].to_numpy() if "fold" in df.columns else None
        self.is_holdout = (
            df["is_holdout"].to_numpy(dtype=bool)
            if "is_holdout" in df.columns
            else np.zeros(len(df), dtype=bool)
        )

    def _run_within(self, mask: np.ndarray, scope: np.ndarray) -> dict | None:
        """在 `scope` 母體內、對 `mask & scope` 命中列做逐日 ctrl/lift 計算。

        回傳 dict（不含 fold_ctrl / holdout 欄位），若有效日 < _MIN_DAYS 回 None。
        """
        scope = scope.astype(bool)
        eligible = mask.astype(bool) & scope
        if not eligible.any():
            return None

        scope_idx = np.flatnonzero(scope)
        date_codes, date_vals = pd.factorize(self.dates[scope_idx], sort=True)
        n_dates = len(date_vals)
        hit_s = self.hit[scope_idx]

        # scope 內以 (date, atr_bucket) 分組的期望命中率（bexp），逐列對齊回去
        bkt = pd.DataFrame({"dc": date_codes, "atr": self.atr[scope_idx], "hit": hit_s})
        bexp = bkt.groupby(["dc", "atr"])["hit"].transform("mean").to_numpy(dtype=float)

        # scope 內每日基率（全 scope，不限 mask）
        day_base_all = np.bincount(date_codes, weights=hit_s, minlength=n_dates) / np.bincount(
            date_codes, minlength=n_dates
        )

        # 選中（mask & scope）列，映射回 scope 內的相對索引
        sel_in_scope = eligible[scope_idx]
        idx = np.flatnonzero(sel_in_scope)
        if len(idx) == 0:
            return None
        dc = date_codes[idx]
        cnt = np.bincount(dc, minlength=n_dates)
        ok_days = cnt >= _MIN_PICKS
        n_days = int(ok_days.sum())
        if n_days < _MIN_DAYS:
            return None

        keep = ok_days[dc]
        idx, dc = idx[keep], dc[keep]
        cnt = np.bincount(dc, minlength=n_dates).astype(float)
        with np.errstate(invalid="ignore", divide="ignore"):
            d_hit = np.bincount(dc, weights=hit_s[idx], minlength=n_dates) / cnt
            d_ctrl = np.bincount(dc, weights=(hit_s[idx] - bexp[idx]), minlength=n_dates) / cnt

        sel = ok_days
        day_ctrl_ok = d_ctrl[sel]
        day_lift_ok = d_hit[sel] - day_base_all[sel]

        ctrl = float(np.nanmean(day_ctrl_ok)) * 100
        t_ctrl = _daily_t(day_ctrl_ok)
        lift = float(np.nanmean(day_lift_ok)) * 100

        n = len(idx)
        if n == 0:
            return None
        hit_pct = float(hit_s[idx].mean()) * 100
        base_pct = float(day_base_all[sel].mean()) * 100

        return {
            "hit": round(hit_pct, 2),
            "base": round(base_pct, 2),
            "lift": round(lift, 2),
            "ctrl": round(ctrl, 2),
            "t_ctrl": round(t_ctrl, 2) if not np.isnan(t_ctrl) else float("nan"),
            "n_picks": n,
            "n_days": n_days,
        }

    def run(self, mask: np.ndarray, scope: np.ndarray) -> dict | None:
        mask = np.asarray(mask, dtype=bool)
        scope = np.asarray(scope, dtype=bool)
        in_sample = ~self.is_holdout

        base = self._run_within(mask, scope & in_sample)
        if base is None:
            return None

        fold_ctrl = []
        if self.fold is not None:
            for fi in range(3):
                fold_mask = (self.fold == fi) & in_sample
                fr = self._run_within(mask & fold_mask, scope & fold_mask)
                fold_ctrl.append(fr["ctrl"] if fr is not None else None)

        holdout_scope = scope & self.is_holdout
        hr = self._run_within(mask, holdout_scope)

        base["fold_ctrl"] = fold_ctrl
        base["holdout_ctrl"] = hr["ctrl"] if hr is not None else None
        base["holdout_hit"] = hr["hit"] if hr is not None else None
        return base

    @staticmethod
    def bonferroni_gate(n_cells: int) -> float:
        return float(norm.isf(0.025 / n_cells))


def routing_delta(cell: dict, allmarket: dict) -> float:
    return cell["ctrl"] - allmarket["ctrl"]
