"""論點狀態機：三出口優先序、到期重審、倒數預告。"""
from app.engines.thesis_engine import evaluate_thesis

T = {"target_pct": 10.0, "horizon_days": 10, "stop_pct": 8.0,
     "source": "manual", "clock_start": "2026-08-20", "reaudit_count": 0, "state": "active"}


def ev(**kw):
    args = dict(avg_cost=100.0, hi_since_clock=100.0, lo_today=100.0, days_elapsed=1)
    args.update(kw)
    return evaluate_thesis({**T, **kw.pop("thesis", {})} if "thesis" in kw else dict(T), **args)


def test_active_green():
    r = ev(days_elapsed=3)
    assert (r.state, r.level) == ("active", "green")
    assert r.target_price == 110.0 and r.stop_price == 92.0
    assert r.days_left == 7


def test_stop_beats_target_same_day():
    r = ev(hi_since_clock=111.0, lo_today=91.0)
    assert (r.state, r.level) == ("refuted", "red")


def test_fulfilled_on_target_touch():
    r = ev(hi_since_clock=110.0)
    assert (r.state, r.level) == ("fulfilled", "red")


def test_expiring_yellow_last_two_days():
    assert ev(days_elapsed=9).state == "expiring"
    assert ev(days_elapsed=9).level == "yellow"
    assert ev(days_elapsed=8).state == "expiring"
    assert ev(days_elapsed=7).state == "active"


def test_expiry_awaits_reaudit_then_hard_expires():
    r = ev(days_elapsed=10)
    assert (r.state, r.level) == ("awaiting_reaudit", "orange")
    r2 = evaluate_thesis({**T, "reaudit_count": 2}, avg_cost=100.0,
                         hi_since_clock=100.0, lo_today=100.0, days_elapsed=10)
    assert (r2.state, r2.level) == ("expired", "red")


def test_terminal_state_passthrough():
    r = evaluate_thesis({**T, "state": "fulfilled"}, avg_cost=100.0,
                        hi_since_clock=120.0, lo_today=80.0, days_elapsed=12)
    assert (r.state, r.level) == ("fulfilled", "red")


# ── 2026-08-24：波段軌預設無停損（stop_pct=None）──


NO_STOP = {"target_pct": 10.0, "horizon_days": 10, "stop_pct": None,
           "reaudit_count": 0, "state": "active"}


def test_no_stop_thesis_ignores_deep_drawdown():
    """沒有停損出口時，跌多深都不算反證——風控是時間不是價格。"""
    r = evaluate_thesis(NO_STOP, avg_cost=100, hi_since_clock=101, lo_today=55,
                        days_elapsed=3)
    assert r.state == "active" and r.level == "green" and r.stop_price is None


def test_no_stop_thesis_still_fulfills_and_expires():
    hit = evaluate_thesis(NO_STOP, avg_cost=100, hi_since_clock=110, lo_today=55,
                          days_elapsed=3)
    assert hit.state == "fulfilled"
    old = evaluate_thesis(NO_STOP, avg_cost=100, hi_since_clock=101, lo_today=55,
                          days_elapsed=10)
    assert old.state == "awaiting_reaudit"


def test_explicit_strategy_stop_still_applies():
    """使用者在實驗室策略上明示的 stop_pct 仍然有效（明示優先於軌別預設）。"""
    r = evaluate_thesis({**NO_STOP, "stop_pct": 6.0}, avg_cost=100, hi_since_clock=101,
                        lo_today=93, days_elapsed=3)
    assert r.state == "refuted" and r.stop_price == 94.0
