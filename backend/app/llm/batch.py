"""每日盤後 LLM 批次（架構④ LLMBatchStep）。

把已算好的結論翻白話存 llm_cache：類股方向解讀 + 盤勢總結 + 持股提醒白話。
全用 Haiku + prompt caching（同 translator 的 system 連續命中）。client 不可用則略過
（白天讀舊快取）；單檔失敗回 None 不中斷其他（NotifyStep 之外，pipeline 非必要步）。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..engines.exit_engine import ExitEngine
from ..services.holding_service import HoldingService
from ..storage import models
from .client import LLMClient
from .news_digest import run_news_batch
from .store import cache_key, put_cached
from .translators import HoldingAlertTranslator, MarketTranslator, SectorTranslator


def run_batch(session: Session, td: date, client: LLMClient | None = None) -> dict:
    client = client or LLMClient()
    if not client.available:
        return {"status": "skipped", "reason": "no_api_key"}

    sectors = market = holdings = 0
    model = SectorTranslator().model

    # 類股方向解讀
    sec_tr = SectorTranslator()
    rows = session.execute(
        select(models.SectorDaily, models.Sector.name)
        .join(models.Sector, models.SectorDaily.sector_id == models.Sector.id)
        .where(models.SectorDaily.date == td)
    ).all()
    for sd, name in rows:
        text = sec_tr.translate(
            client, name=name, strength=sd.strength_score, trend_short=sd.trend_short,
            trend_long=sd.trend_long, rotation=sd.rotation_stage, dim_momentum=sd.dim_momentum,
            dim_fund=sd.dim_fund, dim_tech=sd.dim_tech, m5=sd.momentum_5, m20=sd.momentum_20,
            foreign=sd.foreign_net,
        )
        if text:
            put_cached(session, cache_key("sector", sd.sector_id, td), "sector", sd.sector_id, td, text, model)
            sectors += 1
    session.flush()

    # 盤勢總結
    def closes(d):
        return dict(session.execute(
            select(models.DailyPrice.stock_id, models.DailyPrice.close).where(models.DailyPrice.date == d)
        ).all())

    dates = list(session.execute(
        select(models.DailyPrice.date).where(models.DailyPrice.date <= td)
        .distinct().order_by(models.DailyPrice.date.desc()).limit(2)
    ).scalars().all())
    if dates:
        cur = closes(dates[0])
        pv = closes(dates[1]) if len(dates) > 1 else {}
        adv = sum(1 for k, c in cur.items() if c is not None and pv.get(k) is not None and c > pv[k])
        dec = sum(1 for k, c in cur.items() if c is not None and pv.get(k) is not None and c < pv[k])
        turnover = (session.execute(select(func.sum(models.DailyPrice.turnover)).where(models.DailyPrice.date == td)).scalar() or 0) / 1e8
        f, t = session.execute(select(func.sum(models.Institutional.foreign_net), func.sum(models.Institutional.trust_net)).where(models.Institutional.date == td)).one()
        from ..engines.market_breadth import compute_breadth
        text = MarketTranslator().translate(client, advancers=adv, decliners=dec, foreign_net=f, trust_net=t,
                                            turnover_billion=turnover, breadth=compute_breadth(session, td))
        if text:
            put_cached(session, cache_key("market", "tw", td), "market", "tw", td, text, model)
            market = 1
    session.flush()

    # 持股提醒白話（🔴🟠🟡）
    svc, ex, ht = HoldingService(), ExitEngine(), HoldingAlertTranslator()
    for h in session.execute(select(models.Holding).where(models.Holding.status == "open")).scalars().all():
        pos = svc.position(session, h)
        if pos.shares <= 0 or pos.avg_cost is None:
            continue
        close = session.execute(
            select(models.DailyPrice.close).where(models.DailyPrice.stock_id == h.stock_id, models.DailyPrice.date <= td)
            .order_by(models.DailyPrice.date.desc()).limit(1)
        ).scalar()
        if close is None:
            continue
        st = ex.evaluate(session, h, td, avg_cost=pos.avg_cost, close=close)
        if st.level == "green":
            continue
        stock = session.get(models.Stock, h.stock_id)
        text = ht.translate(client, name=stock.name if stock else h.stock_id, track=h.track,
                            level=st.level, signals=st.signals, profitable=close >= pos.avg_cost)
        if text:
            put_cached(session, cache_key("holding", h.id, td), "holding", h.id, td, text, model)
            holdings += 1
    session.flush()

    # 近期消息總結（情報頁四視角）
    news = run_news_batch(session, td, client)

    return {"status": "ok", "sectors": sectors, "market": market, "holdings": holdings, "news": news}
