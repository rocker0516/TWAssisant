"""presentation：估算價（台股升降單位、保守側取整）與健康條判讀句。"""

from __future__ import annotations

import pytest

from app.mlentry.serving.presentation import est_barrier_prices


@pytest.mark.parametrize("close, target, stop", [
    (16.35, 17.95, 15.55),      # 17.985 → 向下 0.05；15.5325 → 向上 0.05
    (43.15, 47.45, 41.00),      # 47.465 → 47.45；40.9925 → 41.00
    (9.50, 10.45, 9.03),        # 10.45 落 10–50 區（0.05）；9.025 落 <10 區（0.01）向上
    (100.0, 110.0, 95.0),       # 110 落 100–500（0.5）；95 落 50–100（0.1）
    (1000.0, 1100.0, 950.0),    # 1100 落 ≥1000（5）；950 落 500–1000（1）
])
def test_est_barrier_prices_rounds_conservatively(close, target, stop):
    assert est_barrier_prices(close) == (target, stop)


@pytest.mark.parametrize("close", [None, 0.0, -1.0, float("nan")])
def test_est_barrier_prices_invalid_close(close):
    assert est_barrier_prices(close) == (None, None)
