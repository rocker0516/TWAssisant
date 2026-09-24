"""基本面 PIT 可得性測試：法定期限規則、金融業較晚期限、first_seen 下界、append-only。"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.services import pit_fundamentals as pit
from app.storage import models
from app.storage.database import Base


@pytest.fixture()
def session():
    eng = create_engine("sqlite://", future=True)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)()
    yield s
    s.close()


def _seed_stock(session, sid: str, industry: str | None = None):
    session.merge(models.Stock(id=sid, name=f"測試{sid}", industry_category=industry))
    session.flush()


# ── 法定期限規則 ──

def test_revenue_avail_next_month_10th():
    assert pit.revenue_avail_date(2025, 3) == date(2025, 4, 10)
    assert pit.revenue_avail_date(2025, 12) == date(2026, 1, 10)  # 跨年


@pytest.mark.parametrize("q, expected", [
    (1, date(2025, 5, 15)), (2, date(2025, 8, 14)),
    (3, date(2025, 11, 14)), (4, date(2026, 3, 31)),
])
def test_financials_avail_general(q, expected):
    assert pit.financials_avail_date(2025, q) == expected


@pytest.mark.parametrize("q, expected", [
    (1, date(2025, 5, 30)), (2, date(2025, 8, 31)),
    (3, date(2025, 11, 29)), (4, date(2026, 3, 31)),
])
def test_financials_avail_financial_industry(q, expected):
    assert pit.financials_avail_date(2025, q, financial=True) == expected


# ── 載入器：金融股與一般股用不同期限 ──

def test_load_financials_pit_financial_gets_late_deadline(session):
    _seed_stock(session, "2330", "半導體業")
    _seed_stock(session, "2881", "金融保險")
    for sid in ("2330", "2881"):
        session.add(models.FinancialQuarter(
            stock_id=sid, year=2025, quarter=2, eps=1.0))
    session.flush()

    df = pit.load_financials_pit(session).set_index("stock_id")
    assert df.loc["2330", "avail"] == pd.Timestamp(pit.financials_avail_date(2025, 2))
    assert df.loc["2881", "avail"] == pd.Timestamp(pit.financials_avail_date(2025, 2, financial=True))


def test_load_revenue_pit_avail(session):
    _seed_stock(session, "2330")
    session.add(models.RevenueMonthly(
        stock_id="2330", year=2025, month=7, revenue=100.0, yoy=10.0, mom=1.0))
    session.flush()

    df = pit.load_revenue_pit(session)
    assert df.loc[0, "avail"] == pd.Timestamp("2025-08-10")


# ── first_seen 側表：min(法定期限, 首次入庫日) ──

def test_first_seen_earlier_than_deadline_wins(session):
    """排程當天抓到（早於法定期限）→ 可得日提前到入庫日。"""
    _seed_stock(session, "2330")
    session.add(models.RevenueMonthly(stock_id="2330", year=2025, month=7, yoy=10.0))
    session.add(models.FundamentalFirstSeen(
        kind="rev", stock_id="2330", year=2025, period=7,
        first_seen=date(2025, 8, 5)))
    session.flush()

    df = pit.load_revenue_pit(session)
    assert df.loc[0, "avail"] == pd.Timestamp("2025-08-05")


def test_first_seen_later_than_deadline_ignored(session):
    """歷史一次性回補（first_seen 遠晚於期限）→ min() 退回法定規則。"""
    _seed_stock(session, "2330")
    session.add(models.RevenueMonthly(stock_id="2330", year=2021, month=3, yoy=5.0))
    session.add(models.FundamentalFirstSeen(
        kind="rev", stock_id="2330", year=2021, period=3,
        first_seen=date(2026, 8, 27)))
    session.flush()

    df = pit.load_revenue_pit(session)
    assert df.loc[0, "avail"] == pd.Timestamp("2021-04-10")


# ── record_first_seen：補新不改舊 ──

def test_record_first_seen_append_only(session):
    _seed_stock(session, "2330")
    session.add(models.RevenueMonthly(stock_id="2330", year=2025, month=6, yoy=1.0))
    session.flush()

    out1 = pit.record_first_seen(session, today=date(2025, 7, 10))
    assert out1["new"] == 1

    # 新一期進來，舊紀錄的日期不得被改寫
    session.add(models.RevenueMonthly(stock_id="2330", year=2025, month=7, yoy=2.0))
    session.add(models.FinancialQuarter(stock_id="2330", year=2025, quarter=2, eps=1.0))
    session.flush()
    out2 = pit.record_first_seen(session, today=date(2025, 8, 5))
    assert out2["new"] == 2

    rows = {
        (r.kind, r.period): r.first_seen
        for r in session.execute(select(models.FundamentalFirstSeen)).scalars()
    }
    assert rows[("rev", 6)] == date(2025, 7, 10)   # 未被 8/5 覆寫
    assert rows[("rev", 7)] == date(2025, 8, 5)
    assert rows[("fin", 2)] == date(2025, 8, 5)

    # 重跑冪等
    assert pit.record_first_seen(session, today=date(2025, 8, 6))["new"] == 0
