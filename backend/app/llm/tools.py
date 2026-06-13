"""助手工具集（架構④ 進階：tool-use 接地查詢）。

讓 AI 助手在對話中『自己決定要查什麼』，而不是後端一次塞滿、或叫使用者去某頁看。
每個工具都是『唯讀』查 App 自己的 SQLite，回傳的是 App 已算好的『質化結論』
（強/弱、偏多/偏空、買超/賣超、燈號、評分定位、買區/停損），不上網、不吐原始行情財報。
接地鐵律不變：只翻譯已算好的結論，不編造、不喊單。
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..engines.exit_engine import ExitEngine
from ..services.holding_service import HoldingService
from ..storage import models
from .assistant import _LIGHT, _market_date, health_facts
from .translators import _level, _mom, _net

_exit = ExitEngine()
_holding = HoldingService()

# ─────────────────────────── 工具 schema（給 Claude 看的目錄）───────────────────────────

ASSISTANT_TOOLS: list[dict] = [
    {
        "name": "find_stock",
        "description": "以股票名稱、部分名稱或代號查出對應的股票代號（stock_id）。"
                       "使用者用中文名稱問某檔股票時，先用這個查到代號，再用 stock_detail 查細節。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "股票名稱、部分名稱或代號，如『台積電』『2330』『金control』"}
            },
            "required": ["query"],
        },
    },
    {
        "name": "stock_detail",
        "description": "查一檔個股或 ETF 的完整健檢結論：雙軌評分定位、技術面姿態、法人籌碼、"
                       "月營收/估值、季財報、融資券、所屬類股方向、買區/停損、評分理由、近期利空。"
                       "需要 stock_id（先用 find_stock 取得）。",
        "input_schema": {
            "type": "object",
            "properties": {"stock_id": {"type": "string", "description": "股票代號，如 2330"}},
            "required": ["stock_id"],
        },
    },
    {
        "name": "sector_detail",
        "description": "查某一類股的方向結論：強弱定位、短/中長期方向、輪動階段、三維度（動能/資金/技術）、法人動向。",
        "input_schema": {
            "type": "object",
            "properties": {"sector": {"type": "string", "description": "類股名稱或 sector_id，如『半導體』『24』"}},
            "required": ["sector"],
        },
    },
    {
        "name": "list_recommendations",
        "description": "列出今日進場推薦（達門檻）標的，含評分定位與建議買區。可指定軌道。",
        "input_schema": {
            "type": "object",
            "properties": {
                "track": {"type": "string", "enum": ["wave", "long"],
                          "description": "wave=波段軌、long=長線軌；不填則兩軌都列"}
            },
        },
    },
    {
        "name": "my_portfolio",
        "description": "查使用者目前的持股（含出場燈號與損益方向）與觀察清單。",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "recent_news",
        "description": "查近期重大訊息/新聞事件。給 stock_id 則查該股；不給則查全市場近期利空與題材叢集。",
        "input_schema": {
            "type": "object",
            "properties": {
                "stock_id": {"type": "string", "description": "個股代號（可選）"},
                "days": {"type": "integer", "description": "回溯天數，預設 14"},
            },
        },
    },
]


# ─────────────────────────── 質化 helper ───────────────────────────


def _tech_posture(ind: models.Indicator | None, close: float | None) -> str:
    if ind is None:
        return "未知"
    parts: list[str] = []
    if ind.ma20 and close:
        parts.append("站上月線" if close >= ind.ma20 else "跌破月線")
    if ind.ma60 and close:
        parts.append("站上季線" if close >= ind.ma60 else "跌破季線")
    if ind.kd_k is not None and ind.kd_d is not None:
        tag = "（高檔）" if ind.kd_k > 80 else "（低檔）" if ind.kd_k < 20 else ""
        parts.append(("KD 偏多" if ind.kd_k >= ind.kd_d else "KD 偏空") + tag)
    if ind.macd_hist is not None:
        parts.append("MACD 柱狀偏多" if ind.macd_hist > 0 else "MACD 柱狀偏空")
    return "、".join(parts) if parts else "未知"


def _margin_trend(session: Session, stock_id: str) -> str:
    rows = session.execute(
        select(models.Margin.margin_change).where(models.Margin.stock_id == stock_id)
        .order_by(models.Margin.date.desc()).limit(5)
    ).scalars().all()
    chg = [c for c in rows if c is not None]
    if not chg:
        return "未知"
    s = sum(chg)
    return "融資近期增加（散戶加碼）" if s > 0 else "融資近期減少（散戶退場）" if s < 0 else "融資持平"


def _financial_note(fq: models.FinancialQuarter | None) -> str:
    if fq is None:
        return "未知"
    eps = "EPS 為正" if (fq.eps or 0) > 0 else "EPS 為負" if fq.eps is not None else "EPS 未知"
    if fq.roe is None:
        roe = ""
    else:
        roe = "、ROE 偏高" if fq.roe >= 15 else "、ROE 偏低" if fq.roe < 5 else "、ROE 中等"
    return f"{fq.year}Q{fq.quarter}：{eps}{roe}"


def _score_anchor(session: Session, stock_id: str, td: date) -> dict | None:
    """波段軌（無則長線軌）的買區/停損/理由 chips。"""
    for track in ("wave", "long"):
        sc = session.get(models.Score, {"stock_id": stock_id, "date": td, "track": track})
        if sc and (sc.buy_low or sc.reasons):
            return {
                "track": "波段軌" if track == "wave" else "長線軌",
                "buy_low": sc.buy_low, "buy_high": sc.buy_high,
                "stop_loss": sc.stop_loss, "reasons": sc.reasons or [],
            }
    return None


# ─────────────────────────── 工具實作 ───────────────────────────


def _t_find_stock(session: Session, q: str) -> str:
    q = (q or "").strip()
    if not q:
        return "（請提供名稱或代號）"
    rows = session.execute(
        select(models.Stock.id, models.Stock.name, models.Sector.name)
        .outerjoin(models.Sector, models.Stock.sector_id == models.Sector.id)
        .where(models.Stock.id == q)
    ).all()
    if not rows:
        rows = session.execute(
            select(models.Stock.id, models.Stock.name, models.Sector.name)
            .outerjoin(models.Sector, models.Stock.sector_id == models.Sector.id)
            .where(models.Stock.name.like(f"%{q}%")).limit(8)
        ).all()
    if not rows:
        return f"找不到符合「{q}」的標的。"
    return "符合的標的：" + "；".join(
        f"{sid} {name}（{sec or '未分類'}）" for sid, name, sec in rows
    )


def _t_stock_detail(session: Session, stock_id: str) -> str:
    td = _market_date(session)
    if not td:
        return "（目前尚無盤後資料）"
    hf = health_facts(session, stock_id, td)
    if hf is None:
        return f"找不到股票 {stock_id} 或尚無資料。"

    lines = [f"【{hf['name']}（{stock_id}）】"]
    if hf.get("is_etf"):
        e = hf.get("etf") or {}
        lines.append(
            f"類型：ETF；追蹤 {e.get('track_index') or '主動式/未對應指數'}；"
            f"規模 {e.get('scale_label', '未知')}；"
            f"波段定位{hf['wave_level']}、長線定位{hf['long_level']}；法人{_net(hf['chip_net'])}；"
            f"所屬類股方向{hf['sector_trend']}；近期重大利空：{'有' if hf['has_risk'] else '無'}。"
            f"（ETF 無月營收/本益比）"
        )
    else:
        lines.append(
            f"波段評分定位{hf['wave_level']}（{'達門檻' if hf['wave_passed'] else '未達門檻'}）、"
            f"長線定位{hf['long_level']}（{'達門檻' if hf['long_passed'] else '未達門檻'}）。"
        )
        lines.append(
            f"法人籌碼{_net(hf['chip_net'])}；月營收趨勢{hf['revenue_trend']}；估值{hf['pe_level']}；"
            f"所屬類股方向{hf['sector_trend']}；近期重大利空：{'有' if hf['has_risk'] else '無'}。"
        )
        fq = session.execute(
            select(models.FinancialQuarter).where(models.FinancialQuarter.stock_id == stock_id)
            .order_by(models.FinancialQuarter.year.desc(), models.FinancialQuarter.quarter.desc()).limit(1)
        ).scalars().first()
        lines.append(f"季財報：{_financial_note(fq)}；籌碼面：{_margin_trend(session, stock_id)}。")

    # 技術姿態（個股 / ETF 皆適用）
    ind = session.execute(
        select(models.Indicator).where(models.Indicator.stock_id == stock_id)
        .order_by(models.Indicator.date.desc()).limit(1)
    ).scalars().first()
    close = session.execute(
        select(models.DailyPrice.close).where(models.DailyPrice.stock_id == stock_id)
        .order_by(models.DailyPrice.date.desc()).limit(1)
    ).scalar()
    lines.append(f"技術面：{_tech_posture(ind, close)}。")

    anchor = _score_anchor(session, stock_id, td)
    if anchor:
        if anchor["buy_low"] and anchor["buy_high"]:
            lines.append(
                f"{anchor['track']}參考買區 {anchor['buy_low']:.1f}~{anchor['buy_high']:.1f}"
                + (f"、停損約 {anchor['stop_loss']:.1f}" if anchor["stop_loss"] else "") + "。"
            )
        if anchor["reasons"]:
            lines.append("評分理由：" + "、".join(str(r) for r in anchor["reasons"][:5]) + "。")
    return "\n".join(lines)


def _t_sector_detail(session: Session, sector: str) -> str:
    td = _market_date(session)
    if not td:
        return "（目前尚無盤後資料）"
    q = (sector or "").strip()
    stmt = (
        select(models.SectorDaily, models.Sector.name)
        .join(models.Sector, models.SectorDaily.sector_id == models.Sector.id)
        .where(models.SectorDaily.date == td)
    )
    stmt = stmt.where(models.Sector.id == int(q)) if q.isdigit() else stmt.where(models.Sector.name.like(f"%{q}%"))
    row = session.execute(stmt.limit(1)).first()
    if not row:
        return f"找不到類股「{sector}」或尚無方向資料。"
    sd, name = row
    return (
        f"【{name}】強弱{_level(sd.strength_score)}；短波段{sd.trend_short}、中長期{sd.trend_long}；"
        f"輪動階段{sd.rotation_stage}；動能維度{_mom(sd.momentum_5)}（近5日）/{_mom(sd.momentum_20)}（近20日）；"
        f"法人近5日{_net(sd.foreign_net)}；站上月線家數比約 {sd.above_ma20:.0f}%。"
        if sd.above_ma20 is not None else
        f"【{name}】強弱{_level(sd.strength_score)}；短波段{sd.trend_short}、中長期{sd.trend_long}；"
        f"輪動階段{sd.rotation_stage}；法人近5日{_net(sd.foreign_net)}。"
    )


def _t_list_recommendations(session: Session, track: str | None) -> str:
    td = _market_date(session)
    if not td:
        return "（目前尚無盤後資料）"
    tracks = [(track, {"wave": "波段軌", "long": "長線軌"}[track])] if track in ("wave", "long") \
        else [("wave", "波段軌"), ("long", "長線軌")]
    parts: list[str] = []
    for tk, label in tracks:
        rows = session.execute(
            select(models.Stock.id, models.Stock.name, models.Score.total_score,
                   models.Score.buy_low, models.Score.buy_high)
            .join(models.Stock, models.Score.stock_id == models.Stock.id)
            .where(models.Score.track == tk, models.Score.date == td, models.Score.passed.is_(True))
            .order_by(models.Score.total_score.desc()).limit(8)
        ).all()
        if not rows:
            parts.append(f"{label}：今日無達門檻標的。")
            continue
        items = "；".join(
            f"{name}（{sid}，{_level(s)}" + (f"，買區 {bl:.1f}~{bh:.1f}" if bl and bh else "") + "）"
            for sid, name, s, bl, bh in rows
        )
        parts.append(f"{label}達門檻 {len(rows)} 檔（含以上）：{items}")
    return "\n".join(parts)


def _t_my_portfolio(session: Session) -> str:
    td = _market_date(session)
    if not td:
        return "（目前尚無盤後資料）"
    out: list[str] = []
    holds = session.execute(
        select(models.Holding).where(models.Holding.status == "open")
    ).scalars().all()
    hitems: list[str] = []
    for h in holds:
        pos = _holding.position(session, h)
        if pos.shares <= 0 or pos.avg_cost is None:
            continue
        close = session.execute(
            select(models.DailyPrice.close).where(models.DailyPrice.stock_id == h.stock_id, models.DailyPrice.date <= td)
            .order_by(models.DailyPrice.date.desc()).limit(1)
        ).scalar()
        if close is None:
            continue
        st = _exit.evaluate(session, h, td, avg_cost=pos.avg_cost, close=close)
        stock = session.get(models.Stock, h.stock_id)
        name = stock.name if stock else h.stock_id
        direction = "獲利中" if close >= pos.avg_cost else "虧損中"
        sig = ("，訊號：" + "、".join(st.signals[:2])) if st.signals else ""
        hitems.append(f"{name}（{_LIGHT.get(st.level, st.level)}、{direction}{sig}）")
    out.append("持股：" + ("；".join(hitems) if hitems else "目前無持股") + "。")

    witems = session.execute(
        select(models.Stock.name, models.Stock.id)
        .join(models.WatchlistItem, models.WatchlistItem.stock_id == models.Stock.id)
    ).all()
    if witems:
        out.append("觀察清單：" + "、".join(f"{n}（{i}）" for n, i in witems[:20]) + "。")
    else:
        out.append("觀察清單：目前為空。")
    return "\n".join(out)


def _t_recent_news(session: Session, stock_id: str | None, days: int | None) -> str:
    td = _market_date(session)
    if not td:
        return "（目前尚無盤後資料）"
    since = td - timedelta(days=days or 14)
    stmt = (
        select(models.Event, models.Stock.name)
        .outerjoin(models.Stock, models.Event.stock_id == models.Stock.id)
        .where(models.Event.date >= since)
    )
    if stock_id:
        stmt = stmt.where(models.Event.stock_id == stock_id)
    rows = session.execute(stmt.order_by(models.Event.date.desc()).limit(20)).all()
    if not rows:
        return "近期無顯著事件。"
    lines = []
    for ev, name in rows:
        star = "⚠️" if ev.is_risk else ""
        who = f"{name}｜" if name and not stock_id else ""
        lines.append(f"- {star}{who}[{ev.category or '中性'}] {ev.title}")
    return "近期事件：\n" + "\n".join(lines)


# ─────────────────────────── 分派 ───────────────────────────

_DISPATCH = {
    "find_stock": lambda s, a: _t_find_stock(s, a.get("query", "")),
    "stock_detail": lambda s, a: _t_stock_detail(s, a.get("stock_id", "")),
    "sector_detail": lambda s, a: _t_sector_detail(s, a.get("sector", "")),
    "list_recommendations": lambda s, a: _t_list_recommendations(s, a.get("track")),
    "my_portfolio": lambda s, a: _t_my_portfolio(s),
    "recent_news": lambda s, a: _t_recent_news(s, a.get("stock_id"), a.get("days")),
}


def run_tool(session: Session, name: str, tool_input: dict) -> str:
    """執行一個助手工具，回傳純文字結論。未知工具/例外都回可讀訊息（不讓串流中斷）。"""
    fn = _DISPATCH.get(name)
    if fn is None:
        return f"（未知工具 {name}）"
    try:
        return fn(session, tool_input or {})
    except Exception as exc:  # noqa: BLE001 — 工具錯誤回訊息，讓助手據此回覆而非整段崩潰
        return f"（查詢 {name} 時發生問題：{type(exc).__name__}；可改用其他方式回答）"
