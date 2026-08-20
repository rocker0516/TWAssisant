"""ExitEngine 波段分流：論點路徑、日更狀態轉移、重審、舊持股補快照。"""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.engines.exit_engine import ExitEngine
from app.storage import models

D0 = date(2026, 8, 3)  # 週一


def _mk_days(n):
    """n 個連續平日（近似交易日）。"""
    out, d = [], D0
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    days = _mk_days(12)
    s.add_all([models.User(id=1, email="u@x", password_hash="h"),
               models.Stock(id="1101", name="甲", is_etf=False)])
    for d in days:  # 12 日橫盤：不觸目標不觸停損
        s.add(models.MarketIndex(date=d, close=20000))
        s.add(models.DailyPrice(stock_id="1101", date=d, open=100, high=102,
                                low=99, close=100, volume=1000))
    s.commit()
    s.days = days
    yield s
    s.close()


def _holding(s, thesis, opened):
    h = models.Holding(user_id=1, stock_id="1101", track="wave", status="open",
                       opened_date=opened, thesis=thesis)
    s.add(h)
    s.flush()
    s.add(models.Transaction(user_id=1, holding_id=h.id, type="buy",
                             date=opened, price=100, shares=1))
    s.flush()
    return h


BASE = {"target_pct": 10.0, "horizon_days": 10, "stop_pct": 8.0,
        "source": "manual", "reaudit_count": 0, "state": "active"}


def test_evaluate_wave_uses_thesis_not_signals(session):
    h = _holding(session, {**BASE, "clock_start": session.days[0].isoformat()}, session.days[0])
    st = ExitEngine().evaluate(session, h, session.days[2], avg_cost=100.0, close=100.0)
    assert st.thesis_state == "active" and st.level == "green"
    assert st.target_price == 110.0 and st.stop_price == 92.0
    assert st.days_left == 10 - 3


def test_run_reaudit_manual_fails_without_score(session):
    # 第 10 個交易日到期；無 wave Score → 重審不過 → expired
    h = _holding(session, {**BASE, "clock_start": session.days[0].isoformat()}, session.days[0])
    ExitEngine().run(session, session.days[9])
    assert h.thesis["state"] == "expired"


def test_run_reaudit_manual_passes_with_score_resets_clock(session):
    session.add(models.Score(stock_id="1101", date=session.days[9], track="wave",
                             passed_filter=True, passed=True))
    session.commit()
    h = _holding(session, {**BASE, "clock_start": session.days[0].isoformat()}, session.days[0])
    ExitEngine().run(session, session.days[9])
    assert h.thesis["state"] == "active"
    assert h.thesis["reaudit_count"] == 1
    assert h.thesis["clock_start"] == session.days[9].isoformat()


def test_run_backfills_thesis_for_legacy_wave_holding(session):
    h = _holding(session, None, session.days[0])
    ExitEngine().run(session, session.days[3])
    assert h.thesis is not None
    assert h.thesis["clock_start"] == session.days[3].isoformat()  # 時鐘自遷移日起算


def test_run_persists_expired_state_when_reaudits_exhausted(session):
    # reaudit_count 已達上限（預設 reaudit_max=2）且到期 → evaluate_thesis 直接回傳 expired，
    # run() 必須把這個終態寫回 DB，否則持股會停在 active 之後可能誤判成 refuted/fulfilled。
    h = _holding(session, {**BASE, "clock_start": session.days[0].isoformat(),
                           "reaudit_count": 2}, session.days[0])
    ExitEngine().run(session, session.days[9])
    assert h.thesis["state"] == "expired"
    assert h.thesis.get("settled_date") == session.days[9].isoformat()

    # 終態鎖定：再跑一天 run()，expired 不得被之後的價格變動改成 fulfilled/refuted。
    ExitEngine().run(session, session.days[10])
    assert h.thesis["state"] == "expired"
