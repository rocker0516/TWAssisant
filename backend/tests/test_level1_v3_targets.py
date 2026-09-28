"""Level 1 v3：價量載入、漲停判定、Entry/fill、R_net、洩漏防線。"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
import pytest

from app.research.level1 import prices as pr
from app.research.level1 import targets_v3 as t3
from app.research.level2.costs import up_limit


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


def test_limit_up_matrix_matches_scalar_rule():
    prev = pd.DataFrame({
        "A": [9.99, 49.5, 95.0, 999.0, np.nan],
        "B": [10.0, 100.0, 1000.0, 2475.0, 33.3],
    })
    lim = t3.limit_up_from_prev(prev)
    for c in prev.columns:
        for i, v in enumerate(prev[c]):
            if np.isnan(v):
                assert np.isnan(lim[c].iloc[i])
            else:
                assert lim[c].iloc[i] == pytest.approx(up_limit(v))


def _oc():
    """4 個交易日、4 檔。決策日 d1 的 Entry = d2 開盤。"""
    dates = pd.Index([f"2025-01-0{i}" for i in range(1, 5)], name="date")
    close = pd.DataFrame(index=dates, data={
        "LIM": [100.0, 110.0, 112.0, 115.0],   # d2 開盤即漲停（110 = up_limit(100)）
        "OK":  [100.0, 104.0, 106.0, 108.0],   # 正常成交
        "GAP": [100.0, np.nan, 101.0, 102.0],  # d2 停牌 → NO_TRADE
        "DN":  [100.0, 91.0, 92.0, 93.0],      # d2 開盤跌停 → 視為成交
    })
    open_ = pd.DataFrame(index=dates, data={
        "LIM": [99.0, 110.0, 111.0, 114.0],
        "OK":  [99.0, 105.0, 105.5, 107.0],
        "GAP": [99.0, np.nan, 100.5, 101.5],
        "DN":  [99.0, 90.0, 91.5, 92.5],
    })
    close.columns.name = open_.columns.name = "stock_id"
    return open_, close


def test_entry_and_fill_codes():
    open_, close = _oc()
    entry, fill = t3.entry_and_fill(open_, close)
    d1 = "2025-01-01"
    assert fill.loc[d1, "LIM"] == t3.LIMIT_UP_UNFILLED and np.isnan(entry.loc[d1, "LIM"])
    assert fill.loc[d1, "OK"] == t3.FILLED and entry.loc[d1, "OK"] == 105.0
    assert fill.loc[d1, "GAP"] == t3.NO_TRADE and np.isnan(entry.loc[d1, "GAP"])
    assert fill.loc[d1, "DN"] == t3.FILLED and entry.loc[d1, "DN"] == 90.0
    assert (fill.loc["2025-01-04"] == t3.NO_TRADE).all()     # 最後一日無 t+1
    assert fill.dtypes.iloc[0] == "int8"


def test_net_return_formula_and_cost():
    open_, close = _oc()
    entry, _ = t3.entry_and_fill(open_, close)
    net1 = t3.net_returns(entry, close, 1)
    # OK：d2 開盤 105 進，d2 收盤 104 出
    assert net1.loc["2025-01-01", "OK"] == pytest.approx(104.0 / 105.0 - 1 - 0.00585)
    net2 = t3.net_returns(entry, close, 2)
    assert net2.loc["2025-01-01", "OK"] == pytest.approx(106.0 / 105.0 - 1 - 0.00585)
    assert np.isnan(net1.loc["2025-01-01", "LIM"])            # 未成交 → NaN


def test_build_targets_masks_universe_and_truncation_does_not_change_past():
    open_, close = _oc()
    mask = close.notna()
    mask.loc[:, "DN"] = False                                  # DN 不在 U_t
    targets, entry, fill = t3.build_targets_v3(open_, close, mask, horizons=(1, 2))
    assert set(targets) == {1, 2}
    assert np.isnan(targets[1]["net"].loc["2025-01-01", "DN"])
    assert targets[1]["net"].dtypes.iloc[0] == "float32"
    # 截斷未來（去掉最後一日）不得改變過去任何非 NaN 值
    t_cut, _, _ = t3.build_targets_v3(
        open_.iloc[:-1], close.iloc[:-1], mask.iloc[:-1], horizons=(1, 2)
    )
    full = targets[2]["net"].iloc[:-1]
    cut = t_cut[2]["net"]
    both = full.notna() & cut.notna()
    assert np.allclose(full[both].fillna(0).to_numpy(), cut[both].fillna(0).to_numpy())
    assert cut.loc["2025-01-02"].isna().all()                 # 截斷後最後可算列變 NaN
