"""Level 1 ML 推薦軌端點（FRS §8：Top-K 是 Ledger 全排名上的視圖，K 不固定）。

資料源=level1_predictions（每日盤後 Level1PredictStep 寫入）。
純預測/排序展示：不帶買賣指令語意（§20 Model ≠ Trading System）。
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..storage import models
from .deps import get_session

router = APIRouter(prefix="/level1", tags=["level1"])

_HORIZONS = (1, 5, 10)

# 展示層只認一個版本。ledger PK 含 model_version，換版時舊列並存——不過濾會使同一
# 支股票回傳多列、rank 重複，Top-K 直接失真（設計 §8.1）。
# 此常數必須與 scripts/level1_predict.py 的 MODEL_VERSION 一致。
CURRENT_MODEL_VERSION = "l1_lgbm_v2"


class Level1Item(BaseModel):
    rank: int
    stock_id: str
    name: str | None
    score: float
    pct_rank: float
    close: float | None = None
    actual_return: float | None = None   # 成熟後才有
    actual_pct: float | None = None


class Level1Board(BaseModel):
    date: date | None
    horizon: int
    k: int
    model_version: str | None
    universe_size: int | None
    items: list[Level1Item]


class Level1MaturedDay(BaseModel):
    prediction_date: date
    topk_mean_return: float      # Top-K 平均實際報酬
    universe_mean_return: float  # 全體平均（同日基準）
    excess: float                # 超額
    topk_mean_actual_pct: float  # Top-K 平均實際百分位（0.5=無資訊）


class Level1Performance(BaseModel):
    horizon: int
    k: int
    n_days: int
    mean_excess: float | None
    day_win_rate: float | None   # 超額>0 的日子占比
    mean_actual_pct: float | None
    days: list[Level1MaturedDay]


@router.get("/board", response_model=Level1Board)
def board(
    horizon: int = Query(5, description="1/5/10；5D 為主軌"),
    k: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session),
) -> Level1Board:
    """最新交易日的 Top-K 排名（含成熟後回填的實際表現）。"""
    if horizon not in _HORIZONS:
        horizon = 5
    P = models.Level1Prediction
    d = session.execute(
        select(func.max(P.prediction_date)).where(
            P.horizon == horizon, P.model_version == CURRENT_MODEL_VERSION)
    ).scalar()
    if d is None:
        return Level1Board(date=None, horizon=horizon, k=k,
                           model_version=None, universe_size=None, items=[])
    rows = session.execute(
        select(P, models.Stock.name, models.DailyPrice.close)
        .join(models.Stock, P.stock_id == models.Stock.id)
        .outerjoin(models.DailyPrice,
                   (models.DailyPrice.stock_id == P.stock_id)
                   & (models.DailyPrice.date == P.prediction_date))
        .where(P.horizon == horizon, P.prediction_date == d,
               P.model_version == CURRENT_MODEL_VERSION)
        .order_by(P.rank).limit(k)
    ).all()
    items = [
        Level1Item(rank=p.rank, stock_id=p.stock_id, name=name,
                   score=round(p.score, 4), pct_rank=round(p.pct_rank, 4),
                   close=close, actual_return=p.actual_return,
                   actual_pct=p.actual_pct)
        for p, name, close in rows
    ]
    mv = rows[0][0].model_version if rows else None
    us = rows[0][0].universe_size if rows else None
    return Level1Board(date=d, horizon=horizon, k=k, model_version=mv,
                       universe_size=us, items=items)


@router.get("/performance", response_model=Level1Performance)
def performance(
    horizon: int = Query(5),
    k: int = Query(20, ge=1, le=100),
    limit: int = Query(60, ge=1, le=250, description="最近 N 個已成熟預測日"),
    session: Session = Depends(get_session),
) -> Level1Performance:
    """已成熟預測日的 Top-K 實績（Ledger 可驗證戰績，§15）。"""
    if horizon not in _HORIZONS:
        horizon = 5
    P = models.Level1Prediction
    dates = session.execute(
        select(P.prediction_date).distinct()
        .where(P.horizon == horizon, P.actual_return.is_not(None),
               P.model_version == CURRENT_MODEL_VERSION)
        .order_by(P.prediction_date.desc()).limit(limit)
    ).scalars().all()
    days: list[Level1MaturedDay] = []
    for d in sorted(dates):
        topk = session.execute(
            select(func.avg(P.actual_return), func.avg(P.actual_pct))
            .where(P.horizon == horizon, P.prediction_date == d,
                   P.rank <= k, P.actual_return.is_not(None),
                   P.model_version == CURRENT_MODEL_VERSION)
        ).one()
        univ = session.execute(
            select(func.avg(P.actual_return))
            .where(P.horizon == horizon, P.prediction_date == d,
                   P.actual_return.is_not(None),
                   P.model_version == CURRENT_MODEL_VERSION)
        ).scalar()
        if topk[0] is None or univ is None:
            continue
        days.append(Level1MaturedDay(
            prediction_date=d,
            topk_mean_return=round(topk[0], 5),
            universe_mean_return=round(univ, 5),
            excess=round(topk[0] - univ, 5),
            topk_mean_actual_pct=round(topk[1], 4),
        ))
    if not days:
        return Level1Performance(horizon=horizon, k=k, n_days=0, mean_excess=None,
                                 day_win_rate=None, mean_actual_pct=None, days=[])
    n = len(days)
    return Level1Performance(
        horizon=horizon, k=k, n_days=n,
        mean_excess=round(sum(x.excess for x in days) / n, 5),
        day_win_rate=round(sum(1 for x in days if x.excess > 0) / n, 3),
        mean_actual_pct=round(sum(x.topk_mean_actual_pct for x in days) / n, 4),
        days=days,
    )
