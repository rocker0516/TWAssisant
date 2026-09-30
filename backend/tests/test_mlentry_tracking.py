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


_EV_STATUS = {1: "TARGET", 2: "STOP", 3: "STOP_AMBIGUOUS", 4: "TIMEOUT"}


def _random_path(seed: int, vol: float = 0.03) -> list[tuple[float, float, float, float]]:
    rng = np.random.default_rng(seed)
    rows = [FLAT]
    px = 100.0
    for _ in range(10):
        o = px * (1 + rng.normal(0, 0.01)); c = o * (1 + rng.normal(0, vol))
        rows.append((o, max(o, c) * 1.01, min(o, c) * 0.99, c)); px = c
    rows.append(FLAT)                                      # 第 11 列讓第 0 列在原生引擎中成熟
    return rows


def _direct_expect(rows, d: int) -> dict:
    """只保留第 0..d 列的真實資料（其後 NaN、但列數足夠讓第 0 列成熟），直接跑 run_barriers。"""
    cut = [r if i <= d else (np.nan,) * 4 for i, r in enumerate(rows)]
    direct = run_barriers(_mats({"A": cut}, len(rows)), CFG)
    g = lambda k: direct[k].iloc[0, 0]
    ev = int(g("event_type"))
    status = _EV_STATUS[ev]
    if status == "TIMEOUT" and d < CFG.max_horizon:
        status = "LIVE"
    hit = {"TARGET": g("target_first_hit_day"), "STOP_AMBIGUOUS": g("target_first_hit_day"),
           "STOP": g("stop_first_hit_day")}.get(status)
    return {"status": status, "hit_day": None if hit is None else int(hit),
            "ret": float(g("return_10d")), "mfe": float(g("mfe_10d")), "mae": float(g("mae_10d"))}


def _assert_parity(rows, d: int):
    m = _mats({"A": rows[: d + 1]}, d + 1)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    e = _direct_expect(rows, d)
    assert r["day_index"] == d
    assert r["status"] == e["status"]
    assert r["hit_day"] == e["hit_day"]
    assert r["ret_now"] == pytest.approx(e["ret"], abs=1e-6)
    assert r["mfe"] == pytest.approx(e["mfe"], abs=1e-6)
    assert r["mae"] == pytest.approx(e["mae"], abs=1e-6)
    return e


def test_parity_with_matured_engine():
    """完整 10 日路徑：status／hit_day／ret_now／mfe／mae 與 run_barriers 直接成熟結果一致。"""
    seen = set()
    for seed in range(12):
        e = _assert_parity(_random_path(seed), 10)
        seen.add(e["status"])
    assert seen & {"TARGET", "STOP", "STOP_AMBIGUOUS"} and "TIMEOUT" in seen   # 兩類結局皆被驗到


def test_parity_truncated_prefixes_exercise_padding():
    """同一條路徑截到第 d 日（d=1..9）：補 NaN 後的結果 = 該日之後全 NaN 的直接引擎結果；
    已含命中的前綴須回報與完整路徑相同的 hit_day 與 status。"""
    hits = 0
    for seed in range(20):
        rows = _random_path(seed, vol=0.04)
        full = _direct_expect(rows, 10)
        for d in range(1, 10):
            e = _assert_parity(rows, d)
            if full["hit_day"] is not None and d >= full["hit_day"]:
                assert e["status"] == full["status"] and e["hit_day"] == full["hit_day"]
                hits += 1
            elif full["hit_day"] is None:
                assert e["status"] == "LIVE" and e["hit_day"] is None
    assert hits > 0


def test_ledger_override_keeps_hit_day_consistent():
    m = _mats({"A": [FLAT, (100, 103, 99, 102), (102, 111, 101, 110)]}, 3)   # 引擎：TARGET@2
    key = ("2026-09-01", "A")
    (t,) = track_paths(m, [key], CFG)
    assert t["status"] == "TARGET" and t["hit_day"] == 2
    (r,) = track_paths(m, [key], CFG, ledger={key: 4})                     # ledger：TIMEOUT
    assert r["status"] == "TIMEOUT" and r["hit_day"] is None
    assert r["ret_now"] == t["ret_now"] and r["mfe"] == t["mfe"] and r["mae"] == t["mae"]
    (r,) = track_paths(m, [key], CFG, ledger={key: 2})                     # ledger：STOP → 用引擎 stop 日（無 → None）
    assert r["status"] == "STOP" and r["hit_day"] is None
    (r,) = track_paths(m, [key], CFG, ledger={key: 3})                     # ledger：STOP_AMBIGUOUS → target 日
    assert r["status"] == "STOP_AMBIGUOUS" and r["hit_day"] == 2
    (r,) = track_paths(m, [key], CFG, ledger={key: 0})
    assert r["status"] == "NOT_ENTERED" and r["hit_day"] is None


def test_summarize_counts():
    rows = [{"status": s} for s in ("TARGET", "STOP", "STOP_AMBIGUOUS", "LIVE", "LIVE", "PENDING_ENTRY", "TIMEOUT", "NOT_ENTERED")]
    assert summarize(rows) == {"n": 8, "target": 1, "stop": 2, "timeout": 1, "live": 2, "pending": 1}


def test_load_tracking_shape_on_real_db():
    import sqlite3
    from app.config import get_settings
    from app.mlentry.serving.tracking import load_tracking
    from app.storage.database import init_db, session_scope

    init_db()
    con = sqlite3.connect(str(get_settings().db_path))
    try:
        with session_scope() as s:
            res = load_tracking(con, s, days=10)
    finally:
        con.close()
    assert set(res) == {"as_of", "summary", "items"}
    assert set(res["summary"]) == {"n", "target", "stop", "timeout", "live", "pending"}
    assert res["summary"]["n"] == len(res["items"])
    dates = [it["signal_date"] for it in res["items"]]
    assert dates == sorted(dates, reverse=True)
    for it in res["items"]:
        assert {"signal_date", "stock_id", "name", "day_index", "status", "ret_now", "mfe", "mae", "hit_day"} <= set(it)
