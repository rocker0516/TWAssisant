"""backend/tests/test_user_strategies.py — ownership 與 is_active 單一性。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.storage import models
from app.storage.user_data import UserData


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    for i, email in enumerate(["a@t.twa", "b@t.twa"], start=1):
        s.add(models.User(id=i, email=email, password_hash="x"))
    s.commit()
    yield s
    s.close()


def _mk(ud: UserData, name: str = "測試策略") -> models.UserStrategy:
    return ud.create_strategy(
        name=name,
        conditions=[{"field": "close", "op": "gt", "value": 100}],
        sort_field="turnover", sort_desc=True, top_n=30,
        target_pct=10.0, horizon_days=10, stop_pct=None,
    )


def test_ownership_isolation(session):
    ua, ub = UserData(session, 1), UserData(session, 2)
    st = _mk(ua)
    session.commit()
    assert ub.strategy(st.id) is None          # B 讀不到 A 的
    assert ua.strategy(st.id) is not None
    assert ub.delete_strategy(st.id) is False  # B 刪不掉 A 的
    assert ua.strategies() and not ub.strategies()


def test_set_active_exclusive(session):
    ua = UserData(session, 1)
    s1, s2 = _mk(ua, "一"), _mk(ua, "二")
    session.commit()
    ua.set_active_strategy(s1.id)
    ua.set_active_strategy(s2.id)
    session.commit()
    active = ua.active_strategy()
    assert active is not None and active.id == s2.id
    assert ua.strategy(s1.id).is_active is False
