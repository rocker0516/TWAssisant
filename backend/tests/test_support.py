"""支撐/壓力偵測測試：分類、confluence 強度、距離過濾、選位（純函式，免 DB）。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.engines.support import detect_levels


def _series(prices: list[float], vols: list[int] | None = None) -> pd.DataFrame:
    """把收盤序列包成 OHLCV（high/low 給小幅振盪），date 升冪。"""
    n = len(prices)
    vols = vols or [1000] * n
    base = pd.Timestamp("2024-01-01")
    return pd.DataFrame(
        {
            "date": [(base + pd.Timedelta(days=i)).date() for i in range(n)],
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices,
            "volume": vols,
        }
    )


def test_classifies_by_current_price():
    df = _series([100 + np.sin(i / 5) * 3 for i in range(120)])
    close = float(df["close"].iloc[-1])
    levels = detect_levels(df, close, {"ma20": close * 0.95, "ma60": close * 1.04})
    assert levels, "應產出至少一條"
    for l in levels:
        if l.kind == "support":
            assert l.price <= close
        else:
            assert l.price >= close


def test_confluence_beats_single_source():
    """同一價位有多來源重疊 → 強度應高於只有單一均線的價位。"""
    # 在 90 附近反覆做出前低（pivot），且均線也落在 90 → 多來源重疊
    prices = []
    for _ in range(6):
        prices += [100, 96, 92, 90, 92, 96, 100, 104, 100]  # 每段在 90 觸底
    df = _series(prices)
    close = float(df["close"].iloc[-1])
    levels = detect_levels(df, close, {"ma60": 90.0, "ma120": 90.0})
    sups = [l for l in levels if l.kind == "support"]
    assert sups
    # 90 附近那條（多來源）應為最強
    strongest = max(sups, key=lambda l: l.strength)
    assert abs(strongest.price - 90) / 90 < 0.03
    assert strongest.strength >= 80


def test_far_levels_filtered():
    """超過距離上限的位不應入選（除非保底最近一條）。"""
    df = _series([50 + i * 0.5 for i in range(200)])  # 一路走高，底部遠在 -40%+
    close = float(df["close"].iloc[-1])
    levels = detect_levels(df, close, {})
    for l in levels:
        # 保底機制可能留一條最近的；其餘須在 25% 內
        assert abs(l.distance_pct) <= 25.0 or l is levels[0]


def test_empty_inputs():
    assert detect_levels(pd.DataFrame(), 100.0, {}) == []
    assert detect_levels(_series([100, 101, 102]), None, {}) == []
