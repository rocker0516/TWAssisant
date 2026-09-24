"""Level 2 live paper pipeline 測試（FRS v1.1 §10：帳戶生日、執行、防禦、重放）。"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.storage.database import Base
from app.storage import models
from scripts import level2_paper as lp


D1, D2, D3, D4 = (date(2026, 9, 14), date(2026, 9, 15),
                  date(2026, 9, 16), date(2026, 9, 17))


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(models.Sector(id=1, name="測試"))
    for sid in ("AAAA", "BBBB", "CCCC"):
        s.add(models.Stock(id=sid, name=f"股{sid}", sector_id=1))
    for d in (D1, D2, D3):
        s.add(models.MarketIndex(date=d, close=20000.0 + d.day))
        for sid, px in (("AAAA", 100.0), ("BBBB", 50.0), ("CCCC", 20.0)):
            s.add(models.DailyPrice(stock_id=sid, date=d, open=px, high=px,
                                    low=px, close=px, volume=1000))
    s.commit()
    yield s
    s.close()


def _ledger(s, d, pct: dict[str, float], horizon: int):
    rank = {sid: i + 1 for i, sid in
            enumerate(sorted(pct, key=pct.get, reverse=True))}
    for sid, p in pct.items():
        s.add(models.Level1Prediction(
            prediction_date=d, stock_id=sid, horizon=horizon,
            model_version=lp.CURRENT_MODEL_VERSION, score=p, rank=rank[sid],
            pct_rank=p, universe_size=len(pct), feature_version="t"))


def test_birthday_processes_only_latest_day(session):
    _ledger(session, D3, {"AAAA": 0.99, "BBBB": 0.9, "CCCC": 0.5}, 5)
    _ledger(session, D3, {"AAAA": 0.9, "BBBB": 0.8, "CCCC": 0.6}, 1)
    out = lp.run_catchup(session)
    session.commit()
    assert len(out) == 1 and out[0]["date"] == str(D3)   # 不回補歷史
    acct = session.scalar(select(models.Level2Account))
    assert acct.start_date == D3 and acct.cash == lp.INITIAL_CASH
    # 再平衡日（counter 0）：三檔皆 rank ≤ k_in → 各買 nav/20
    orders = session.execute(select(models.Level2Order)).scalars().all()
    assert {o.stock_id for o in orders} == {"AAAA", "BBBB", "CCCC"}
    assert all(o.status == "pending" for o in orders)
    a = next(o for o in orders if o.stock_id == "AAAA")
    assert a.qty == int(lp.INITIAL_CASH / 20 / 100.0)
    nav = session.scalar(select(models.Level2Nav))
    assert nav.nav == pytest.approx(lp.INITIAL_CASH) and nav.had_signal


def test_next_day_fills_and_replay_consistency(session):
    _ledger(session, D3, {"AAAA": 0.99, "BBBB": 0.9, "CCCC": 0.5}, 5)
    _ledger(session, D3, {"AAAA": 0.9, "BBBB": 0.8, "CCCC": 0.6}, 1)
    lp.run_catchup(session)
    session.commit()
    # 新交易日 D4：開盤 102，委託應以 102 成交
    session.add(models.MarketIndex(date=D4, close=20100.0))
    for sid, px in (("AAAA", 102.0), ("BBBB", 51.0), ("CCCC", 20.4)):
        session.add(models.DailyPrice(stock_id=sid, date=D4, open=px, high=px,
                                      low=px, close=px, volume=1000))
    _ledger(session, D4, {"AAAA": 0.99, "BBBB": 0.9, "CCCC": 0.5}, 5)
    _ledger(session, D4, {"AAAA": 0.9, "BBBB": 0.8, "CCCC": 0.6}, 1)
    out = lp.run_catchup(session)
    session.commit()
    assert len(out) == 1 and out[0]["filled"] == 3
    filled = session.execute(select(models.Level2Order).where(
        models.Level2Order.status == "filled")).scalars().all()
    assert {f.trade_date for f in filled} == {D4}
    a = next(f for f in filled if f.stock_id == "AAAA")
    assert a.price == 102.0
    # 持倉/NAV/重放一致
    acct = session.scalar(select(models.Level2Account))
    pos = session.execute(select(models.Level2Position).where(
        models.Level2Position.date == D4)).scalars().all()
    nav_row = session.scalar(select(models.Level2Nav).where(
        models.Level2Nav.date == D4))
    assert nav_row.nav == pytest.approx(
        acct.cash + sum(p.qty * p.close for p in pos))
    assert lp.verify_replay(session)
    # D4 非再平衡日（counter=1）且無防禦觸發 → 無新委託
    pending = session.execute(select(models.Level2Order).where(
        models.Level2Order.status == "pending")).scalars().all()
    assert pending == []


def test_defense_fires_between_rebalances(session):
    _ledger(session, D3, {"AAAA": 0.99, "BBBB": 0.9, "CCCC": 0.5}, 5)
    _ledger(session, D3, {"AAAA": 0.9, "BBBB": 0.8, "CCCC": 0.6}, 1)
    lp.run_catchup(session)
    session.commit()
    session.add(models.MarketIndex(date=D4, close=20100.0))
    for sid, px in (("AAAA", 102.0), ("BBBB", 51.0), ("CCCC", 20.4)):
        session.add(models.DailyPrice(stock_id=sid, date=D4, open=px, high=px,
                                      low=px, close=px, volume=1000))
    _ledger(session, D4, {"AAAA": 0.99, "BBBB": 0.9, "CCCC": 0.5}, 5)
    # CCCC 的 1D 掉到最弱分位 → 防禦出場單
    _ledger(session, D4, {"AAAA": 0.9, "BBBB": 0.8, "CCCC": 0.1}, 1)
    lp.run_catchup(session)
    session.commit()
    pending = session.execute(select(models.Level2Order).where(
        models.Level2Order.status == "pending")).scalars().all()
    assert [(o.stock_id, o.side, o.reason) for o in pending] == \
        [("CCCC", "sell", "defense")]


def test_no_ledger_day_no_signal(session):
    _ledger(session, D3, {"AAAA": 0.99, "BBBB": 0.9, "CCCC": 0.5}, 5)
    _ledger(session, D3, {"AAAA": 0.9, "BBBB": 0.8, "CCCC": 0.6}, 1)
    lp.run_catchup(session)
    session.commit()
    session.add(models.MarketIndex(date=D4, close=20100.0))
    for sid, px in (("AAAA", 102.0), ("BBBB", 51.0), ("CCCC", 20.4)):
        session.add(models.DailyPrice(stock_id=sid, date=D4, open=px, high=px,
                                      low=px, close=px, volume=1000))
    out = lp.run_catchup(session)   # D4 無 ledger 列（Level 1 缺席）
    session.commit()
    assert out[0]["had_signal"] is False and out[0]["filled"] == 3
    acct = session.scalar(select(models.Level2Account))
    assert acct.rebalance_counter == 1   # 缺席日不計訊號日
