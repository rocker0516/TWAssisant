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
