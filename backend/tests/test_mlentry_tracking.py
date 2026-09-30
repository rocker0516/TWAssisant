"""追蹤中：以 barrier 引擎計算近 10 日推薦的即時路徑；與成熟引擎語意一致。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.mlentry.config import LabelConfig
from app.mlentry.labels.barriers import run_barriers
from app.mlentry.serving.tracking import summarize, track_paths

CFG = LabelConfig()


def _mats(paths: dict[str, list[tuple[float, float, float, float]]], n_days: int) -> dict[str, pd.DataFrame]:
    """paths：stock → [(open, high, low, close), ...] 從第 0 列（signal_date）起；不足列補 NaN。"""
    idx = pd.Index([f"2026-09-{d:02d}" for d in range(1, n_days + 1)], name="date")
    out = {}
    for j, col in enumerate(("open", "high", "low", "close")):
        data = {}
        for sid, rows in paths.items():
            vals = [r[j] for r in rows] + [np.nan] * (n_days - len(rows))
            data[sid] = vals
        out[col] = pd.DataFrame(data, index=idx, dtype=float)
    return out


FLAT = (100.0, 101.0, 99.0, 100.0)


def test_pending_entry_on_last_day():
    m = _mats({"A": [FLAT]}, 1)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    assert r["status"] == "PENDING_ENTRY" and r["day_index"] == 0 and r["ret_now"] is None


def test_live_path_reports_ret_mfe_mae():
    # 第 0 列 signal；第 1 列開 100 進場，高 104 低 98 收 103；第 2 列高 105 低 101 收 102
    m = _mats({"A": [FLAT, (100, 104, 98, 103), (102, 105, 101, 102)]}, 3)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    assert r["status"] == "LIVE" and r["day_index"] == 2 and r["hit_day"] is None
    assert r["ret_now"] == pytest.approx(0.02, abs=1e-6)
    assert r["mfe"] == pytest.approx(0.05, abs=1e-6)
    assert r["mae"] == pytest.approx(-0.02, abs=1e-6)


def test_target_hit_day():
    m = _mats({"A": [FLAT, (100, 103, 99, 102), (102, 111, 101, 110)]}, 3)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    assert r["status"] == "TARGET" and r["hit_day"] == 2


def test_stop_hit_day():
    m = _mats({"A": [FLAT, (100, 101, 94.9, 95)]}, 2)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    assert r["status"] == "STOP" and r["hit_day"] == 1


def test_same_day_double_touch_is_stop_ambiguous():
    m = _mats({"A": [FLAT, (100, 110.5, 94.0, 100)]}, 2)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    assert r["status"] == "STOP_AMBIGUOUS" and r["hit_day"] == 1


def test_timeout_after_ten_days():
    rows = [FLAT] + [(100, 102, 98, 100)] * 10
    m = _mats({"A": rows}, 11)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    assert r["status"] == "TIMEOUT" and r["day_index"] == 10


def test_limit_up_open_is_not_entered():
    # 前收 100 → 漲停 110；隔日開在 110 → PRICE_LIMIT_CONSTRAINT
    m = _mats({"A": [FLAT, (110, 110, 110, 110)]}, 2)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    assert r["status"] == "NOT_ENTERED" and r["ret_now"] is None


def test_missing_stock_is_data_missing():
    m = _mats({"A": [FLAT, FLAT]}, 2)
    (r,) = track_paths(m, [("2026-09-01", "ZZZZ")], CFG)
    assert r["status"] == "DATA_MISSING"


def test_ledger_event_overrides():
    m = _mats({"A": [FLAT, (100, 101, 99, 100)]}, 2)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG, ledger={("2026-09-01", "A"): 4})
    assert r["status"] == "TIMEOUT"


def test_parity_with_matured_engine():
    """完整 10 日路徑：track_paths 的 status／hit_day／mfe 必須與 run_barriers 直接跑的成熟結果一致。"""
    rng = np.random.default_rng(1)
    rows = [FLAT]
    px = 100.0
    for _ in range(10):
        o = px * (1 + rng.normal(0, 0.01)); c = o * (1 + rng.normal(0, 0.03))
        rows.append((o, max(o, c) * 1.01, min(o, c) * 0.99, c)); px = c
    rows.append(FLAT)                                      # 第 11 列讓第 0 列在原生引擎中成熟
    m = _mats({"A": rows}, 12)
    direct = run_barriers(m, CFG)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    ev = int(direct["event_type"].iloc[0, 0])
    expect = {1: "TARGET", 2: "STOP", 3: "STOP_AMBIGUOUS", 4: "TIMEOUT"}[ev]
    assert r["status"] == expect
    assert r["mfe"] == pytest.approx(float(direct["mfe_10d"].iloc[0, 0]), abs=1e-6)
    assert r["mae"] == pytest.approx(float(direct["mae_10d"].iloc[0, 0]), abs=1e-6)


def test_summarize_counts():
    rows = [{"status": s} for s in ("TARGET", "STOP", "STOP_AMBIGUOUS", "LIVE", "LIVE", "PENDING_ENTRY", "TIMEOUT", "NOT_ENTERED")]
    assert summarize(rows) == {"n": 8, "target": 1, "stop": 2, "timeout": 1, "live": 2, "pending": 1}
