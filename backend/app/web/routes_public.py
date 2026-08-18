"""公開頁（匿名、可被索引）：伺服器端 Jinja 模板，與 /app 的 SPA 完全分離。

命名空間約定（與 main.py 的登入牆互為前提）：
  /api/*  JSON API，需 session（AUTH_EXEMPT 除外）
  /app/*  SPA 殼，登入後的工具
  其餘    本模組的公開頁——只放全站共用的盤後資料，沒有任何 per-user 內容，
          所以整個命名空間可以匿名放行，不需要逐路徑登記。

內容邊界（法規考量，見 docs/superpowers/specs 的分層設計第 3 節）：
  公開頁只放「客觀事實」——收盤、漲跌、成交值、法人買賣超。不放買進區間、
  停損、推薦名單；那些屬「建議」，留在登入牆後。

效能：每頁 2~4 個對日期索引的查詢，當前流量下即時算即可。日後流量大了，
正確的下一步是 pipeline 加預生成 step（資料一天只變一次），不是加 cache header。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..api.deps import get_session
from ..storage import models

router = APIRouter(tags=["public"], include_in_schema=False)
_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _latest_dates(session: Session, n: int = 2) -> list[date]:
    """最近 n 個有行情的交易日（降冪）。漲跌幅要兩天收盤才算得出來。"""
    rows = session.execute(
        select(models.DailyPrice.date).distinct()
        .order_by(models.DailyPrice.date.desc()).limit(n)
    ).scalars().all()
    return list(rows)


@dataclass
class _Row:
    stock_id: str
    name: str
    close: float | None
    value: float
    display: str
    signed: bool = True  # 值有方向性（漲跌/買賣超）才上紅綠色


_TOP_N = 20


@router.get("/rankings", response_class=HTMLResponse)
def rankings(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    dates = _latest_dates(session, 2)
    if not dates:
        return _templates.TemplateResponse(request, "rankings.html",
                                           {"cutoff": "尚無資料", "boards": []})
    d0 = dates[0]

    # 漲跌幅：兩日收盤自比。上市+上櫃全宇宙，一次查回來在 Python 配對——
    # 兩日各 ~2000 列，比 SQL 自 join 好讀且對 SQLite 更省查詢計畫。
    boards: list[dict] = []
    if len(dates) == 2:
        d1 = dates[1]
        prev = dict(session.execute(
            select(models.DailyPrice.stock_id, models.DailyPrice.close)
            .where(models.DailyPrice.date == d1, models.DailyPrice.close.is_not(None))
        ).all())
        cur = session.execute(
            select(models.DailyPrice.stock_id, models.DailyPrice.close, models.Stock.name)
            .join(models.Stock, models.Stock.id == models.DailyPrice.stock_id)
            .where(models.DailyPrice.date == d0, models.DailyPrice.close.is_not(None))
        ).all()
        chg = [
            _Row(sid, name, c, pct, f"{pct:+.2f}%")
            for sid, c, name in cur
            if (p := prev.get(sid)) and p > 0
            for pct in [(c / p - 1) * 100]
        ]
        chg.sort(key=lambda r: -r.value)
        boards.append({"title": "漲幅排行", "value_label": "漲跌幅", "rows": chg[:_TOP_N]})
        boards.append({"title": "跌幅排行", "value_label": "漲跌幅",
                       "rows": list(reversed(chg[-_TOP_N:]))})

    # 成交值
    turn = session.execute(
        select(models.DailyPrice.stock_id, models.DailyPrice.close,
               models.DailyPrice.turnover, models.Stock.name)
        .join(models.Stock, models.Stock.id == models.DailyPrice.stock_id)
        .where(models.DailyPrice.date == d0, models.DailyPrice.turnover.is_not(None))
        .order_by(models.DailyPrice.turnover.desc()).limit(_TOP_N)
    ).all()
    boards.append({"title": "成交值排行", "value_label": "成交值(億)", "rows": [
        _Row(sid, name, c, t, f"{t / 1e8:.1f}", signed=False)
        for sid, c, t, name in turn
    ]})

    # 三大法人合計買超（張）
    inst = session.execute(
        select(models.Institutional.stock_id, models.Institutional.total_net,
               models.Stock.name, models.DailyPrice.close)
        .join(models.Stock, models.Stock.id == models.Institutional.stock_id)
        .join(models.DailyPrice,
              (models.DailyPrice.stock_id == models.Institutional.stock_id)
              & (models.DailyPrice.date == d0))
        .where(models.Institutional.date == d0, models.Institutional.total_net.is_not(None))
        .order_by(models.Institutional.total_net.desc()).limit(_TOP_N)
    ).all()
    boards.append({"title": "法人買超排行", "value_label": "買超(張)", "rows": [
        _Row(sid, name, c, n, f"{n:+,}") for sid, n, name, c in inst
    ]})

    return _templates.TemplateResponse(
        request, "rankings.html", {"cutoff": d0.isoformat(), "boards": boards})


@router.get("/stocks", response_class=HTMLResponse)
def stocks_index(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    """全站個股索引。存在的理由是內鏈：沒有這頁，個股頁對爬蟲是孤兒。"""
    dates = _latest_dates(session, 1)
    d0 = dates[0] if dates else None

    rows = session.execute(
        select(models.Stock.id, models.Stock.name, models.Sector.name.label("sector"),
               models.DailyPrice.close)
        .join(models.Sector, models.Sector.id == models.Stock.sector_id, isouter=True)
        .join(models.DailyPrice,
              (models.DailyPrice.stock_id == models.Stock.id)
              & (models.DailyPrice.date == d0), isouter=True)
        .order_by(models.Sector.name, models.Stock.id)
    ).all()

    groups: list[dict] = []
    for sid, name, sector, close in rows:
        label = sector or "未分類"
        if not groups or groups[-1]["sector"] != label:
            groups.append({"sector": label, "stocks": []})
        groups[-1]["stocks"].append(
            {"stock_id": sid, "name": name, "close": close})

    return _templates.TemplateResponse(
        request, "stocks_index.html",
        {"cutoff": d0.isoformat() if d0 else "尚無資料",
         "total": len(rows), "groups": groups})


@router.get("/stock/{stock_id}", response_class=HTMLResponse)
def stock_page(stock_id: str, request: Request,
               session: Session = Depends(get_session)) -> HTMLResponse:
    """公開個股頁。內容邊界（分層設計待決事項的落地）：只放客觀資料——
    行情/估值/月營收/季財報/股利/公司資料。買進區間、停損、評分、目標價、
    支撐壓力一律不出現在匿名層；那些已進入「建議」的範疇，留在登入牆後。
    """
    stock = session.get(models.Stock, stock_id)
    if stock is None:
        raise HTTPException(404, "查無此股票")
    sector = session.get(models.Sector, stock.sector_id) if stock.sector_id else None

    # 行情：該股自己的最近兩個交易日（停牌股的最新價可能早於全市場最新日）
    prices = session.execute(
        select(models.DailyPrice).where(models.DailyPrice.stock_id == stock_id)
        .order_by(models.DailyPrice.date.desc()).limit(2)
    ).scalars().all()
    p0 = prices[0] if prices else None
    chg = None
    if len(prices) == 2 and p0.close and prices[1].close:
        chg = (p0.close / prices[1].close - 1) * 100

    w52_high = w52_low = None
    if p0 is not None:
        w52_high, w52_low = session.execute(
            select(func.max(models.DailyPrice.high), func.min(models.DailyPrice.low))
            .where(models.DailyPrice.stock_id == stock_id,
                   models.DailyPrice.date > p0.date - timedelta(days=365))
        ).one()

    quote = {
        "close": p0.close if p0 else None,
        "change_pct": chg,
        "volume": p0.volume if p0 else None,
        "turnover": p0.turnover if p0 else None,
        "w52_high": w52_high,
        "w52_low": w52_low,
    }

    valuation = session.execute(
        select(models.Valuation).where(models.Valuation.stock_id == stock_id)
        .order_by(models.Valuation.date.desc()).limit(1)
    ).scalars().first()

    profile = session.get(models.CompanyProfile, stock_id)
    market_cap = None
    if profile and profile.issued_shares and p0 and p0.close:
        market_cap = profile.issued_shares * p0.close

    revenues = session.execute(
        select(models.RevenueMonthly).where(models.RevenueMonthly.stock_id == stock_id)
        .order_by(models.RevenueMonthly.year.desc(), models.RevenueMonthly.month.desc())
        .limit(12)
    ).scalars().all()

    quarters = session.execute(
        select(models.FinancialQuarter).where(models.FinancialQuarter.stock_id == stock_id)
        .order_by(models.FinancialQuarter.year.desc(), models.FinancialQuarter.quarter.desc())
        .limit(8)
    ).scalars().all()

    dividends = session.execute(
        select(models.Dividend).where(models.Dividend.stock_id == stock_id)
        .order_by(models.Dividend.period.desc()).limit(6)
    ).scalars().all()

    return _templates.TemplateResponse(request, "stock.html", {
        "cutoff": p0.date.isoformat() if p0 else "尚無資料",
        "stock": stock,
        "sector_name": sector.name if sector else None,
        "quote": type("Q", (), quote)(),
        "valuation": valuation,
        "market_cap": market_cap,
        "profile": profile,
        "revenues": revenues,
        "quarters": quarters,
        "dividends": dividends,
    })


@router.get("/", response_class=HTMLResponse)
def home(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    """公開首頁：大盤狀態磚＋今日通過篩選數（登入鉤子）＋類股強弱＋今日消息。

    「今日 N 檔通過篩選」只給數字不給名單——具體數字比行銷文案有說服力，
    名單本身屬登入牆後（見檔頭內容邊界）。
    """
    dates = _latest_dates(session, 2)
    d0 = dates[0] if dates else None
    if d0 is None:
        return _templates.TemplateResponse(request, "home.html", {
            "cutoff": "尚無資料", "idx_close": None, "idx_chg": None,
            "turnover_total": None, "n_up": 0, "n_down": 0, "inst_total": None,
            "n_passed": 0, "n_wave": 0, "n_long": 0,
            "sectors": [], "news": [], "n_stocks": 0})

    # 加權指數：取最近兩筆自算漲跌（index 表只有 close）
    idx = session.execute(
        select(models.MarketIndex.close).order_by(models.MarketIndex.date.desc()).limit(2)
    ).scalars().all()
    idx_close = idx[0] if idx else None
    idx_chg = (idx[0] / idx[1] - 1) * 100 if len(idx) == 2 and idx[1] else None

    turnover_total = session.execute(
        select(func.sum(models.DailyPrice.turnover)).where(models.DailyPrice.date == d0)
    ).scalar_one()
    turnover_total = turnover_total / 1e8 if turnover_total else None

    # 漲跌家數：兩日收盤配對
    n_up = n_down = 0
    if len(dates) == 2:
        prev = dict(session.execute(
            select(models.DailyPrice.stock_id, models.DailyPrice.close)
            .where(models.DailyPrice.date == dates[1], models.DailyPrice.close.is_not(None))
        ).all())
        for sid, c in session.execute(
            select(models.DailyPrice.stock_id, models.DailyPrice.close)
            .where(models.DailyPrice.date == d0, models.DailyPrice.close.is_not(None))
        ).all():
            p = prev.get(sid)
            if not p:
                continue
            if c > p:
                n_up += 1
            elif c < p:
                n_down += 1

    inst_total = session.execute(
        select(models.InstitutionalMarketTotal.total_net)
        .order_by(models.InstitutionalMarketTotal.date.desc()).limit(1)
    ).scalar_one_or_none()

    # 今日通過雙軌篩選的檔數（只給數字，名單在登入牆後）
    passed = dict(session.execute(
        select(models.Score.track, func.count()).where(
            models.Score.date == d0, models.Score.passed.is_(True))
        .group_by(models.Score.track)
    ).all())
    n_wave, n_long = passed.get("wave", 0), passed.get("long", 0)

    sector_rows = session.execute(
        select(models.Sector.name, models.SectorDaily.strength_score,
               models.SectorDaily.momentum_5, models.SectorDaily.trend_short,
               models.SectorDaily.rotation_stage)
        .join(models.Sector, models.Sector.id == models.SectorDaily.sector_id)
        .where(models.SectorDaily.date == d0)
        .order_by(models.SectorDaily.strength_score.desc())
    ).all()
    sectors = [{"name": n, "score": sc, "mom": m, "trend": t, "stage": st}
               for n, sc, m, t, st in sector_rows]

    news = [
        {"stock_id": sid, "stock_name": sname, "title": title,
         "category": cat, "is_risk": risk}
        for sid, sname, title, cat, risk in session.execute(
            select(models.Event.stock_id, models.Stock.name, models.Event.title,
                   models.Event.category, models.Event.is_risk)
            .join(models.Stock, models.Stock.id == models.Event.stock_id, isouter=True)
            .order_by(models.Event.date.desc(), models.Event.is_risk.desc(),
                      models.Event.id.desc())
            .limit(8)
        ).all()
    ]

    n_stocks = session.execute(
        select(func.count()).select_from(models.Stock)).scalar_one()

    return _templates.TemplateResponse(request, "home.html", {
        "cutoff": d0.isoformat(),
        "idx_close": idx_close, "idx_chg": idx_chg,
        "turnover_total": turnover_total,
        "n_up": n_up, "n_down": n_down, "inst_total": inst_total,
        "n_passed": n_wave + n_long, "n_wave": n_wave, "n_long": n_long,
        "sectors": sectors, "news": news, "n_stocks": n_stocks,
    })
