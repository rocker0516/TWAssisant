"""§19.4 Purge：training sample 合法 iff label_available_date < 該 fold 的 test 起點。

label_available_date = signal_date 之後第 max_horizon 個 calendar 日，故以位置表達：
pos(signal) + max_horizon < pos(test_start)。以 label_available_date 為準，不依固定曆日。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def purged_train_positions(test_start_pos: int, max_horizon: int,
                           train_window: int | None, min_pos: int = 0) -> np.ndarray:
    """回傳合法訓練 signal 位置（升冪）。rolling 視窗取最近 train_window 個；None = expanding。"""
    last = test_start_pos - max_horizon - 1
    if last < min_pos:
        return np.array([], dtype=np.int64)
    first = min_pos if train_window is None else max(min_pos, last - train_window + 1)
    return np.arange(first, last + 1, dtype=np.int64)


def assert_no_label_leak(train_avail: pd.Series, test_start: str) -> None:
    """train 的 label_available_date 必須全部 < test_start（None 視為未成熟 → 非法）。"""
    ta = train_avail.astype(object)
    bad = ta.isna() | (ta.astype(str) >= test_start)
    if bad.any():
        raise AssertionError(f"{int(bad.sum())} training samples have labels maturing on/after {test_start}")
