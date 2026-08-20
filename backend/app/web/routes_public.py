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
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..api.deps import get_session
from ..engines.fear_greed import compute_fear_greed, label_of
from ..engines.signal_log import LISTED
from ..llm.store import cache_key, get_cached
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
        select(models.Sector.id, models.Sector.name, models.SectorDaily.strength_score,
               models.SectorDaily.momentum_5, models.SectorDaily.trend_short,
               models.SectorDaily.rotation_stage)
        .join(models.Sector, models.Sector.id == models.SectorDaily.sector_id)
        .where(models.SectorDaily.date == d0)
        .order_by(models.SectorDaily.strength_score.desc())
    ).all()
    sectors = [{"id": i, "name": n, "score": sc, "mom": m, "trend": t, "stage": st}
               for i, n, sc, m, t, st in sector_rows]

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


# ─────────────────────────── 每日盤後 ───────────────────────────


def _trading_dates(session: Session) -> list[date]:
    """全部有行情的交易日（升冪）。翻頁與 10 日結算都要靠這條時間軸。"""
    return list(session.execute(
        select(models.DailyPrice.date).distinct().order_by(models.DailyPrice.date)
    ).scalars().all())


@router.get("/daily", response_class=HTMLResponse)
def daily_latest(session: Session = Depends(get_session)) -> RedirectResponse:
    """無日期＝最新交易日。302 而非直接渲染：讓每一天都有唯一網址（電子報連結用）。"""
    dates = _latest_dates(session, 1)
    if not dates:
        raise HTTPException(404, "尚無資料")
    return RedirectResponse(f"/daily/{dates[0].isoformat()}", status_code=302)


@router.get("/daily/{day}", response_class=HTMLResponse)
def daily(day: str, request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    """每日盤後頁：某交易日的完整快照，兼電子報主體。

    與首頁的分工：首頁永遠是「最新」，這裡是「那一天」——內容凍結、網址永久，
    可以被連結與引用。守同一條內容邊界：上榜/掉榜只給家數不給名單。
    """
    try:
        d0 = date.fromisoformat(day)
    except ValueError:
        raise HTTPException(404, "日期格式錯誤") from None

    all_dates = _trading_dates(session)
    if d0 not in all_dates:
        raise HTTPException(404, "該日無行情資料")
    i = all_dates.index(d0)
    prev_day = all_dates[i - 1] if i > 0 else None
    next_day = all_dates[i + 1] if i + 1 < len(all_dates) else None

    # 大盤：加權指數取 ≤ 當日的兩筆自算漲跌（index 表偶有缺日，不能硬對）
    idx = session.execute(
        select(models.MarketIndex.close).where(models.MarketIndex.date <= d0)
        .order_by(models.MarketIndex.date.desc()).limit(2)
    ).scalars().all()
    idx_close = idx[0] if idx else None
    idx_chg = (idx[0] / idx[1] - 1) * 100 if len(idx) == 2 and idx[1] else None

    turnover_total = session.execute(
        select(func.sum(models.DailyPrice.turnover)).where(models.DailyPrice.date == d0)
    ).scalar_one()
    turnover_total = turnover_total / 1e8 if turnover_total else None

    n_up = n_down = 0
    if prev_day is not None:
        prev = dict(session.execute(
            select(models.DailyPrice.stock_id, models.DailyPrice.close)
            .where(models.DailyPrice.date == prev_day, models.DailyPrice.close.is_not(None))
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

    inst = session.get(models.InstitutionalMarketTotal, d0)

    passed = dict(session.execute(
        select(models.Score.track, func.count()).where(
            models.Score.date == d0, models.Score.passed.is_(True))
        .group_by(models.Score.track)
    ).all())

    # 上榜/掉榜家數（signal_log；只給數字，名單在登入牆後）
    changes = {(k, t): n for k, t, n in session.execute(
        select(models.SignalLog.kind, models.SignalLog.track, func.count())
        .where(models.SignalLog.date == d0)
        .group_by(models.SignalLog.kind, models.SignalLog.track)
    ).all()}

    sector_rows = session.execute(
        select(models.Sector.id, models.Sector.name, models.SectorDaily.strength_score,
               models.SectorDaily.momentum_5, models.SectorDaily.trend_short,
               models.SectorDaily.rotation_stage)
        .join(models.Sector, models.Sector.id == models.SectorDaily.sector_id)
        .where(models.SectorDaily.date == d0)
        .order_by(models.SectorDaily.strength_score.desc())
    ).all()
    sectors = [{"id": i, "name": n, "score": sc, "mom": m, "trend": t, "stage": st}
               for i, n, sc, m, t, st in sector_rows]

    news = [
        {"stock_id": sid, "stock_name": sname, "title": title,
         "category": cat, "is_risk": risk}
        for sid, sname, title, cat, risk in session.execute(
            select(models.Event.stock_id, models.Stock.name, models.Event.title,
                   models.Event.category, models.Event.is_risk)
            .join(models.Stock, models.Stock.id == models.Event.stock_id, isouter=True)
            .where(models.Event.date == d0)
            .order_by(models.Event.is_risk.desc(), models.Event.id.desc())
            .limit(12)
        ).all()
    ]

    # 盤後總評（每日批次 LLM 快取；沒有就整段不顯示）
    commentary = get_cached(session, cache_key("news_market", "tw", d0))

    # 恐懼貪婪：併在盤後頁而非獨立頁（情緒是盤後快照的一部分）。
    # 該日分數從引擎的歷史序列查——翻舊日期也看得到當天的情緒；
    # 組件明細引擎只算最新一天，僅當日頁顯示。
    fg = compute_fear_greed(session)
    fg_score = next((h["score"] for h in fg["history"] if h["date"] == d0), None)
    fg_points = ""
    hist = fg["history"]
    if len(hist) >= 2:
        w, h = 640, 140
        xs = w / (len(hist) - 1)
        fg_points = " ".join(
            f"{j * xs:.1f},{h - p['score'] / 100 * h:.1f}" for j, p in enumerate(hist))

    return _templates.TemplateResponse(request, "daily.html", {
        "cutoff": d0.isoformat(), "day": d0,
        "prev_day": prev_day, "next_day": next_day,
        "idx_close": idx_close, "idx_chg": idx_chg,
        "turnover_total": turnover_total, "n_up": n_up, "n_down": n_down,
        "inst": inst,
        "n_wave": passed.get("wave", 0), "n_long": passed.get("long", 0),
        "n_listed": changes.get(("listed", "wave"), 0) + changes.get(("listed", "long"), 0),
        "n_delisted": changes.get(("delisted", "wave"), 0) + changes.get(("delisted", "long"), 0),
        "sectors": sectors, "news": news,
        "commentary": [p for p in commentary.splitlines() if p.strip()] if commentary else None,
        "fg_score": fg_score,
        "fg_label": label_of(fg_score) if fg_score is not None else None,
        "fg_components": fg["components"] if fg["date"] == d0 else [],
        "fg_points": fg_points,
        "fg_hist_from": hist[0]["date"].isoformat() if hist else None,
    })


# ─────────────────────────── 戰績 ───────────────────────────


@router.get("/track-record", response_class=HTMLResponse)
def track_record(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    """公開戰績（波段軌）：10 日內高點碰 +10% 的命中率＋10 日報酬對大盤超額。

    誠實三原則（此頁的存在理由，順序不可換）：
    1. 只有 backfilled=False 的段落能宣稱「事前說了」——那是當天寫下、append-only
       不可改寫的紀錄；回填段是事後由 scores 回推，一律另列並標明「背景參考」。
    2. 名單只揭「已結算」（訊號日之後滿 10 個交易日）的事件——還在跑的訊號仍屬
       建議，落在登入牆後，這裡只給結算中家數。
    3. 主打相對大盤超額而非絕對命中率——絕對數字是行情的函數（多頭年 70% 誰都有），
       超額才是可跨行情比較的量（見挖掘結論：穩定的是 +31~40pp）。
    """
    all_dates = _trading_dates(session)
    pos = {d: i for i, d in enumerate(all_dates)}

    signals = session.execute(
        select(models.SignalLog, models.Stock.name)
        .join(models.Stock, models.Stock.id == models.SignalLog.stock_id)
        .where(models.SignalLog.kind == LISTED, models.SignalLog.track == "wave")
        .order_by(models.SignalLog.date.desc())
    ).all()

    idx_close = dict(session.execute(
        select(models.MarketIndex.date, models.MarketIndex.close)
        .where(models.MarketIndex.close.is_not(None))
    ).all())

    # 批次撈受影響個股的後續行情：一次查回來建 dict，避免每訊號各查一次
    prices: dict[tuple[str, date], tuple[float | None, float | None]] = {}
    if signals:
        affected = {s.stock_id for s, _ in signals}
        min_d = min(s.date for s, _ in signals)
        prices = {
            (sid, d): (hi, cl)
            for sid, d, hi, cl in session.execute(
                select(models.DailyPrice.stock_id, models.DailyPrice.date,
                       models.DailyPrice.high, models.DailyPrice.close)
                .where(models.DailyPrice.stock_id.in_(affected),
                       models.DailyPrice.date >= min_d)
            ).all()
        }

    settled: dict[bool, list[dict]] = {True: [], False: []}
    pending = 0
    for sig, name in signals:
        entry = (sig.payload or {}).get("close") or (prices.get((sig.stock_id, sig.date)) or (None, None))[1]
        i = pos.get(sig.date)
        if not entry or i is None:
            continue
        window = all_dates[i + 1: i + 11]
        rows = [prices.get((sig.stock_id, d)) for d in window]
        rows = [r for r in rows if r and r[1] is not None]
        if len(window) < 10 or not rows:
            if not sig.backfilled:
                pending += 1
            continue
        peak = max((hi for hi, _ in rows if hi is not None), default=None)
        ret10 = (rows[-1][1] / entry - 1) * 100
        i0, i10 = idx_close.get(sig.date), idx_close.get(window[-1])
        mkt10 = (i10 / i0 - 1) * 100 if i0 and i10 else None
        settled[bool(sig.backfilled)].append({
            "date": sig.date, "stock_id": sig.stock_id, "name": name,
            "entry": entry,
            "hit": peak is not None and peak >= entry * 1.10,
            "peak_pct": (peak / entry - 1) * 100 if peak else None,
            "ret10": ret10, "mkt10": mkt10,
            "excess": ret10 - mkt10 if mkt10 is not None else None,
        })

    def _summary(rows: list[dict]) -> dict | None:
        if not rows:
            return None
        ex = [r["excess"] for r in rows if r["excess"] is not None]
        return {
            "n": len(rows),
            "hit_rate": sum(r["hit"] for r in rows) / len(rows) * 100,
            "avg_ret": sum(r["ret10"] for r in rows) / len(rows),
            "avg_mkt": sum(r["mkt10"] for r in rows if r["mkt10"] is not None) / len(ex) if ex else None,
            "avg_excess": sum(ex) / len(ex) if ex else None,
        }

    live, back = settled[False], settled[True]
    return _templates.TemplateResponse(request, "track_record.html", {
        "cutoff": all_dates[-1].isoformat() if all_dates else "尚無資料",
        "live_summary": _summary(live), "live_rows": live[:100],
        "back_summary": _summary(back),
        "pending": pending,
    })


# ─────────────────────────── 除權息 ───────────────────────────


@router.get("/dividends", response_class=HTMLResponse)
def dividends(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    dates = _latest_dates(session, 1)
    d0 = dates[0] if dates else None
    today = date.today()

    rows = session.execute(
        select(models.Dividend, models.Stock.name, models.DailyPrice.close)
        .join(models.Stock, models.Stock.id == models.Dividend.stock_id)
        .join(models.DailyPrice,
              (models.DailyPrice.stock_id == models.Dividend.stock_id)
              & (models.DailyPrice.date == d0), isouter=True)
        .where(models.Dividend.cash_ex_date >= today - timedelta(days=60))
        .order_by(models.Dividend.cash_ex_date)
    ).all()

    def _item(dv: models.Dividend, name: str, close: float | None) -> dict:
        return {
            "stock_id": dv.stock_id, "name": name, "period": dv.period,
            "cash": dv.cash, "stock": dv.stock,
            "ex_date": dv.cash_ex_date, "pay_date": dv.pay_date, "close": close,
            "yield_pct": dv.cash / close * 100 if dv.cash and close else None,
        }

    upcoming = [_item(*r) for r in rows if r[0].cash_ex_date >= today]
    recent = [_item(*r) for r in reversed(rows) if r[0].cash_ex_date < today]

    return _templates.TemplateResponse(request, "dividends.html", {
        "cutoff": d0.isoformat() if d0 else "尚無資料",
        "today": today.isoformat(),
        "upcoming": upcoming[:80], "recent": recent[:80],
    })


# ─────────────────────────── 類股 ───────────────────────────


@router.get("/sectors", response_class=HTMLResponse)
def sectors_index(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    """類股行情總表（畫面地圖的樞紐頁之一：既有會員版「開放匿名」的公開版）。

    內容邊界：SectorDaily 全是統計計算（動能/廣度/成交佔比），屬客觀資料；
    首頁本來就公開展示強弱分數，這裡只是給完整表格＋通往成分股的內鏈。
    """
    d = session.execute(select(func.max(models.SectorDaily.date))).scalar()
    if d is None:
        return _templates.TemplateResponse(request, "sectors.html",
                                           {"cutoff": "尚無資料", "rows": []})
    rows = [
        {"id": sd.sector_id, "name": name, "score": sd.strength_score,
         "trend_short": sd.trend_short, "trend_long": sd.trend_long,
         "stage": sd.rotation_stage, "mom5": sd.momentum_5, "mom20": sd.momentum_20,
         "turnover_share": sd.turnover_share, "above_ma20": sd.above_ma20,
         "foreign_net": sd.foreign_net, "constituents": sd.constituents,
         "foreign_disp": f"{sd.foreign_net:+,}" if sd.foreign_net is not None else None}
        for sd, name in session.execute(
            select(models.SectorDaily, models.Sector.name)
            .join(models.Sector, models.Sector.id == models.SectorDaily.sector_id)
            .where(models.SectorDaily.date == d)
            .order_by(models.SectorDaily.strength_score.desc())
        ).all()
    ]
    return _templates.TemplateResponse(
        request, "sectors.html", {"cutoff": d.isoformat(), "rows": rows})


@router.get("/sectors/{sector_id}", response_class=HTMLResponse)
def sector_page(sector_id: int, request: Request,
                session: Session = Depends(get_session)) -> HTMLResponse:
    """公開類股詳情：類股狀態磚＋成分股行情表（通往個股頁的內鏈，補齊冷門股連入）。"""
    sector = session.get(models.Sector, sector_id)
    if sector is None:
        raise HTTPException(404, "查無此類股")

    d = session.execute(select(func.max(models.SectorDaily.date))).scalar()
    sd = session.get(models.SectorDaily, (sector_id, d)) if d else None

    dates = _latest_dates(session, 2)
    d0 = dates[0] if dates else None
    prev = {}
    if len(dates) == 2:
        prev = dict(session.execute(
            select(models.DailyPrice.stock_id, models.DailyPrice.close)
            .where(models.DailyPrice.date == dates[1],
                   models.DailyPrice.close.is_not(None))
        ).all())

    stocks = []
    for sid, name, close, turnover in session.execute(
        select(models.Stock.id, models.Stock.name,
               models.DailyPrice.close, models.DailyPrice.turnover)
        .join(models.DailyPrice,
              (models.DailyPrice.stock_id == models.Stock.id)
              & (models.DailyPrice.date == d0), isouter=True)
        .where(models.Stock.sector_id == sector_id)
    ).all():
        p = prev.get(sid)
        stocks.append({
            "stock_id": sid, "name": name, "close": close,
            "chg": (close / p - 1) * 100 if close and p else None,
            "turnover": turnover / 1e8 if turnover else None,
        })
    stocks.sort(key=lambda s: -(s["turnover"] or 0))

    return _templates.TemplateResponse(request, "sector.html", {
        "cutoff": d0.isoformat() if d0 else "尚無資料",
        "sector": sector, "sd": sd, "stocks": stocks,
    })


# ─────────────────────────── 月營收 ───────────────────────────

_REV_FLOOR = 100_000  # 千元＝月營收 1 億；YoY/MoM 排行的入榜門檻，擋掉小基數暴衝


def _revenue_months(session: Session) -> list[tuple[int, int]]:
    """所有有營收資料的 (年, 月)，降冪。翻頁軸＋月份驗證共用。"""
    return [tuple(r) for r in session.execute(
        select(models.RevenueMonthly.year, models.RevenueMonthly.month).distinct()
        .order_by(models.RevenueMonthly.year.desc(), models.RevenueMonthly.month.desc())
    ).all()]


@router.get("/revenue", response_class=HTMLResponse)
def revenue_latest(session: Session = Depends(get_session)) -> RedirectResponse:
    """無月份＝最新。302 出唯一網址：每月 10 號的公布尖峰要有可累積的頁面。"""
    months = _revenue_months(session)
    if not months:
        raise HTTPException(404, "尚無資料")
    y, m = months[0]
    return RedirectResponse(f"/revenue/{y}/{m}", status_code=302)


@router.get("/revenue/{y}/{m}", response_class=HTMLResponse)
def revenue(y: int, m: int, request: Request,
            session: Session = Depends(get_session)) -> HTMLResponse:
    months = _revenue_months(session)
    if (y, m) not in months:
        raise HTTPException(404, "該月無營收資料")
    i = months.index((y, m))
    prev_ym = months[i + 1] if i + 1 < len(months) else None   # 降冪：往後是更舊
    next_ym = months[i - 1] if i > 0 else None

    rows = session.execute(
        select(models.RevenueMonthly, models.Stock.name)
        .join(models.Stock, models.Stock.id == models.RevenueMonthly.stock_id)
        .where(models.RevenueMonthly.year == y, models.RevenueMonthly.month == m,
               models.RevenueMonthly.revenue.is_not(None))
    ).all()

    def _item(r: models.RevenueMonthly, name: str, value: float, display: str) -> dict:
        return {"stock_id": r.stock_id, "name": name, "revenue": r.revenue,
                "rev_disp": f"{r.revenue / 1e5:,.1f}",
                "yoy": r.yoy, "mom": r.mom, "value": value, "display": display}

    big = [(r, n) for r, n in rows if r.revenue >= _REV_FLOOR]
    yoy = sorted((_item(r, n, r.yoy, f"{r.yoy:+.1f}%") for r, n in big if r.yoy is not None),
                 key=lambda x: -x["value"])
    mom = sorted((_item(r, n, r.mom, f"{r.mom:+.1f}%") for r, n in big if r.mom is not None),
                 key=lambda x: -x["value"])
    scale = sorted((_item(r, n, r.revenue,
                          f"{r.yoy:+.1f}%" if r.yoy is not None else "—") for r, n in rows),
                   key=lambda x: -x["value"])

    # 創歷史新高：當月營收 ≥ 自身全史最大值（同值視為新高）
    # 「歷史」只到該月為止——翻舊月份時不能拿未來的營收當分母
    hist_max = dict(session.execute(
        select(models.RevenueMonthly.stock_id, func.max(models.RevenueMonthly.revenue))
        .where(models.RevenueMonthly.year * 100 + models.RevenueMonthly.month <= y * 100 + m)
        .group_by(models.RevenueMonthly.stock_id)
    ).all())
    highs = sorted(
        (_item(r, n, r.revenue, f"{r.revenue / 1e5:,.1f}")
         for r, n in rows if r.revenue >= (hist_max.get(r.stock_id) or 0)),
        key=lambda x: -(x["yoy"] or 0))

    return _templates.TemplateResponse(request, "revenue.html", {
        "cutoff": f"{y} 年 {m} 月",
        "ym": (y, m), "n_reported": len(rows),
        "prev_ym": prev_ym, "next_ym": next_ym,
        "boards": [
            {"title": "年增率排行", "value_label": "YoY", "rows": yoy[:_TOP_N], "signed": True},
            {"title": "月增率排行", "value_label": "MoM", "rows": mom[:_TOP_N], "signed": True},
            {"title": "營收規模排行", "value_label": "YoY", "rows": scale[:_TOP_N], "signed": False},
        ],
        "highs": highs[:30],
    })
