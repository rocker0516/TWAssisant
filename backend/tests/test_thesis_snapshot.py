"""建倉論點快照：strategy 來源凍結參數；manual 用預設；long 軌無 thesis。"""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services.holding_service import HoldingService
from app.storage import models

D = date(2026, 8, 20)


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add_all([
        models.User(id=1, email="u@x", password_hash="h"),
        models.Stock(id="1101", name="甲", is_etf=False),
        models.UserStrategy(id=3, user_id=1, name="S", conditions=[{"field": "close", "op": "gt", "value": 100}],
                            target_pct=12.0, horizon_days=7, stop_pct=6.0),
    ])
    s.commit()
    yield s
    s.close()


def test_strategy_source_freezes_params(session):
    h = HoldingService().create(session, user_id=1, stock_id="1101", track="wave",
                                date_=D, price=100, shares=1, strategy_id=3)
    t = h.thesis
    assert t["source"] == "strategy" and t["strategy_id"] == 3
    assert (t["target_pct"], t["horizon_days"], t["stop_pct"]) == (12.0, 7, 6.0)
    assert t["conditions"] == [{"field": "close", "op": "gt", "value": 100}]
    assert t["clock_start"] == D.isoformat()
    assert t["reaudit_count"] == 0 and t["state"] == "active"


def test_manual_source_uses_defaults(session):
    h = HoldingService().create(session, user_id=1, stock_id="1101", track="wave",
                                date_=D, price=100, shares=1)
    t = h.thesis
    assert t["source"] == "manual"
    assert (t["target_pct"], t["horizon_days"], t["stop_pct"]) == (10.0, 10, 8.0)


def test_long_track_has_no_thesis(session):
    h = HoldingService().create(session, user_id=1, stock_id="1101", track="long",
                                date_=D, price=100, shares=1)
    assert h.thesis is None
