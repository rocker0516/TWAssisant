"""Level 1 PIT Universe 與 Target Generator 測試（FRS §3、§5、§11 洩漏防線）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import sqlite3

from app.research.level1 import targets as tg, universe as uv


# ── 靜態合格性 ──

def _stocks(rows):
    return pd.DataFrame(rows, columns=["id", "is_etf", "market", "industry_category"])


def test_eligible_ids_filters_non_common():
    df = _stocks([
        ("2330", 0, "上市", "半導體業"),        # 普通股 ✓
        ("6488", 0, "上櫃", "半導體業"),        # 上櫃普通股 ✓
        ("0050", 1, "上市", "所有證券"),        # ETF ✗
        ("2887A", 0, "上市", "金融保險"),       # 特別股（5 碼含字母）✗
        ("910322", 0, "上市", "存託憑證"),      # DR ✗
        ("020000", 0, "上市", "ETN"),           # ETN ✗
        ("IX0001", 0, None, "Index"),           # 指數 ✗
        ("9999", 0, None, "其他"),              # market 缺 → ✗
    ])
    assert uv.eligible_ids(df) == {"2330", "6488"}


def test_eligible_ids_category_backstop():
    """股號型態放行、但類別屬非普通股 → 仍排除（雙重把關）。"""
    df = _stocks([("1234", 0, "上市", "受益證券")])
    assert uv.eligible_ids(df) == set()


# ── PIT 存在性 ──

def _close_fixture():
    """三檔股票：A 全程、B 於 d3 上市、C 於 d4 後下市。交易日 d1~d6。"""
    dates = [f"2025-01-0{i}" for i in range(1, 7)]
    close = pd.DataFrame(index=dates, data={
        "AAAA": [10.0, 10.5, 11.0, 11.5, 12.0, 12.5],
        "BBBB": [np.nan, np.nan, 20.0, 21.0, 22.0, 23.0],
        "CCCC": [30.0, 29.0, 28.0, 27.0, np.nan, np.nan],
    })
    close.index.name = "date"
    close.columns.name = "stock_id"
    return close


def test_universe_is_point_in_time():
    mask = uv.universe_mask(_close_fixture())
    assert not mask.loc["2025-01-02", "BBBB"]   # 上市前不存在
    assert mask.loc["2025-01-03", "BBBB"]        # 上市日起進 U_t
    assert mask.loc["2025-01-04", "CCCC"]        # 下市前仍在
    assert not mask.loc["2025-01-05", "CCCC"]    # 下市後離開
    assert mask.sum(axis=1).tolist() == [2, 2, 3, 3, 2, 2]


# ── Target：報酬、百分位、缺值 ──

def test_forward_return_values():
    close = _close_fixture()
    fwd = tg.forward_returns(close, 2)
    assert fwd.loc["2025-01-01", "AAAA"] == pytest.approx(11.0 / 10.0 - 1)
    assert np.isnan(fwd.loc["2025-01-05", "AAAA"])   # 觀測窗未到
    assert np.isnan(fwd.loc["2025-01-03", "CCCC"])   # 下市 → 無 t+N 價


def test_percentile_within_universe_only():
    close = _close_fixture()
    mask = uv.universe_mask(close)
    built = tg.build_targets(close, mask, horizons=(2,))
    pct = built[2]["pct"]
    # d3：A +4.55%、B +10%、C 缺值 → 分母只有 2 檔
    assert pct.loc["2025-01-03", "AAAA"] == pytest.approx(0.5)
    assert pct.loc["2025-01-03", "BBBB"] == pytest.approx(1.0)
    assert np.isnan(pct.loc["2025-01-03", "CCCC"])
    # d1：A +10%、C −6.7%（B 未上市，不得混入分母）
    assert pct.loc["2025-01-01", "AAAA"] == pytest.approx(1.0)
    assert pct.loc["2025-01-01", "CCCC"] == pytest.approx(0.5)
    assert np.isnan(pct.loc["2025-01-01", "BBBB"])


def test_no_lookahead_in_universe():
    """洩漏防線：把未來一段行情整段抹掉，過去日的 U_t 與 pct 不得改變。"""
    close = _close_fixture()
    mask = uv.universe_mask(close)
    full = tg.build_targets(close, mask, horizons=(1,))[1]["pct"]

    truncated = close.iloc[:4]  # 只看到 d1~d4
    mask_t = uv.universe_mask(truncated)
    part = tg.build_targets(truncated, mask_t, horizons=(1,))[1]["pct"]

    # d1~d3（其 t+1 都在截斷範圍內）結果必須逐位相同
    a, b = full.iloc[:3], part.iloc[:3]
    pd.testing.assert_frame_equal(a, b)


def test_mean_pct_is_half():
    """百分位定義自檢：每日有效股票的 pct 均值 ≈ (n+1)/2n → 全體近 0.5。"""
    rng = np.random.default_rng(0)
    dates = pd.date_range("2025-01-01", periods=30).astype(str)
    close = pd.DataFrame(
        100 * np.cumprod(1 + rng.normal(0, 0.02, (30, 50)), axis=0),
        index=dates, columns=[f"{1000+i}" for i in range(50)])
    mask = uv.universe_mask(close)
    pct = tg.build_targets(close, mask, horizons=(5,))[5]["pct"]
    assert float(pct.stack().mean()) == pytest.approx(0.51, abs=0.01)


# ── Trading Eligibility（FRS §3 v1.1）──

def _turnover_fixture():
    """AAAA 全程高量、BBBB 全程低量、CCCC 前 10 日低量後 5 日暴量。"""
    dates = [f"2025-01-{d:02d}" for d in range(1, 16)]
    return pd.DataFrame(index=dates, data={
        "AAAA": [2e8] * 15,
        "BBBB": [1e6] * 15,
        "CCCC": [1e6] * 10 + [1e9] * 5,
    })


def _flat_close(t):
    return pd.DataFrame(10.0, index=t.index, columns=t.columns)


def _no_punish(t):
    return pd.DataFrame(False, index=t.index, columns=t.columns)


def test_adv20_warmup_is_nan():
    a = uv.adv20(_turnover_fixture())
    assert a.iloc[:9].isna().all().all()        # 不足 10 日 → NaN
    assert not a.iloc[9:].isna().any().any()


def test_tradable_mask_applies_liquidity_floor():
    t = _turnover_fixture()
    m = uv.tradable_mask(_flat_close(t), t, _no_punish(t))
    assert m.loc["2025-01-15", "AAAA"]          # 2 億 ≥ 5,000 萬
    assert not m.loc["2025-01-15", "BBBB"]      # 100 萬 < 5,000 萬
    assert not m.iloc[:9].any().any()           # 暖身期 NaN → 一律不在 U_t


def test_tradable_mask_liquidity_is_backward_looking_only():
    """CCCC 於 d11 才放量；d10 的 ADV20 不得被之後的成交值抬高（截斷未來不改變過去）。"""
    t = _turnover_fixture()
    m = uv.tradable_mask(_flat_close(t), t, _no_punish(t))
    assert not m.loc["2025-01-10", "CCCC"]
    truncated = uv.tradable_mask(_flat_close(t).iloc[:10], t.iloc[:10],
                                 _no_punish(t).iloc[:10])
    assert m.iloc[:10].equals(truncated)


def test_punish_mask_window_is_inclusive():
    dates = pd.Index([f"2025-01-{d:02d}" for d in range(1, 8)])
    w = pd.DataFrame([{"stock_id": "AAAA", "begin_date": "2025-01-03",
                       "end_date": "2025-01-05"}])
    pm = uv.punish_mask(w, dates, pd.Index(["AAAA", "BBBB"]))
    assert not pm.loc["2025-01-02", "AAAA"]
    assert pm.loc["2025-01-03", "AAAA"]         # 起日含
    assert pm.loc["2025-01-05", "AAAA"]         # 迄日含
    assert not pm.loc["2025-01-06", "AAAA"]
    assert not pm["BBBB"].any()


def test_punish_mask_ignores_unknown_stock():
    w = pd.DataFrame([{"stock_id": "ZZZZ", "begin_date": "2025-01-01",
                       "end_date": "2025-01-01"}])
    pm = uv.punish_mask(w, pd.Index(["2025-01-01"]), pd.Index(["AAAA"]))
    assert not pm.any().any()


def test_tradable_mask_excludes_punished_days():
    t = _turnover_fixture()
    w = pd.DataFrame([{"stock_id": "AAAA", "begin_date": "2025-01-12",
                       "end_date": "2025-01-13"}])
    m = uv.tradable_mask(_flat_close(t), t, uv.punish_mask(w, t.index, t.columns))
    assert m.loc["2025-01-11", "AAAA"]
    assert not m.loc["2025-01-12", "AAAA"]
    assert not m.loc["2025-01-13", "AAAA"]
    assert m.loc["2025-01-14", "AAAA"]          # 處置結束後回歸


def test_adv20_floor_is_frozen_constant():
    """5,000 萬是 FRS §3 凍結常數（設計 §4.1）。改動此值等同以 Universe 做 Target Mining。"""
    assert uv.ADV20_FLOOR == 5e7


# ── 單一組裝入口（設計 §6：杜絕研究／生產各自組裝）──

def _memory_db():
    """最小 DB：台積電（高量、1/12–1/13 處置）、殼股（低量）、ETF（應被靜態排除）。"""
    con = sqlite3.connect(":memory:")
    con.executescript(
        "CREATE TABLE stocks (id TEXT, name TEXT, is_etf INT, market TEXT,"
        " industry_category TEXT, listed_date TEXT);"
        "CREATE TABLE daily_prices (stock_id TEXT, date TEXT, close REAL, turnover REAL);"
        "CREATE TABLE attention_listings (stock_id TEXT, date TEXT, kind TEXT,"
        " begin_date TEXT, end_date TEXT);"
    )
    con.executemany("INSERT INTO stocks VALUES (?,?,?,?,?,?)", [
        ("2330", "台積電", 0, "上市", "半導體業", "1994-09-05"),
        ("8444", "殼股", 0, "上市", "其他業", "2010-01-01"),
        ("0050", "ETF", 1, "上市", "所有證券", "2003-06-30"),
    ])
    rows = []
    for d in range(1, 16):
        day = f"2025-01-{d:02d}"
        rows += [("2330", day, 1000.0, 4e9), ("8444", day, 5.0, 1e5),
                 ("0050", day, 150.0, 5e9)]
    con.executemany("INSERT INTO daily_prices VALUES (?,?,?,?)", rows)
    con.executemany("INSERT INTO attention_listings VALUES (?,?,?,?,?)", [
        ("2330", "2025-01-12", "punish", "2025-01-12", "2025-01-13"),
        ("8444", "2025-01-05", "notice", None, None),   # 注意股不得被排除
    ])
    return con


def test_build_tradable_universe_end_to_end():
    con = _memory_db()
    close, mask = uv.build_tradable_universe(con)
    con.close()
    assert "0050" not in close.columns              # ETF 靜態排除
    assert mask.loc["2025-01-11", "2330"]           # 高量普通股在 U_t
    assert not mask.loc["2025-01-11", "8444"]       # 10 萬元/日 < 5,000 萬
    assert not mask.loc["2025-01-12", "2330"]       # 處置期間排除
    assert not mask.loc["2025-01-13", "2330"]
    assert mask.loc["2025-01-14", "2330"]           # 處置結束後回歸
    assert not mask.iloc[:9].any().any()            # ADV20 暖身期


def test_build_tradable_universe_keeps_notice_stocks():
    """注意股（notice）不是不可交易——只有處置（punish）才排除。"""
    con = sqlite3.connect(":memory:")
    con.executescript(
        "CREATE TABLE stocks (id TEXT, name TEXT, is_etf INT, market TEXT,"
        " industry_category TEXT, listed_date TEXT);"
        "CREATE TABLE daily_prices (stock_id TEXT, date TEXT, close REAL, turnover REAL);"
        "CREATE TABLE attention_listings (stock_id TEXT, date TEXT, kind TEXT,"
        " begin_date TEXT, end_date TEXT);"
    )
    con.execute("INSERT INTO stocks VALUES "
                "('2330','台積電',0,'上市','半導體業','1994-09-05')")
    con.executemany("INSERT INTO daily_prices VALUES (?,?,?,?)", [
        ("2330", f"2025-01-{d:02d}", 1000.0, 4e9) for d in range(1, 16)])
    con.execute("INSERT INTO attention_listings VALUES "
                "('2330','2025-01-12','notice',NULL,NULL)")
    _, mask = uv.build_tradable_universe(con)
    con.close()
    assert mask.loc["2025-01-12", "2330"]
