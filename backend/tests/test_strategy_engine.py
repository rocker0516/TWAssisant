"""backend/tests/test_strategy_engine.py — 求值器：op 語意與 null 處理。"""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services import strategy_engine as se
from app.storage import models

D1, D2 = date(2026, 1, 5), date(2026, 1, 6)


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    # 兩檔股票兩天：A 收盤 105/110、B 收盤 95/None
    s.add_all([
        models.Stock(id="1101", name="甲", is_etf=False),
        models.Stock(id="2202", name="乙", is_etf=False),
        models.DailyPrice(stock_id="1101", date=D1, close=105, high=106, low=104,
                          open=105, volume=1000, turnover=5e8),
        models.DailyPrice(stock_id="1101", date=D2, close=110, high=111, low=109,
                          open=110, volume=1000, turnover=6e8),
        models.DailyPrice(stock_id="2202", date=D1, close=95, high=96, low=94,
                          open=95, volume=1000, turnover=1e8),
        models.DailyPrice(stock_id="2202", date=D2, close=None, high=None, low=None,
                          open=None, volume=None, turnover=None),
    ])
    s.commit()
    yield s
    s.close()


def test_gt_and_null_excluded(session):
    out = se.evaluate(session, [{"field": "close", "op": "gt", "value": 100}], [D1, D2])
    assert out[D1] == ["1101"]
    assert out[D2] == ["1101"]  # B 的 None 不符合任何條件


def test_and_semantics(session):
    conds = [{"field": "close", "op": "gt", "value": 100},
             {"field": "turnover", "op": "gte", "value": 6e8}]
    out = se.evaluate(session, conds, [D1, D2])
    assert out[D1] == [] and out[D2] == ["1101"]


def test_streak_op(session):
    # 連 2 日 close > 100：D2 的 A 成立（105,110），D1 不成立（只有一天）
    conds = [{"field": "close", "op": "streak_gt", "value": {"n": 2, "threshold": 100}}]
    out = se.evaluate(session, conds, [D1, D2])
    assert out[D1] == [] and out[D2] == ["1101"]


def test_unknown_field_rejected(session):
    with pytest.raises(ValueError):
        se.evaluate(session, [{"field": "nope", "op": "gt", "value": 1}], [D1])
