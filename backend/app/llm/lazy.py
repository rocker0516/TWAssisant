"""LLM 懶生成（架構④）：白天頁面首讀時未命中快取才現場生成一次並回寫。

取代原 LLMBatchStep 夜間全量批次——只為使用者實際點開的內容花 LLM 呼叫。
無金鑰或生成失敗 → 回 None，頁面顯示空（與舊「批次掛了讀舊快取」語意一致）。
回寫走獨立 session_scope（讀取端點的 get_session 不 commit）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..storage import models
from ..storage.database import session_scope
from .client import LLMClient
from .store import cache_key, get_cached, put_cached
from .translators import MarketTranslator, SectorTranslator


def get_or_generate(
    session: Session, kind: str, ref: str | int, td: date,
    generate: Callable[[LLMClient], str | None], model: str,
) -> str | None:
    """命中快取直接回；未命中且 client 可用 → 生成 + 回寫（獨立短交易）。"""
    key = cache_key(kind, ref, td)
    text = get_cached(session, key)
    if text:
        return text
    client = LLMClient()
    if not client.available:
        return None
    text = generate(client)
    if text:
        with session_scope() as s:
            put_cached(s, key, kind, ref, td, text, model)
    return text


def sector_note(session: Session, sector_id: int, td: date) -> str | None:
    """類股方向解讀（類股詳情頁）。"""
    def _gen(client: LLMClient) -> str | None:
        row = session.execute(
            select(models.SectorDaily, models.Sector.name)
            .join(models.Sector, models.SectorDaily.sector_id == models.Sector.id)
            .where(models.SectorDaily.sector_id == sector_id, models.SectorDaily.date == td)
        ).first()
        if row is None:
            return None
        sd, name = row
        return SectorTranslator().translate(
            client, name=name, strength=sd.strength_score, trend_short=sd.trend_short,
            trend_long=sd.trend_long, rotation=sd.rotation_stage, dim_momentum=sd.dim_momentum,
            dim_fund=sd.dim_fund, dim_tech=sd.dim_tech, m5=sd.momentum_5, m20=sd.momentum_20,
            foreign=sd.foreign_net,
        )

    return get_or_generate(session, "sector", sector_id, td, _gen, SectorTranslator.model)


def market_note(session: Session, td: date) -> str | None:
    """盤勢總結（首頁總覽）。"""
    def _gen(client: LLMClient) -> str | None:
        def closes(d):
            return dict(session.execute(
                select(models.DailyPrice.stock_id, models.DailyPrice.close)
                .where(models.DailyPrice.date == d)
            ).all())

        dates = list(session.execute(
            select(models.DailyPrice.date).where(models.DailyPrice.date <= td)
            .distinct().order_by(models.DailyPrice.date.desc()).limit(2)
        ).scalars().all())
        if not dates:
            return None
        cur = closes(dates[0])
        pv = closes(dates[1]) if len(dates) > 1 else {}
        adv = sum(1 for k, c in cur.items() if c is not None and pv.get(k) is not None and c > pv[k])
        dec = sum(1 for k, c in cur.items() if c is not None and pv.get(k) is not None and c < pv[k])
        turnover = (session.execute(
            select(func.sum(models.DailyPrice.turnover)).where(models.DailyPrice.date == td)
        ).scalar() or 0) / 1e8
        f, t = session.execute(
            select(func.sum(models.Institutional.foreign_net), func.sum(models.Institutional.trust_net))
            .where(models.Institutional.date == td)
        ).one()
        from ..engines.market_breadth import compute_breadth

        return MarketTranslator().translate(
            client, advancers=adv, decliners=dec, foreign_net=f, trust_net=t,
            turnover_billion=turnover, breadth=compute_breadth(session, td),
        )

    return get_or_generate(session, "market", "tw", td, _gen, MarketTranslator.model)
