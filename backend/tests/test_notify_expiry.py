from app.engines.exit_engine import ExitStatus
from app.scheduler.steps import format_exit_lines


def _st(**kw):
    base = dict(level="green", light="🟢", signals=[])
    base.update(kw)
    return ExitStatus(**base)


def test_expiring_tomorrow_gets_notice_line():
    rows = [("甲", _st(level="yellow", light="🟡", thesis_state="expiring", days_left=1))]
    lines = format_exit_lines(rows)
    assert any("明日到期" in ln for ln in lines)


def test_expiring_two_days_left_no_notice():
    rows = [("甲", _st(level="yellow", light="🟡", thesis_state="expiring", days_left=2))]
    assert not any("明日到期" in ln for ln in format_exit_lines(rows))


def test_expiring_tomorrow_notice_shows_correct_day_fraction():
    rows = [("甲", _st(level="yellow", light="🟡", thesis_state="expiring",
                       days_left=1, horizon_days=10))]
    lines = format_exit_lines(rows)
    assert any("第 9/10 天" in ln for ln in lines)
