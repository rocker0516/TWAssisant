"""持股 API 帶出論點欄位。走 build_item 層級（不起 HTTP）。

注：brief 原稿假設 exit_status 為巢狀物件（item.exit_status.thesis_state），但實際
HoldingItem／ExitStatus 是扁平欄位（light/level/hard_stop/... 直接掛在 HoldingItem
上），此測試改為對齊現有慣例，直接斷言 item.thesis_state 等扁平欄位。
"""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes_holdings import build_item
from app.storage import models

D = date(2026, 8, 20)


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add_all([models.User(id=1, email="u@x", password_hash="h"),
               models.Stock(id="1101", name="甲", is_etf=False),
               models.MarketIndex(date=D, close=20000),
               models.DailyPrice(stock_id="1101", date=D, open=100, high=102,
                                 low=99, close=100, volume=1000)])
    s.commit()
    yield s
    s.close()


def test_build_item_exposes_thesis_fields(session):
    h = models.Holding(user_id=1, stock_id="1101", track="wave", status="open",
                       opened_date=D,
                       thesis={"target_pct": 10.0, "horizon_days": 10, "stop_pct": 8.0,
                               "source": "manual", "clock_start": D.isoformat(),
                               "reaudit_count": 0, "state": "active"})
    session.add(h)
    session.flush()
    session.add(models.Transaction(user_id=1, holding_id=h.id, type="buy",
                                   date=D, price=100, shares=1))
    session.commit()
    item = build_item(session, h, D)
    assert item.thesis_state == "active"
    assert item.target_price == 110.0 and item.stop_price == 92.0
    assert item.days_left == 9
    assert item.horizon_days == 10


def test_build_item_long_track_thesis_fields_are_none(session):
    """long 軌無 thesis → 走 evaluate() fallback 分支，五＋一欄留 None。"""
    h = models.Holding(user_id=1, stock_id="1101", track="long", status="open",
                       opened_date=D, thesis=None)
    session.add(h)
    session.flush()
    session.add(models.Transaction(user_id=1, holding_id=h.id, type="buy",
                                   date=D, price=100, shares=1))
    session.commit()
    item = build_item(session, h, D)
    assert item.thesis_state is None
    assert item.days_left is None
    assert item.reaudit_count is None
    assert item.target_price is None
    assert item.stop_price is None
    assert item.horizon_days is None
