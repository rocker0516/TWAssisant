"""類股端點（P3）：強弱排行 / 熱力圖資料 + 類股專屬頁。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..llm.lazy import sector_note
from ..storage import models
from .deps import get_session
from .schemas import SectorConstituent, SectorDetail, SectorItem, SectorList

router = APIRouter(prefix="/sectors", tags=["sectors"])


def _to_item(sd: models.SectorDaily, name: str, turnover_chg5: float | None = None) -> SectorItem:
    return SectorItem(
        id=sd.sector_id, name=name, strength_score=sd.strength_score,
        dim_momentum=sd.dim_momentum, dim_fund=sd.dim_fund, dim_tech=sd.dim_tech,
        trend_short=sd.trend_short, trend_long=sd.trend_long, rotation_stage=sd.rotation_stage,
        momentum_5=sd.momentum_5, momentum_20=sd.momentum_20, foreign_net=sd.foreign_net,
        turnover_share=sd.turnover_share, above_ma20=sd.above_ma20, constituents=sd.constituents,
        turnover_chg5=turnover_chg5,
    )


@router.get("", response_model=SectorList)
def list_sectors(session: Session = Depends(get_session)) -> SectorList:
    d = session.execute(select(func.max(models.SectorDaily.date))).scalar()
    if d is None:
        return SectorList(date=None, items=[])
    rows = session.execute(
        select(models.SectorDaily, models.Sector.name)
        .join(models.Sector, models.SectorDaily.sector_id == models.Sector.id)
        .where(models.SectorDaily.date == d)
        .order_by(models.SectorDaily.strength_score.desc())
    ).all()

    # 成交佔比 vs 前 5 日均（個百分點）：+ = 資金正移入該類股
    prev_dates = session.execute(
        select(models.SectorDaily.date).distinct()
        .where(models.SectorDaily.date < d)
        .order_by(models.SectorDaily.date.desc()).limit(5)
    ).scalars().all()
    chg5: dict[int, float] = {}
    if prev_dates:
        for sid_, avg_ in session.execute(
            select(models.SectorDaily.sector_id, func.avg(models.SectorDaily.turnover_share))
            .where(models.SectorDaily.date.in_(prev_dates),
                   models.SectorDaily.turnover_share.is_not(None))
            .group_by(models.SectorDaily.sector_id)
        ).all():
            chg5[sid_] = avg_
    items = []
    for sd, name in rows:
        prev = chg5.get(sd.sector_id)
        delta = (round(sd.turnover_share - prev, 2)
                 if sd.turnover_share is not None and prev is not None else None)
        items.append(_to_item(sd, name, delta))
    return SectorList(date=d, items=items)


@router.get("/{sector_id}", response_model=SectorDetail)
def sector_detail(sector_id: int, session: Session = Depends(get_session)) -> SectorDetail:
    d = session.execute(select(func.max(models.SectorDaily.date))).scalar()
    sd = session.get(models.SectorDaily, {"sector_id": sector_id, "date": d}) if d else None
    sector = session.get(models.Sector, sector_id)
    if sector is None or sd is None:
        raise HTTPException(404, f"找不到類股 {sector_id}")

    # 成分股 + 最新評分 + 當日漲跌（領漲排序、標記已推薦）
    stocks = session.execute(
        select(models.Stock.id, models.Stock.name).where(models.Stock.sector_id == sector_id)
    ).all()
    scores = {
        (sc.stock_id, sc.track): sc
        for sc in session.execute(
            select(models.Score).where(models.Score.date == d)
        ).scalars().all()
    }

    # 產業鏈細分標籤（一次撈整個類股的成員，個股去重保序）
    ids = [sid for sid, _ in stocks]
    tag_map: dict[str, list[str]] = {}
    for sid, node_name in session.execute(
        select(models.IndustryChainMember.stock_id, models.IndustryChainMember.node_name)
        .where(models.IndustryChainMember.stock_id.in_(ids),
               models.IndustryChainMember.node_name.is_not(None))
        .order_by(models.IndustryChainMember.stock_id, models.IndustryChainMember.chain_id,
                  models.IndustryChainMember.node_id)
    ).all():
        tags = tag_map.setdefault(sid, [])
        if node_name not in tags:
            tags.append(node_name)

    # 批次備料（避免逐檔 N+1）：近 21 個交易日收盤 → 當日漲跌 + 5/20 日動能
    from datetime import timedelta

    px_since = d - timedelta(days=45)
    closes: dict[str, list[float]] = {}
    for sid, cl in session.execute(
        select(models.DailyPrice.stock_id, models.DailyPrice.close)
        .where(models.DailyPrice.stock_id.in_(ids),
               models.DailyPrice.date > px_since, models.DailyPrice.date <= d,
               models.DailyPrice.close.is_not(None))
        .order_by(models.DailyPrice.date)
    ).all():
        closes.setdefault(sid, []).append(cl)

    # 站上月線：當日 indicators.ma20 vs 收盤
    ma20_map = dict(session.execute(
        select(models.Indicator.stock_id, models.Indicator.ma20)
        .where(models.Indicator.stock_id.in_(ids), models.Indicator.date == d)
    ).all())

    # 法人近 5 / 20 交易日淨買超合計（5 vs 20 對照＝加速買 / 退潮）
    inst_dates = session.execute(
        select(models.Institutional.date).distinct()
        .where(models.Institutional.date <= d)
        .order_by(models.Institutional.date.desc()).limit(20)
    ).scalars().all()
    inst5_map: dict[str, int] = {}
    inst20_map: dict[str, int] = {}
    if inst_dates:
        d5 = set(inst_dates[:5])
        for sid, dd, net in session.execute(
            select(models.Institutional.stock_id, models.Institutional.date,
                   models.Institutional.total_net)
            .where(models.Institutional.stock_id.in_(ids),
                   models.Institutional.date.in_(inst_dates),
                   models.Institutional.total_net.is_not(None))
        ).all():
            inst20_map[sid] = inst20_map.get(sid, 0) + int(net)
            if dd in d5:
                inst5_map[sid] = inst5_map.get(sid, 0) + int(net)

    # 最新月營收 YoY（每檔取最近一期）
    rev_map: dict[str, float] = {}
    for sid, yoy in session.execute(
        select(models.RevenueMonthly.stock_id, models.RevenueMonthly.yoy)
        .where(models.RevenueMonthly.stock_id.in_(ids),
               models.RevenueMonthly.yoy.is_not(None))
        .order_by(models.RevenueMonthly.year, models.RevenueMonthly.month)
    ).all():
        rev_map[sid] = round(yoy, 1)  # 升冪迭代 → 最後留下的即最新一期

    def _mom(seq: list[float], n: int) -> float | None:
        if len(seq) <= n or not seq[-n - 1]:
            return None
        return round((seq[-1] / seq[-n - 1] - 1) * 100, 2)

    items: list[SectorConstituent] = []
    for sid, name in stocks:
        seq = closes.get(sid)
        if not seq:
            continue
        close = seq[-1]
        change_pct = round((close - seq[-2]) / seq[-2] * 100, 2) if len(seq) > 1 and seq[-2] else None
        w, ll = scores.get((sid, "wave")), scores.get((sid, "long"))
        ma20 = ma20_map.get(sid)
        items.append(SectorConstituent(
            stock_id=sid, name=name, close=close, change_pct=change_pct,
            wave_score=w.total_score if w else None,
            long_score=ll.total_score if ll else None,
            recommended=bool((w and w.passed) or (ll and ll.passed)),
            tags=tag_map.get(sid, []),
            mom5_pct=_mom(seq, 5), mom20_pct=_mom(seq, 20),
            above_ma20=(close > ma20) if ma20 else None,
            inst_net5=inst5_map.get(sid),
            inst_net20=inst20_map.get(sid),
            rev_yoy=rev_map.get(sid),
        ))
    items.sort(key=lambda c: (c.change_pct if c.change_pct is not None else -999), reverse=True)
    interp = sector_note(session, sector_id, d) if d else None

    # 大盤同期動能（細分相對強弱的基準）
    mkt = session.execute(
        select(models.MarketIndex.close)
        .where(models.MarketIndex.date <= d, models.MarketIndex.close.is_not(None))
        .order_by(models.MarketIndex.date.desc()).limit(21)
    ).scalars().all()  # 降冪：[0]=最新

    def _mkt_mom(n: int) -> float | None:
        if len(mkt) <= n or not mkt[n]:
            return None
        return round((mkt[0] / mkt[n] - 1) * 100, 2)

    return SectorDetail(sector=_to_item(sd, sector.name), constituents=items,
                        interpretation=interp,
                        market_mom5=_mkt_mom(5), market_mom20=_mkt_mom(20))
