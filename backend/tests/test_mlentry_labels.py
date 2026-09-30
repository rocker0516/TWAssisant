"""mlentry label 層：barrier first-hit 順序、同日雙觸、漲停無法進場、trading-day indexing、成熟／截斷。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.mlentry.config import LabelConfig
from app.mlentry.data.calendar import TradingCalendar
from app.mlentry.labels import canonical_outcome as co
from app.mlentry.labels.barriers import EntryStatus, Event, run_barriers

CFG = LabelConfig()


def _path(entry: float, days: list[tuple[float, float, float]], n_before: int = 1, n_after: int = 0):
    """建單股矩陣：signal 日在位置 n_before−1，t+1 開 entry，之後每日 (high, low, close)。

    days[0] 是進場日（k=1）的 (high, low, close)。列數 = n_before + len(days) + n_after。
    """
    n = n_before + len(days) + n_after
    idx = pd.Index([f"d{i:03d}" for i in range(n)], name="date")
    cols = pd.Index(["S"], name="stock_id")
    o = pd.DataFrame(np.nan, index=idx, columns=cols); h = o.copy(); l = o.copy(); c = o.copy()
    for i in range(n_before):
        o.iloc[i] = h.iloc[i] = l.iloc[i] = c.iloc[i] = 100.0
    for j, (hh, ll, cc) in enumerate(days):
        i = n_before + j
        o.iloc[i] = entry if j == 0 else cc
        h.iloc[i], l.iloc[i], c.iloc[i] = hh, ll, cc
    for i in range(n_before + len(days), n):
        o.iloc[i] = h.iloc[i] = l.iloc[i] = c.iloc[i] = 100.0
    v = c.notna().astype(float) * 1000
    return {"open": o, "high": h, "low": l, "close": c, "volume": v, "turnover": v * 100}


def _flat(k: int, px: float = 100.0):
    return [(px, px, px)] * k


def _row(mats, name, i=0):
    return mats[name].iloc[i, 0]


def test_target_before_stop_and_horizon_split():
    # k=3 碰 +10%，k=7 碰 −5%
    days = _flat(2) + [(110.0, 99.0, 105.0)] + _flat(3, 105.0) + [(105.0, 94.0, 96.0)] + _flat(3, 96.0)
    out = co.build_outcome_matrices(_path(100.0, days), CFG)
    assert _row(out, "event_type") == Event.TARGET
    assert _row(out, "target_first_hit_day") == 3 and _row(out, "stop_first_hit_day") == 7
    assert _row(out, "target_hit_3d") == 1 and _row(out, "target_hit_5d") == 1 and _row(out, "target_hit_10d") == 1
    assert _row(out, "stop_hit_5d") == 0 and _row(out, "stop_hit_10d") == 0   # 先後優先於是否碰到
    assert _row(out, "mfe_3d") == pytest.approx(0.10) and _row(out, "mae_3d") == pytest.approx(-0.01)
    assert _row(out, "mae_10d") == pytest.approx(-0.06)
    assert _row(out, "mae_before_target") == pytest.approx(-0.01)
    assert _row(out, "return_10d") == pytest.approx(-0.04)


def test_stop_before_target_kills_later_target():
    days = _flat(1) + [(101.0, 94.0, 95.0)] + _flat(2, 95.0) + [(112.0, 95.0, 110.0)] + _flat(5, 110.0)
    out = co.build_outcome_matrices(_path(100.0, days), CFG)
    assert _row(out, "event_type") == Event.STOP
    assert _row(out, "target_hit_10d") == 0 and _row(out, "stop_hit_3d") == 1
    assert np.isnan(_row(out, "mae_before_target"))


def test_same_day_double_touch_is_stop_ambiguous():
    days = _flat(1) + [(111.0, 94.0, 100.0)] + _flat(8)
    out = co.build_outcome_matrices(_path(100.0, days), CFG)
    assert _row(out, "event_type") == Event.STOP_AMBIGUOUS
    assert _row(out, "target_hit_10d") == 0 and _row(out, "stop_hit_3d") == 1
    assert _row(out, "target_first_hit_day") == 2 == _row(out, "stop_first_hit_day")


def test_entry_day_itself_counts_as_k1():
    days = [(110.0, 100.0, 108.0)] + _flat(9, 108.0)
    out = co.build_outcome_matrices(_path(100.0, days), CFG)
    assert _row(out, "event_type") == Event.TARGET and _row(out, "target_first_hit_day") == 1


def test_timeout_when_no_barrier_in_10_days():
    out = co.build_outcome_matrices(_path(100.0, _flat(10, 103.0) ), CFG)
    assert _row(out, "event_type") == Event.TIMEOUT
    assert _row(out, "target_hit_10d") == 0 and _row(out, "stop_hit_10d") == 0
    assert _row(out, "return_5d") == pytest.approx(0.03)


def test_limit_up_open_is_not_entered_and_outcome_nan():
    # 前收 100 → 漲停 110；t+1 開 110 → PRICE_LIMIT_CONSTRAINT
    days = [(115.0, 110.0, 112.0)] + _flat(9, 112.0)
    out = co.build_outcome_matrices(_path(110.0, days), CFG)
    assert _row(out, "entry_status") == EntryStatus.PRICE_LIMIT_CONSTRAINT
    assert _row(out, "entry_executable") == 0 and _row(out, "event_type") == Event.NOT_ENTERED
    assert np.isnan(_row(out, "benchmark_entry_price")) and np.isnan(_row(out, "target_hit_10d"))
    assert np.isnan(_row(out, "return_1d"))
    # 109.5 開盤（低於漲停 tick）→ FILLED
    out2 = co.build_outcome_matrices(_path(109.5, days), CFG)
    assert _row(out2, "entry_status") == EntryStatus.FILLED


def test_no_market_data_on_entry_day():
    m = _path(100.0, _flat(10))
    for k in ("open", "high", "low", "close"):
        m[k].iloc[1, 0] = np.nan
    out = co.build_outcome_matrices(m, CFG)
    assert _row(out, "entry_status") == EntryStatus.NO_MARKET_DATA
    assert _row(out, "event_type") == Event.NOT_ENTERED


def test_missing_mid_path_day_is_skipped_not_hit():
    days = _flat(3) + [(np.nan, np.nan, np.nan)] + _flat(6)
    m = _path(100.0, days)
    out = co.build_outcome_matrices(m, CFG)
    assert _row(out, "event_type") == Event.TIMEOUT and _row(out, "path_days_available") == 9
    assert _row(out, "path_truncated") == 0            # 第 10 日有列 → 非截斷


def test_delisting_truncates_path_and_carries_last_close():
    days = _flat(2) + [(101.0, 99.0, 97.0)]              # 之後下市：後面 7 天無列
    m = _path(100.0, days, n_after=7)
    for k in ("open", "high", "low", "close", "volume", "turnover"):
        m[k].iloc[4:, 0] = np.nan
    out = co.build_outcome_matrices(m, CFG)
    assert _row(out, "event_type") == Event.TIMEOUT
    assert _row(out, "path_truncated") == 1 and _row(out, "path_days_available") == 3
    assert _row(out, "return_10d") == pytest.approx(-0.03)


def test_pending_when_not_matured_by_calendar():
    days = _flat(4)
    out = co.build_outcome_matrices(_path(100.0, days), CFG)      # 只有 4 個路徑日在 calendar 上
    assert _row(out, "event_type") == Event.PENDING and _row(out, "matured") == 0
    assert np.isnan(_row(out, "target_hit_3d")) and np.isnan(_row(out, "mfe_3d"))


def test_long_table_uses_calendar_positions_for_dates():
    m = _path(100.0, _flat(10), n_before=3)
    cal = TradingCalendar(m["close"].index)
    mats = co.build_outcome_matrices(m, CFG)
    mask = pd.DataFrame(True, index=m["close"].index, columns=m["close"].columns)
    df = co.to_long(mats, mask, cal, CFG)
    r = df.set_index("signal_date").loc["d002"]
    assert r["entry_date"] == "d003" and r["label_available_date"] == "d012"
    last = df.set_index("signal_date").loc["d012"]
    assert last["entry_date"] is None and last["label_available_date"] is None
    assert set(co.OUTCOME_COLS_ORDER) <= set(df.columns) and "target_hit_5d" in df.columns


def test_label_version_changes_with_barrier_config():
    assert co.label_version(LabelConfig()) != co.label_version(LabelConfig(stop_pct=0.07))
