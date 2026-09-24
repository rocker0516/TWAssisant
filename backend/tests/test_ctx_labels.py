import pandas as pd
import numpy as np
from app.research.ctx_matrix.labels import build_excess_labels, scan_thresholds


def _toy():
    dates = pd.date_range("2024-01-01", periods=25, freq="B")
    # 個股：第0天100，之後每天+1%；大盤：全程持平
    close = 100 * (1.01 ** np.arange(25))
    prices = pd.DataFrame({"stock_id": "1101", "date": dates, "close": close})
    market = pd.Series(100.0, index=dates)
    return prices, market


def test_excess_mfe_is_path_max():
    prices, market = _toy()
    out = build_excess_labels(prices, market, horizon=20)
    # 第0天：未來20根齊全 → 有列；exc_mfe20 ≈ 1.01^20-1 ≈ 22.0%
    row0 = out[out["date"] == prices["date"].iloc[0]].iloc[0]
    assert abs(row0["exc_mfe20"] - ((1.01 ** 20 - 1) * 100)) < 0.1


def test_incomplete_future_dropped():
    prices, market = _toy()
    out = build_excess_labels(prices, market, horizon=20)
    # 25根資料，只有前 25-20=5 天有完整未來窗
    assert len(out) == 5


def test_scan_picks_nearest_5pct():
    labels = pd.DataFrame({"exc_mfe20": [4.0] * 90 + [11.0] * 10})  # X=10 基率恰 10%
    res = scan_thresholds(labels, grid=(6, 8, 10, 12))
    assert res["base_rates"][12] == 0.0
    assert res["chosen_x"] == 10  # 10% 比 0% 更接近 5%
