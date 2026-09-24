"""K 線推薦標記：段落合併與狀態判定（純函式，免 DB）。"""

from __future__ import annotations

from datetime import date, timedelta

from app.api.routes import _MARK_HORIZON, _mark_segments, _mark_status


def _days(n: int) -> list[date]:
    """n 個連續交易日（週末略過不影響邏輯，直接用連續日）。"""
    base = date(2026, 1, 5)
    return [base + timedelta(days=i) for i in range(n)]


def test_consecutive_days_merge_into_one_segment():
    td = _days(20)
    assert _mark_segments([td[3], td[4], td[5]], td) == [td[3]]


def test_short_gap_stays_same_segment():
    td = _days(20)
    # 中斷 4 個交易日（<5）仍算同段
    assert _mark_segments([td[3], td[8]], td) == [td[3]]


def test_long_gap_starts_new_segment():
    td = _days(20)
    # 中斷 5 個交易日（≥5）→ 新段落
    assert _mark_segments([td[3], td[9]], td) == [td[3], td[9]]


def test_dates_missing_from_calendar_are_skipped():
    td = _days(20)
    stray = date(2030, 1, 1)
    assert _mark_segments([td[3], stray], td) == [td[3]]


# 下面三個一律以 _MARK_HORIZON 相對取值，不寫死天數：2026-08 目標由 30 日改 10 日時，
# 寫死 30 日那版的斷言就整組過期了（_mark_status(True, 12, 40) 從 hit 變 miss）。
H = _MARK_HORIZON


def test_status_hit_within_horizon():
    assert _mark_status(True, H - 2, H * 4) == "hit"


def test_status_hit_on_last_day_of_horizon():
    assert _mark_status(True, H, H * 4) == "hit"


def test_status_hit_after_horizon_counts_as_miss():
    assert _mark_status(True, H + 1, H * 4) == "miss"


def test_status_miss_when_window_elapsed():
    assert _mark_status(False, None, H) == "miss"


def test_status_pending_before_window_elapsed():
    assert _mark_status(False, None, H - 1) == "pending"


def test_status_pending_when_window_open():
    assert _mark_status(False, None, 5) == "pending"
