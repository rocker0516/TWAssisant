"""Level 1 PIT Universe 與 Target Generator 測試（FRS §3、§5、§11 洩漏防線）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

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
