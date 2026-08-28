"""Level 1 Walk-forward OOS 框架（FRS §11）。

- 禁止隨機切分：以交易日順序切測試區塊（預設 126 日 ≈ 半年），expanding 訓練窗。
- Purge/Embargo：label 用到 t+N 的未來收盤，訓練集必須整段結束在
  「測試起點 − N 交易日」之前（embargo = horizon），否則訓練 label 與測試期重疊
  ＝變相偷看。這是本框架唯一硬規則，由 `train_slice` 集中實作並被測試釘死。
- 每個區塊重訓一次（expanding）；rank 轉換是逐日橫斷面操作，train/test 無共用統計量，
  故 preprocessing 不需隨窗重算。
"""

from __future__ import annotations

from typing import Callable, Iterator

import numpy as np
import pandas as pd

from .features import assemble_dataset

DEFAULT_FIRST_TEST = "2022-01-01"
DEFAULT_STEP = 126
# 訓練窗下限——OOS 與 Production 共用同一個常數（同 train_slice 的精神）：
# 少於此列數的訓練集在統計上不可信，OOS 端直接跳過該區塊，Production 端須 fail-fast。
MIN_TRAIN_DAYS = 250


def test_blocks(dates: pd.Index, first_test: str, step: int) -> Iterator[tuple[int, int]]:
    """依交易日序切 [s, e) 測試區塊。"""
    i0 = int(np.searchsorted(dates, first_test))
    for s in range(i0, len(dates), step):
        yield s, min(s + step, len(dates))


def train_slice(dates: pd.Index, test_start: int, embargo: int) -> pd.Index:
    """embargo 硬規則：訓練日 t 需滿足 index(t) + embargo < test_start。"""
    return dates[: max(0, test_start - embargo)]


def train_slice_for_date(dates: pd.Index, pred_date: str, embargo: int) -> pd.Index:
    """預測日的訓練窗——Production 端入口，與 OOS 的 test_blocks 同一 embargo 規則。

    Production 不得以「最近 N 日 label 為 NaN 會被 dropna 掉」當作 embargo：那只在
    pred_date == 最新交易日時成立，跑任何歷史日期都會用到 pred_date 之後的資料
    （確定性 temporal leakage，見設計 §2.1）。
    """
    return train_slice(dates, int(dates.get_loc(pred_date)), embargo)


def walk_forward_scores(
    make_model: Callable[[], object],
    ranked: dict[str, pd.DataFrame],
    target_pct: pd.DataFrame,
    *,
    horizon: int,
    first_test: str = DEFAULT_FIRST_TEST,
    step: int = DEFAULT_STEP,
    min_train_days: int = MIN_TRAIN_DAYS,
) -> pd.DataFrame:
    """walk-forward 產 OOS score 矩陣（只在測試區塊有值，其餘 NaN）。

    make_model：回傳有 fit(X, y)/predict(X) 的新模型（每區塊重建、重訓）。
    embargo 固定 = horizon，不可調小。
    """
    dates = target_pct.index
    score = pd.DataFrame(np.nan, index=dates, columns=target_pct.columns, dtype="float32")
    for s, e in test_blocks(dates, first_test, step):
        tr_dates = train_slice(dates, s, horizon)
        if len(tr_dates) < min_train_days:
            continue
        x_tr, y_tr, _ = assemble_dataset(ranked, target_pct, tr_dates)
        if not len(y_tr):
            continue
        model = make_model()
        model.fit(x_tr, y_tr)

        te_dates = dates[s:e]
        x_te, _, meta = assemble_dataset(ranked, target_pct, te_dates)
        if not len(meta):
            continue
        pred = pd.Series(model.predict(x_te), name="score",
                         index=pd.MultiIndex.from_frame(meta))
        block = pred.unstack("stock_id")
        score.loc[block.index, block.columns] = block.astype("float32")
    return score
