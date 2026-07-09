"""近期消息總結（情報頁）——懶生成版。

讀 events 表近 N 日事件 → 首讀未命中快取才用 News 翻譯員生成並回寫 llm_cache：
  news_market（全市場）/ news_theme:<sector_id>（依題材分群）/
  news_focus:me（持股+觀察清單焦點）/ news_stock:<stock_id>（個股）。
intel 頁一次要多篇（市場+焦點+最多 MAX_THEMES 個題材）→ 缺的部分以執行緒池
平行生成（DB 讀寫都在主執行緒，執行緒只打 LLM）。全用 Haiku；無金鑰或單篇
失敗 → 該篇 None，頁面顯示空，不中斷其他。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..storage import models
from ..storage.database import session_scope
from .assistant import _pe_level
from .client import LLMClient
from .lazy import get_or_generate
from .store import cache_key, get_cached, put_cached
from .translators import (
    NewsFocusTranslator,
    NewsMarketTranslator,
    NewsStockTranslator,
    NewsThemeTranslator,
    _mom,
)

DIGEST_DAYS = 7      # 總結窗口（對齊 NewsEngine.lookback_days）
MAX_THEMES = 12      # 最多產幾個類股 digest（限 LLM 呼叫量）
_WORKERS = 8         # intel 缺篇平行生成的執行緒數


def _recent_events(session: Session, td: date) -> list[dict]:
    """近 DIGEST_DAYS 日事件（含個股名/類股），利空優先、再依日期新到舊。"""
    start = td - timedelta(days=DIGEST_DAYS)
    rows = session.execute(
        select(models.Event, models.Stock.name, models.Stock.sector_id)
        .join(models.Stock, models.Event.stock_id == models.Stock.id, isouter=True)
        .where(models.Event.date >= start, models.Event.date <= td)
        .order_by(models.Event.is_risk.desc(), models.Event.date.desc())
    ).all()
    return [
        {
            "stock_id": e.stock_id, "name": name, "sector_id": sector_id,
            "date": e.date, "category": e.category, "title": e.title,
            "is_risk": bool(e.is_risk),
        }
        for e, name, sector_id in rows
    ]


def _focus_ids(session: Session) -> dict[str, str]:
    """持股(open) ∪ 觀察清單股號 → role（持股優先）。"""
    out: dict[str, str] = {}
    for sid in session.execute(
        select(models.WatchlistItem.stock_id).distinct()
    ).scalars().all():
        out[sid] = "watch"
    for sid in session.execute(
        select(models.Holding.stock_id).where(models.Holding.status == "open").distinct()
    ).scalars().all():
        out[sid] = "holding"
    return out


def _stock_fund(session: Session, stock_id: str) -> tuple[str, str, str]:
    """個股名 + 月營收趨勢 + 估值水準（質化）。"""
    stock = session.get(models.Stock, stock_id)
    rev = session.execute(
        select(models.RevenueMonthly).where(models.RevenueMonthly.stock_id == stock_id)
        .order_by(models.RevenueMonthly.year.desc(), models.RevenueMonthly.month.desc()).limit(1)
    ).scalars().first()
    val = session.execute(
        select(models.Valuation).where(models.Valuation.stock_id == stock_id)
        .order_by(models.Valuation.date.desc()).limit(1)
    ).scalars().first()
    name = stock.name if stock else stock_id
    return name, (_mom(rev.yoy) if rev else "未知"), _pe_level(val.pe if val else None)


def stock_digest(session: Session, stock_id: str, td: date) -> str | None:
    """個股近期消息 digest（個股詳情頁）。無事件 → None（不花 LLM）。"""
    def _gen(client: LLMClient) -> str | None:
        start = td - timedelta(days=DIGEST_DAYS)
        rows = session.execute(
            select(models.Event)
            .where(models.Event.stock_id == stock_id,
                   models.Event.date >= start, models.Event.date <= td)
            .order_by(models.Event.is_risk.desc(), models.Event.date.desc())
        ).scalars().all()
        if not rows:
            return None
        evs = [
            {"name": None, "date": e.date, "category": e.category,
             "title": e.title, "is_risk": bool(e.is_risk)}
            for e in rows
        ]
        name, rev_trend, pe_level = _stock_fund(session, stock_id)
        return NewsStockTranslator().translate(
            client, name=name, events=evs, revenue_trend=rev_trend,
            pe_level=pe_level, has_risk=any(x["is_risk"] for x in evs),
        )

    return get_or_generate(
        session, "news_stock", stock_id, td, _gen, NewsStockTranslator.model
    )


def intel_digests(session: Session, td: date) -> dict:
    """情報頁四視角 digest：命中讀快取、缺的平行生成後回寫。

    回 {"market": str|None, "focus": str|None, "themes": [(sector_id, text), ...]}。
    themes 只含「本窗口夠格」的類股（≥3 則或含利空，取前 MAX_THEMES）。
    """
    events = _recent_events(session, td)
    if not events:
        return {"market": None, "focus": None, "themes": []}

    # 分群索引
    by_sector: dict[int, list[dict]] = {}
    by_stock: dict[str, list[dict]] = {}
    for e in events:
        if e["sector_id"] is not None:
            by_sector.setdefault(e["sector_id"], []).append(e)
        if e["stock_id"]:
            by_stock.setdefault(e["stock_id"], []).append(e)

    # 夠格題材：利空優先、再依事件數；門檻 = ≥3 則或含利空
    sector_ids = sorted(
        by_sector,
        key=lambda s: (any(x["is_risk"] for x in by_sector[s]), len(by_sector[s])),
        reverse=True,
    )
    sector_ids = [
        s for s in sector_ids
        if len(by_sector[s]) >= 3 or any(x["is_risk"] for x in by_sector[s])
    ][:MAX_THEMES]

    # 先讀快取，收集缺的生成工作：(kind, ref, model, thunk)
    market = get_cached(session, cache_key("news_market", "tw", td))
    focus = get_cached(session, cache_key("news_focus", "me", td))
    themes: dict[int, str | None] = {
        sid: get_cached(session, cache_key("news_theme", sid, td)) for sid in sector_ids
    }

    jobs: list[tuple[str, str, str, object]] = []

    if market is None:
        risk_count = sum(1 for e in events if e["is_risk"])
        theme_count = sum(1 for e in events if e["category"] == "題材")
        tr = NewsMarketTranslator()
        jobs.append(("news_market", "tw", tr.model, lambda c, tr=tr: tr.translate(
            c, days=DIGEST_DAYS, total=len(events),
            risk_count=risk_count, theme_count=theme_count, events=events,
        )))

    if focus is None:
        focus_stocks = []
        for sid, role in _focus_ids(session).items():
            evs = by_stock.get(sid, [])
            if not evs:
                continue
            stock = session.get(models.Stock, sid)
            focus_stocks.append({
                "name": stock.name if stock else sid, "role": role,
                "has_risk": any(x["is_risk"] for x in evs), "events": evs,
            })
        if focus_stocks:
            tr = NewsFocusTranslator()
            jobs.append(("news_focus", "me", tr.model, lambda c, tr=tr, st=focus_stocks: tr.translate(
                c, stocks=st,
            )))

    missing_themes = [sid for sid in sector_ids if themes[sid] is None]
    if missing_themes:
        sect_names = dict(session.execute(select(models.Sector.id, models.Sector.name)).all())
        sect_dir = {
            sd.sector_id: sd.trend_short
            for sd in session.execute(
                select(models.SectorDaily).where(models.SectorDaily.date == td)
            ).scalars().all()
        }
        tt = NewsThemeTranslator()
        for sid in missing_themes:
            evs = by_sector[sid]
            jobs.append(("news_theme", str(sid), tt.model, lambda c, sid=sid, evs=evs: tt.translate(
                c, sector=sect_names.get(sid, str(sid)), direction=sect_dir.get(sid) or "未知",
                total=len(evs), risk_count=sum(1 for x in evs if x["is_risk"]), events=evs,
            )))

    if jobs:
        client = LLMClient()
        if client.available:
            with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
                texts = list(pool.map(lambda j: j[3](client), jobs))
            with session_scope() as s:
                for (kind, ref, model, _), text in zip(jobs, texts):
                    if not text:
                        continue
                    put_cached(s, cache_key(kind, ref, td), kind, ref, td, text, model)
                    if kind == "news_market":
                        market = text
                    elif kind == "news_focus":
                        focus = text
                    else:
                        themes[int(ref)] = text

    return {
        "market": market,
        "focus": focus,
        "themes": [(sid, themes[sid]) for sid in sector_ids if themes[sid]],
    }
