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


def _seed_prices(s, sid: str, base: date, closes, highs, lows):
    """從 base 起連續交易日種價格（同步種 MarketIndex 當交易日曆）。"""
    from datetime import timedelta
    d = base
    i = 0
    while i < len(closes):
        if d.weekday() < 5:  # 平日當交易日
            s.merge(models.DailyPrice(stock_id=sid, date=d, close=closes[i],
                                      high=highs[i], low=lows[i], open=closes[i],
                                      volume=1000, turnover=1e8 * closes[i]))
            s.merge(models.MarketIndex(date=d, close=20000))
            i += 1
        d += timedelta(days=1)


def test_backtest_hit_stop_and_miss(session):
    from app.services.strategy_engine import run_backtest
    base = date(2026, 2, 2)  # 週一
    # 訊號日 close=200 觸發條件；隔日 high=210 是進場錨。
    # hitcase：第 4 根 high 231 ≥ 210*1.10=231 → 命中
    _seed_prices(session, "1101", base,
                 closes=[200, 205, 210, 220, 231, 230],
                 highs=[201, 210, 215, 225, 231, 232],
                 lows=[199, 204, 209, 219, 224, 229])
    # stopcase：進場錨 110；同一日 high 121(=110*1.10) 且 low 99(=110*0.9)
    # → 同日皆碰，保守記失敗（stop 優先）
    _seed_prices(session, "2202", base,
                 closes=[100, 105, 110, 100, 100, 100],
                 highs=[101, 110, 121, 101, 101, 101],
                 lows=[99, 104, 99, 95, 95, 95])
    session.commit()

    r = run_backtest(
        session,
        conditions=[{"field": "close", "op": "gte", "value": 100}],
        sort_field="turnover", sort_desc=True, top_n=10,
        target_pct=10.0, horizon_days=5, stop_pct=10.0,
        start=base, end=base,  # 只有一個訊號日
    )
    assert r.samples == 2
    assert r.hits == 1              # 1101 命中；2202 同日雙碰記失敗
    assert r.hit_rate == 0.5
    assert r.signal_days == 1
