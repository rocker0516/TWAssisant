"""IndicatorEngine 增量計算測試：增量結果須與全量重算等值、只寫新列、無新資料跳過。"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.engines.indicators import IndicatorEngine
from app.storage import models
from app.storage.database import Base


@pytest.fixture()
def session():
    eng = create_engine("sqlite://", future=True)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)()
    yield s
    s.close()


def _seed_prices(session, stock_id: str, n_days: int, *, seed: int = 7) -> list[date]:
    session.merge(models.Stock(id=stock_id, name=f"測試{stock_id}"))
    rng = np.random.default_rng(seed)
    closes = 100 * np.cumprod(1 + rng.normal(0, 0.02, n_days))
    d0 = date(2023, 1, 2)
    dates = []
    for i, c in enumerate(closes):
        d = d0 + timedelta(days=i)  # 連續日曆天即可，指標只看順序
        dates.append(d)
        session.add(models.DailyPrice(
            stock_id=stock_id, date=d,
            open=float(c * 0.995), high=float(c * 1.01), low=float(c * 0.99),
            close=float(c), volume=int(1000 + rng.integers(0, 500)),
        ))
    session.flush()
    return dates


def _snapshot(session, stock_id: str, d: date) -> dict:
    row = session.execute(
        select(models.Indicator).where(
            models.Indicator.stock_id == stock_id, models.Indicator.date == d
        )
    ).scalars().one()
    return {c: getattr(row, c) for c in (
        "ma5", "ma20", "ma60", "ma240", "kd_k", "kd_d", "macd", "macd_signal", "atr14", "bias_20"
    )}


def test_incremental_matches_full_recompute(session):
    """先算到 T-1、補一天價格增量算 → 最新列與整段 full 重算完全一致（暖身窗足）。"""
    dates = _seed_prices(session, "9901", 800)
    cut = dates[-2]

    # 先把 T-1 以前算好（模擬「昨天跑過」）
    session.query = session.query  # noqa: B018 — 保持介面明確
    last_price = session.execute(
        select(models.DailyPrice).where(models.DailyPrice.date > cut)
    ).scalars().all()
    for p in last_price:
        session.delete(p)
    session.flush()
    IndicatorEngine().run(session, cut)

    # 補回最後一天，增量跑
    _restore = dates[-1]
    session.add(models.DailyPrice(
        stock_id="9901", date=_restore,
        open=100.0, high=103.0, low=99.0, close=102.0, volume=1500,
    ))
    session.flush()
    res = IndicatorEngine().run(session, _restore)
    assert res["status"] == "ok" and res["rows"] == 1
    incr = _snapshot(session, "9901", _restore)

    # 對照組：砍掉指標全量重算
    session.execute(models.Indicator.__table__.delete())
    session.flush()
    IndicatorEngine().run(session, _restore, full=True)
    full = _snapshot(session, "9901", _restore)

    for k, v in full.items():
        assert v is not None, k
        assert math.isclose(incr[k], v, rel_tol=1e-9, abs_tol=1e-9), (
            f"{k}: incremental={incr[k]} full={v}"
        )


def test_incremental_only_writes_new_rows(session):
    dates = _seed_prices(session, "9902", 300)
    IndicatorEngine().run(session, dates[-1])
    before = session.execute(select(func.count()).select_from(models.Indicator)).scalar_one()

    # 無新價格 → up_to_date、不新增
    res = IndicatorEngine().run(session, dates[-1])
    assert res.get("note") == "up_to_date"
    after = session.execute(select(func.count()).select_from(models.Indicator)).scalar_one()
    assert after == before


def test_cold_start_full_history(session):
    """該股無任何指標（冷啟動）→ 全歷史入庫。"""
    dates = _seed_prices(session, "9903", 300)
    res = IndicatorEngine().run(session, dates[-1])
    assert res["status"] == "ok"
    n = session.execute(
        select(func.count()).select_from(models.Indicator)
        .where(models.Indicator.stock_id == "9903")
    ).scalar_one()
    # ewm 類指標（KD/MACD/ATR）首列即有值，dropna(how="all") 不會丟任何列
    assert n == 300
