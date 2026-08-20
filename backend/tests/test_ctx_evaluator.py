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
