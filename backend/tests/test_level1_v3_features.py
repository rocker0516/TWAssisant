"""Level 1 v3 特徵：無未來資訊、U_t-only 寬度、FeatureSet 缺值政策。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.research.level1 import features_v3 as f3
from app.research.level2.costs import up_limit


def _ohlc(n_days=70, n_stocks=6, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.Index(pd.date_range("2025-01-01", periods=n_days).astype(str), name="date")
    cols = pd.Index([f"{1000+i}" for i in range(n_stocks)], name="stock_id")
    close = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.02, (n_days, n_stocks)), axis=0)),
                         index=dates, columns=cols)
    open_ = close.shift(1).fillna(close) * (1 + rng.normal(0, 0.01, close.shape))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.01, close.shape))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.01, close.shape))
    return open_, high, low, close


def _assert_no_future(build, *mats, t_pos=40):
    """擾動 t+1 之後的所有輸入，t 及之前的特徵值不得改變。"""
    base = build(*mats)
    pert = [m.copy() for m in mats]
    for m in pert:
        m.iloc[t_pos + 1:] = m.iloc[t_pos + 1:] * 1.37 + 3.0
    after = build(*pert)
    for k in base:
        a, b = base[k].iloc[:t_pos + 1], after[k].iloc[:t_pos + 1]
        assert np.allclose(a.fillna(-999).to_numpy(), b.fillna(-999).to_numpy()), k


def test_execution_features_no_future():
    open_, _, _, close = _ohlc()
    _assert_no_future(f3.build_execution_features, open_, close)


def test_dist_limit_up_zero_when_locked_and_lockup_counts():
    open_, _, _, close = _ohlc(n_days=30, n_stocks=1)
    c = close.copy()
    # 第 20~24 天連續鎖漲停
    for i in range(20, 25):
        c.iloc[i, 0] = up_limit(float(c.iloc[i - 1, 0]))
    feats = f3.build_execution_features(open_, c)
    assert feats["dist_limit_up"].iloc[22, 0] == pytest.approx(0.0, abs=1e-9)
    assert feats["lockup_days20"].iloc[24, 0] == 5
    assert feats["lockup_days20"].iloc[19, 0] == 0


def test_overnight_minus_intraday_sign():
    """全部漲幅來自跳空（開盤=前收×1.01，收盤=開盤）→ 差值為正。"""
    dates = pd.Index(pd.date_range("2025-01-01", periods=30).astype(str), name="date")
    close = pd.DataFrame({"A": 100 * 1.01 ** np.arange(30)}, index=dates)
    open_ = close.copy()                       # 開盤 = 收盤 → 盤中報酬 0
    feats = f3.build_execution_features(open_, close)
    assert feats["overnight_minus_intraday20"].iloc[-1, 0] > 0.15
    assert feats["gap_std20"].iloc[-1, 0] == pytest.approx(0.0, abs=1e-9)


def test_scale_features_no_future_and_downside_only_negative():
    open_, high, low, close = _ohlc()
    _assert_no_future(f3.build_scale_features, high, low, close)
    up_only = pd.DataFrame({"A": 100 * 1.01 ** np.arange(40)},
                           index=pd.Index(pd.date_range("2025-01-01", periods=40).astype(str)))
    feats = f3.build_scale_features(up_only, up_only, up_only)
    assert feats["downside_vol20"].iloc[-1, 0] == pytest.approx(0.0, abs=1e-12)
    vol20_last = feats["vol20"].iloc[-1, 0]
    assert vol20_last > 0 or vol20_last == pytest.approx(0.0, abs=1e-6)
    assert feats["atr14_pct"].iloc[-1, 0] > 0


def test_market_features_broadcast_and_no_future():
    open_, high, low, close = _ohlc()
    mask = close.notna()
    mkt = pd.Series(100 * np.exp(np.cumsum(np.full(len(close), 0.001))), index=close.index)
    feats = f3.build_market_features(close, mask, mkt)
    assert set(feats) == {"mkt_ret5", "mkt_ret20", "mkt_vol20", "breadth_ma20", "dispersion"}
    for k, m in feats.items():
        assert m.shape == close.shape, k
        row = m.iloc[-1].dropna()
        assert row.nunique() == 1, k                      # 同日各股相同（情境特徵）


def test_market_features_no_future():
    _, _, _, close = _ohlc()
    mask = close.notna()
    mkt = pd.Series(100 * np.exp(np.cumsum(np.full(len(close), 0.001))), index=close.index)
    base = f3.build_market_features(close, mask, mkt)
    c2, m2 = close.copy(), mkt.copy()
    c2.iloc[41:] *= 1.5
    m2.iloc[41:] *= 1.5
    after = f3.build_market_features(c2, mask, m2)
    for k in base:
        a = base[k].iloc[:41].fillna(-9).to_numpy()
        b = after[k].iloc[:41].fillna(-9).to_numpy()
        assert np.allclose(a, b), k


def test_breadth_and_dispersion_use_universe_only():
    open_, high, low, close = _ohlc(n_days=40, n_stocks=4)
    mkt = pd.Series(1.0, index=close.index)
    mask_all = close.notna()
    mask_drop = mask_all.copy()
    mask_drop.iloc[:, 0] = False                            # 第 0 檔踢出 U_t
    b_all = f3.build_market_features(close, mask_all, mkt)["breadth_ma20"].iloc[-1, 0]
    b_drop = f3.build_market_features(close, mask_drop, mkt)["breadth_ma20"].iloc[-1, 0]
    # 手算：只用 U_t 內三檔
    ma20 = close.rolling(20, min_periods=10).mean()
    manual = float((close > ma20).iloc[-1, 1:].mean())
    assert b_drop == pytest.approx(manual)
    assert b_all != b_drop or close.shape[1] == 1
    d_drop = f3.build_market_features(close, mask_drop, mkt)["dispersion"].iloc[-1, 0]
    assert d_drop == pytest.approx(float(close.pct_change().iloc[-1, 1:].std()))


def test_direction_features_sector_neutral_and_amihud():
    open_, high, low, close = _ohlc(n_days=70, n_stocks=4)
    volume = pd.DataFrame(1000.0, index=close.index, columns=close.columns)
    turnover = close * volume
    mask = close.notna()
    sector = pd.Series([1.0, 1.0, 2.0, np.nan], index=close.columns)
    to_rank, raw = f3.build_direction_features(close, volume, turnover, mask, sector)
    assert set(raw) == {"dollar_vol20"}
    assert {"ret20", "sec_neutral_ret20", "amihud20", "vr5_60", "pos240"} <= set(to_rank)
    sn = to_rank["sec_neutral_ret20"].iloc[-1]
    # 類股 1 兩檔的中性化值和為 0；無類股者 NaN
    assert sn.iloc[0] + sn.iloc[1] == pytest.approx(0.0, abs=1e-12)
    assert sn.iloc[2] == pytest.approx(0.0, abs=1e-12)       # 單檔類股 → 等於自己均值
    assert np.isnan(sn.iloc[3])
    assert (to_rank["amihud20"].iloc[-1] > 0).all()
    assert raw["dollar_vol20"].iloc[-1, 0] == pytest.approx(np.log1p(turnover.iloc[-20:, 0].mean()))


def test_feature_set_names_order_and_subset():
    idx = pd.Index(["2025-01-01"], name="date")
    cols = pd.Index(["1111"], name="stock_id")
    one = pd.DataFrame(1.0, index=idx, columns=cols)
    fs = f3.FeatureSet(ranked={"r1": one, "r2": one}, raw={"x1": one})
    assert fs.names == ["r1", "r2", "x1"]
    sub = fs.subset(["x1", "r2"])
    assert sub.names == ["r2", "x1"]                        # 保持 ranked 前 raw 後
    with pytest.raises(KeyError):
        fs.subset(["nope"])


def test_assemble_v3_missing_policy():
    idx = pd.Index(["2025-01-01", "2025-01-02"], name="date")
    cols = pd.Index(["1111", "2222"], name="stock_id")
    target = pd.DataFrame([[0.01, np.nan], [0.02, -0.03]], index=idx, columns=cols)
    ranked = {"r": pd.DataFrame([[0.2, np.nan], [np.nan, 0.9]], index=idx, columns=cols)}
    raw = {"x": pd.DataFrame([[1.5, 2.5], [np.nan, 4.5]], index=idx, columns=cols)}
    x, y, meta = f3.assemble_v3(f3.FeatureSet(ranked, raw), target, idx)
    assert x.shape == (3, 2) and len(y) == 3                # target NaN 列被丟
    assert list(meta.columns) == ["date", "stock_id"]
    # 列序 = (d1,1111), (d2,1111), (d2,2222)
    assert x[1, 0] == pytest.approx(0.5)                    # ranked 缺值 → 0.5
    assert np.isnan(x[1, 1])                                # raw 缺值 → NaN 保留
    assert x[2, 1] == pytest.approx(4.5)
    assert x.dtype == np.float32


def test_families_cover_all_and_baseline_is_subset():
    all_names = {n for ns in f3.FAMILIES.values() for n in ns}
    assert set(f3.BASELINE) <= all_names
    assert list(f3.FAMILIES) == ["A", "B", "C", "DE", "F"]
    assert "ret20_bull" not in all_names                     # v2 交互項不進 v3


def test_build_feature_set_representation_by_role():
    open_, high, low, close = _ohlc(n_days=80, n_stocks=5)
    volume = pd.DataFrame(1000.0, index=close.index, columns=close.columns)
    turnover = close * volume
    mask = close.notna()
    mkt = pd.Series(np.linspace(100, 110, len(close)), index=close.index)
    sector = pd.Series(1.0, index=close.columns)
    fs = f3.build_feature_set(open_, high, low, close, volume, turnover, mask, mkt, sector,
                              fund_feats=None)
    expected_raw = (set(f3.FAMILIES["A"]) | set(f3.FAMILIES["B"]) | set(f3.FAMILIES["C"])
                    | {"dollar_vol20"})
    assert set(fs.raw) == expected_raw
    assert "ret20" in fs.ranked and "vol20" in fs.raw
    # ranked 值域 (0,1]
    r = fs.ranked["ret20"].iloc[-1].dropna()
    assert r.min() > 0 and r.max() <= 1
    assert list(f3.FAMILIES["F"]) == list(f3.FUND_NAMES)     # 族群定義不變
    assert not (set(f3.FUND_NAMES) & set(fs.names))          # 無基本面時特徵集裡沒有任何一個


def test_build_feature_set_does_not_mutate_families():
    open_, high, low, close = _ohlc(n_days=80, n_stocks=5)
    volume = pd.DataFrame(1000.0, index=close.index, columns=close.columns)
    turnover = close * volume
    mask = close.notna()
    mkt = pd.Series(np.linspace(100, 110, len(close)), index=close.index)
    sector = pd.Series(1.0, index=close.columns)
    snapshot = {k: list(v) for k, v in f3.FAMILIES.items()}

    f3.build_feature_set(open_, high, low, close, volume, turnover, mask, mkt, sector,
                         fund_feats=None)
    assert {k: list(v) for k, v in f3.FAMILIES.items()} == snapshot

    fund_feats = {"rev_yoy": pd.DataFrame(0.1, index=close.index, columns=close.columns)}
    fs = f3.build_feature_set(open_, high, low, close, volume, turnover, mask, mkt, sector,
                              fund_feats=fund_feats)
    assert {k: list(v) for k, v in f3.FAMILIES.items()} == snapshot
    assert "rev_yoy" in fs.ranked
