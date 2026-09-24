"""OOS 路徑與 Production 路徑的一致性護欄（設計 §10）。

本次改版的三個裂縫（訓練窗、推論母體、快照漂移）皆源於兩條路徑各自實作等價邏輯。
這裡把「必須共用同一個函式」的契約釘成測試。
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from app.research.level1 import universe as uv, walkforward as wf
from app.research.level1.features import assemble_dataset


def _memory_db():
    """兩檔高量普通股，20 個交易日，無處置。"""
    con = sqlite3.connect(":memory:")
    con.executescript(
        "CREATE TABLE stocks (id TEXT, name TEXT, is_etf INT, market TEXT,"
        " industry_category TEXT, listed_date TEXT);"
        "CREATE TABLE daily_prices (stock_id TEXT, date TEXT, close REAL, turnover REAL);"
        "CREATE TABLE attention_listings (stock_id TEXT, date TEXT, kind TEXT,"
        " begin_date TEXT, end_date TEXT);"
    )
    con.executemany("INSERT INTO stocks VALUES (?,?,?,?,?,?)", [
        ("2330", "甲", 0, "上市", "半導體業", "1994-09-05"),
        ("2317", "乙", 0, "上市", "電子業", "1991-06-18"),
    ])
    rows = []
    for d in range(1, 21):
        day = f"2025-01-{d:02d}"
        rows += [("2330", day, 100.0 + d, 4e9), ("2317", day, 50.0 + d, 3e9)]
    con.executemany("INSERT INTO daily_prices VALUES (?,?,?,?)", rows)
    return con


def test_universe_comes_from_a_single_entry_point():
    """手動組裝與 build_tradable_universe 必須逐格相同——任何呼叫端都不該自己拼。"""
    con = _memory_db()
    close, mask = uv.build_tradable_universe(con)

    elig = uv.eligible_ids(uv.load_stocks(con))
    manual_close = uv.close_matrix(uv.load_close_prices(con, elig))
    manual = uv.tradable_mask(
        manual_close,
        uv.load_turnover(con, elig, manual_close.index, manual_close.columns),
        uv.punish_mask(uv.load_punish_windows(con), manual_close.index,
                       manual_close.columns),
    )
    con.close()
    assert close.equals(manual_close)
    assert mask.equals(manual)


def test_build_tradable_universe_is_deterministic():
    """同一個 con 呼叫兩次必須完全相同（無隨機、無時間相依）。"""
    con = _memory_db()
    c1, m1 = uv.build_tradable_universe(con)
    c2, m2 = uv.build_tradable_universe(con)
    con.close()
    assert c1.equals(c2)
    assert m1.equals(m2)


def _synthetic_ranked(dates, cols, seed=0):
    rng = np.random.default_rng(seed)
    return {f"f{i}": pd.DataFrame(rng.random((len(dates), len(cols))),
                                  index=dates, columns=cols) for i in range(3)}


def test_training_set_never_contains_prediction_date_or_later():
    """Production 訓練集不得含 pred_date 當日或之後的任何列（P0 leakage 防線）。"""
    dates = pd.Index([f"2025-03-{d:02d}" for d in range(1, 29)])
    cols = pd.Index(["2330", "2317"])
    ranked = _synthetic_ranked(dates, cols)
    # target 全期皆有值——若訓練窗有誤，未來列會直接混進訓練集
    pct = pd.DataFrame(0.5, index=dates, columns=cols)

    pred_date = "2025-03-20"
    train_dates = wf.train_slice_for_date(dates, pred_date, embargo=5)
    _, _, meta = assemble_dataset(ranked, pct, train_dates)

    assert len(meta) > 0
    assert meta["date"].max() < pred_date
    assert meta["date"].max() == "2025-03-14"       # index 19 − 5 → dates[:14]


def test_production_and_oos_training_windows_are_identical():
    """同一預測日下，兩條路徑取得的訓練集必須逐列相同。"""
    dates = pd.Index([f"2025-03-{d:02d}" for d in range(1, 29)])
    cols = pd.Index(["2330", "2317"])
    ranked = _synthetic_ranked(dates, cols)
    pct = pd.DataFrame(0.5, index=dates, columns=cols)

    i = 19
    x_prod, y_prod, m_prod = assemble_dataset(
        ranked, pct, wf.train_slice_for_date(dates, dates[i], embargo=5))
    x_oos, y_oos, m_oos = assemble_dataset(
        ranked, pct, wf.train_slice(dates, i, embargo=5))

    assert np.array_equal(x_prod, x_oos)
    assert np.array_equal(y_prod, y_oos)
    assert m_prod.equals(m_oos)
