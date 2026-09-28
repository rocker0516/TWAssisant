"""Level 1 v3：價量載入、漲停判定、Entry/fill、R_net、洩漏防線。"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from app.research.level1 import prices as pr


def _db_with_prices():
    con = sqlite3.connect(":memory:")
    con.execute("""CREATE TABLE daily_prices(
        stock_id TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,
        volume INTEGER, turnover REAL)""")
    con.executemany(
        "INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?,?)",
        [("1111", "2025-01-01", 10.0, 10.5, 9.8, 10.2, 1000, 10200.0),
         ("1111", "2025-01-02", 10.3, 10.6, 10.1, 10.4, 1100, 11440.0),
         ("2222", "2025-01-02", 20.0, 20.5, 19.5, 20.1, 500, 10050.0),
         ("9999", "2025-01-01", 1.0, 1.0, 1.0, 1.0, 1, 1.0)])   # 不在 columns
    con.execute("CREATE TABLE stocks(id TEXT, sector_id INTEGER)")
    con.executemany("INSERT INTO stocks VALUES (?,?)",
                    [("1111", 3), ("2222", None), ("9999", 5)])
    con.execute("CREATE TABLE market_index(date TEXT, close REAL)")
    con.executemany("INSERT INTO market_index VALUES (?,?)",
                    [("2025-01-01", 20000.0), ("2025-01-02", 20100.0)])
    return con


def test_load_price_matrices_aligns_to_index_columns():
    con = _db_with_prices()
    index = pd.Index(["2025-01-01", "2025-01-02"], name="date")
    columns = pd.Index(["1111", "2222"], name="stock_id")
    mats = pr.load_price_matrices(con, index, columns)
    assert set(mats) == {"open", "high", "low", "volume", "turnover"}
    assert mats["open"].shape == (2, 2)
    assert mats["open"].loc["2025-01-02", "1111"] == 10.3
    assert np.isnan(mats["open"].loc["2025-01-01", "2222"])   # 缺列 → NaN
    assert "9999" not in mats["open"].columns
    assert mats["turnover"].dtypes.iloc[0] == "float64"


def test_load_sector_map_and_market_close():
    con = _db_with_prices()
    sec = pr.load_sector_map(con)
    assert sec.loc["1111"] == 3.0
    assert np.isnan(sec.loc["2222"])
    mkt = pr.load_market_close(con)
    assert list(mkt.index) == ["2025-01-01", "2025-01-02"]
    assert mkt.loc["2025-01-02"] == 20100.0
