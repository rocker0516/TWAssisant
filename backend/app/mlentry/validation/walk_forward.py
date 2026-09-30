"""§19 Purged Walk-forward：rolling 訓練窗（config）、固定長度 validation fold、Final Holdout 程式層隔離。

Split 只定義「哪些 signal_date 屬於哪個 fold 的 train / test」，不碰特徵或模型。
dev 與 holdout 是同一個 Split 物件的兩個欄位，holdout 的 test 日期絕不出現在任何 dev fold。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from ..config import ValidationConfig
from .purge import purged_train_positions


@dataclass(frozen=True)
class Fold:
    name: str
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    n_train_days: int
    n_test_days: int


@dataclass(frozen=True)
class Split:
    split_version: str
    as_of: str
    max_horizon: int
    dev_folds: tuple[Fold, ...]
    holdout: Fold | None
    dev_start: str
    dev_end: str
    holdout_start: str | None
    holdout_end: str | None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    def is_holdout(self, signal_date: pd.Series) -> pd.Series:
        if self.holdout is None:
            return pd.Series(False, index=signal_date.index)
        sd = signal_date.astype(str)
        return (sd >= self.holdout.test_start) & (sd <= self.holdout.test_end)


def matured_dates(dates: pd.Index, max_horizon: int) -> pd.Index:
    """as_of 當下已成熟的 signal_date（pos + max_horizon <= 最後位置）。"""
    n = len(dates)
    return dates[: max(0, n - max_horizon)]


def build_split(dates: pd.Index, cfg: ValidationConfig, max_horizon: int,
                as_of: str | None = None) -> Split:
    """dates：資料集內「有合格樣本」的 signal_date（升冪、唯一）。"""
    dates = pd.Index(dates).astype(str)
    as_of = as_of or dates[-1]
    mat = matured_dates(dates, max_horizon)
    if len(mat) == 0:
        raise ValueError("no matured signal dates")
    h = min(cfg.holdout_days, len(mat))
    dev = mat[: len(mat) - h] if h < len(mat) else mat[:0]
    hold = mat[len(mat) - h:]
    folds: list[Fold] = []
    if len(dev):
        # 第一個 fold 的 test 起點：purge 後需有 min_train_days（或完整 train_window）可用訓練日
        need = cfg.min_train_days
        if cfg.require_full_window and cfg.train_window_days:
            need = max(need, cfg.train_window_days)
        first_test = need + max_horizon + 1
        for i, s in enumerate(range(first_test, len(dev), cfg.fold_days)):
            e = min(s + cfg.fold_days, len(dev))
            tr = purged_train_positions(s, max_horizon, cfg.train_window_days)
            if len(tr) < cfg.min_train_days:
                continue
            folds.append(Fold(f"dev_{i:02d}", dev[tr[0]], dev[tr[-1]], dev[s], dev[e - 1],
                              len(tr), e - s))
    holdout = None
    if len(hold) and len(dev):
        s = len(dev)                      # holdout test 起點在 mat 上的位置
        tr = purged_train_positions(s, max_horizon, cfg.train_window_days)
        holdout = Fold("holdout", mat[tr[0]], mat[tr[-1]], hold[0], hold[-1], len(tr), len(hold))
    payload = {"cfg": cfg.version, "as_of": as_of, "max_horizon": max_horizon,
               "dev": [d for d in (dev[0], dev[-1])] if len(dev) else [],
               "hold": [hold[0], hold[-1]] if len(hold) else []}
    ver = "s_" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:8]
    return Split(ver, as_of, max_horizon, tuple(folds), holdout,
                 dev[0] if len(dev) else None, dev[-1] if len(dev) else None,
                 hold[0] if len(hold) else None, hold[-1] if len(hold) else None)


def fold_masks(fold: Fold, signal_date: pd.Series) -> tuple[pd.Series, pd.Series]:
    sd = signal_date.astype(str)
    return ((sd >= fold.train_start) & (sd <= fold.train_end),
            (sd >= fold.test_start) & (sd <= fold.test_end))


def check_split_integrity(split: Split, dates: pd.Index) -> None:
    """dev fold 的 test 與 holdout 互斥；每個 fold train 的最後 label 成熟日 < test 起點。"""
    dates = pd.Index(dates).astype(str)
    pos = {d: i for i, d in enumerate(dates)}
    for f in list(split.dev_folds) + ([split.holdout] if split.holdout else []):
        if pos[f.train_end] + split.max_horizon >= pos[f.test_start]:
            raise AssertionError(f"{f.name}: purge violated")
        if split.holdout and f.name != "holdout" and f.test_end >= split.holdout.test_start:
            raise AssertionError(f"{f.name}: dev fold overlaps holdout")
    if split.holdout:
        for f in split.dev_folds:
            if f.train_end >= split.holdout.test_start:
                raise AssertionError(f"{f.name}: dev training touches holdout")
