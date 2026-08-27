import numpy as np
import pandas as pd
from app.research.ctx_matrix.evaluator import CellEvaluator, routing_delta


def _frame(n_days=80, n_stocks=30, seed=0):
    rng = np.random.default_rng(seed)
    dates = np.repeat(pd.date_range("2023-01-02", periods=n_days, freq="B"), n_stocks)
    df = pd.DataFrame({
        "date": dates,
        "stock_id": np.tile([f"s{i}" for i in range(n_stocks)], n_days),
        "exc_hit20": rng.binomial(1, 0.05, n_days * n_stocks),
        "atr_bucket": rng.integers(0, 3, n_days * n_stocks),
        "fold": np.repeat(np.arange(3), (n_days * n_stocks + 2) // 3)[: n_days * n_stocks],
        "is_holdout": False,
    })
    return df


def test_informative_mask_positive_ctrl():
    df = _frame()
    # 把某些列強制命中，遮罩剛好選中它們 → ctrl 必為正
    hot = np.zeros(len(df), bool)
    hot[::7] = True
    df.loc[hot, "exc_hit20"] = 1
    ev = CellEvaluator(df)
    res = ev.run(hot, scope=np.ones(len(df), bool))
    assert res is not None and res["ctrl"] > 0 and res["t_ctrl"] > 2


def test_random_mask_near_zero_ctrl():
    df = _frame()
    rng = np.random.default_rng(1)
    m = rng.random(len(df)) < 0.1
    ev = CellEvaluator(df)
    res = ev.run(m, scope=np.ones(len(df), bool))
    assert res is None or abs(res["ctrl"]) < 3.0


def test_too_few_days_returns_none():
    df = _frame(n_days=10)
    ev = CellEvaluator(df)
    assert ev.run(np.ones(len(df), bool), np.ones(len(df), bool)) is None


def test_routing_delta():
    assert routing_delta({"ctrl": 5.0}, {"ctrl": 2.0}) == 3.0


def test_holdout_rows_excluded_from_in_sample_computation():
    """holdout 列即使 fold 值與 in-sample 重疊，也不能污染主 ctrl/fold_ctrl；
    holdout_hit 應獨立反映 holdout 列自己的極端值。"""
    n_days = 80
    df = _frame(n_days=n_days)
    hot = np.zeros(len(df), bool)
    hot[::7] = True
    df.loc[hot, "exc_hit20"] = 1

    # 對照組：純 in-sample，無 holdout 污染
    ev_control = CellEvaluator(df)
    res_control = ev_control.run(hot, scope=np.ones(len(df), bool))
    assert res_control is not None

    # 混摻組：額外附加 holdout 列，涵蓋同樣 80 個交易日、fold 值與 in-sample 重疊，
    # exc_hit20 全設為 1（極端值），且遮罩也選中這些列
    dates = pd.date_range("2023-01-02", periods=n_days, freq="B")
    n_extra_stocks = 3
    extra = pd.DataFrame({
        "date": np.repeat(dates, n_extra_stocks),
        "stock_id": np.tile([f"h{i}" for i in range(n_extra_stocks)], n_days),
        "exc_hit20": 1,
        "atr_bucket": np.tile(np.arange(n_extra_stocks) % 3, n_days),
        "fold": np.resize(np.arange(3), n_days * n_extra_stocks),
        "is_holdout": True,
    })
    df_mixed = pd.concat([df, extra], ignore_index=True)
    mask_mixed = np.concatenate([hot, np.ones(len(extra), bool)])

    ev_mixed = CellEvaluator(df_mixed)
    res_mixed = ev_mixed.run(mask_mixed, scope=np.ones(len(df_mixed), bool))

    assert res_mixed is not None
    # 主計算不受污染：ctrl / fold_ctrl 應與對照組一致
    assert res_mixed["ctrl"] == res_control["ctrl"]
    assert res_mixed["fold_ctrl"] == res_control["fold_ctrl"]
    # holdout 反映極端值：holdout 列 exc_hit20 全為 1
    assert res_mixed["holdout_hit"] == 100.0
