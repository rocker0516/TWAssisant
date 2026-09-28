"""Level 1 v3 評估：q25 校準、Top-K 淨報酬、不進場訊號、walk-forward v3 embargo。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from app.research.level1 import evaluation_v3 as e3


def _mats(n_days=300, n_stocks=200, seed=0, sigma=0.03):
    rng = np.random.default_rng(seed)
    dates = pd.Index(pd.date_range("2025-01-01", periods=n_days).astype(str), name="date")
    cols = pd.Index([f"{1000+i}" for i in range(n_stocks)], name="stock_id")
    net = pd.DataFrame(rng.normal(0, sigma, (n_days, n_stocks)), index=dates, columns=cols)
    return dates, cols, net


def test_calibration_true_quantile_breaches_about_alpha():
    _, _, net = _mats()
    q25 = pd.DataFrame(norm.ppf(0.25) * 0.03, index=net.index, columns=net.columns)
    c = e3.calibration_q25(q25, net)
    assert abs(c["breach_rate"] - 0.25) < 0.01
    assert c["n_cells"] == net.size
    assert c["abs_error_pp"] < 1.0


def test_topk_net_perfect_score_beats_universe_and_win_rate():
    _, _, net = _mats(n_days=100)
    out = e3.topk_net_summary(net.copy(), net, ks=(20,))["top20"]
    assert out["mean_net_pct"] > 0
    assert out["excess_pct"] > 0
    assert out["day_win_rate"] == pytest.approx(1.0)
    assert out["n_days"] == 100


def test_topk_net_ignores_missing_net():
    _, _, net = _mats(n_days=50, n_stocks=60)
    score = net.copy()
    holed = net.copy()
    holed.iloc[:, :30] = np.nan                            # 前 30 檔未成交
    out = e3.topk_net_summary(score, holed, ks=(20,))["top20"]
    manual = holed.iloc[:, 30:].apply(lambda r: r.nlargest(20).mean(), axis=1).mean() * 100
    assert out["mean_net_pct"] == pytest.approx(round(float(manual), 3), abs=1e-3)


def test_no_entry_summary_flags_days_with_nonpositive_max():
    dates, cols, net = _mats(n_days=40, n_stocks=50)
    score = pd.DataFrame(0.01, index=dates, columns=cols)
    score.iloc[:10] = -0.01                                 # 前 10 日全負 → 旗標
    net.iloc[:10] = -0.05                                   # 那些日子池子確實跌
    out = e3.no_entry_summary(score, net)
    assert out["n_days_flagged"] == 10
    assert out["share_flagged"] == pytest.approx(0.25)
    assert out["univ_net_pct_flagged"] == pytest.approx(-5.0)
    assert out["diff_pp"] < 0                                # 旗標日比其他日差 → 訊號有用
    assert out["top20_net_pct_flagged"] == pytest.approx(-5.0)


def test_evaluate_v3_bundle_keys():
    _, _, net = _mats(n_days=80)
    out = e3.evaluate_v3(net.copy(), net)
    assert {"mean_ic", "topk", "calibration", "no_entry", "evaluation_n_mean"} <= set(out)
    by = e3.evaluate_v3_by_period(net.copy(), net, {"a": ("2025-01-01", "2025-02-01")})
    assert "a" in by and "topk" in by["a"]
