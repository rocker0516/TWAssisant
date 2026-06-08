"""浮動助手 + 個股健檢的事實組裝（架構④ ask-only / AssistantAgent）。

接地原則：助手只依『App 內部已算好的結論』回答，不編造、不引用外部即時資訊。
context{page,stock_id?,sector_id?} → 後端組 facts 注入 system。健檢為 ask-only 串流。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..storage import models
from .translators import _level, _mom, _net

ASSISTANT_SYSTEM_BASE = """你是台股操作助手 App 內的 AI 助手。請只根據下方『App 內部已經算好的結論』回答使用者，不要編造數字，也不要引用外部即時行情或新聞。嚴守規範：只談方向（偏多／偏空／中性）與觀察點、不要說買進或賣出、結尾附一句免責。使用繁體中文，回答精簡、貼合使用者問題。"""


def _market_date(session: Session) -> date | None:
    return session.execute(select(func.max(models.DailyPrice.date))).scalar()


def _pe_level(pe: float | None) -> str:
    if pe is None or pe <= 0:
        return "未知"
    return "偏低" if pe < 15 else "偏高" if pe > 25 else "合理"


def health_facts(session: Session, stock_id: str, td: date) -> dict | None:
    stock = session.get(models.Stock, stock_id)
    if stock is None:
        return None
    w = session.get(models.Score, {"stock_id": stock_id, "date": td, "track": "wave"})
    ll = session.get(models.Score, {"stock_id": stock_id, "date": td, "track": "long"})
    inst = session.execute(
        select(models.Institutional).where(models.Institutional.stock_id == stock_id)
        .order_by(models.Institutional.date.desc()).limit(1)
    ).scalars().first()
    rev = session.execute(
        select(models.RevenueMonthly).where(models.RevenueMonthly.stock_id == stock_id)
        .order_by(models.RevenueMonthly.year.desc(), models.RevenueMonthly.month.desc()).limit(1)
    ).scalars().first()
    val = session.execute(
        select(models.Valuation).where(models.Valuation.stock_id == stock_id)
        .order_by(models.Valuation.date.desc()).limit(1)
    ).scalars().first()
    sd = None
    if stock.sector_id:
        sd = session.execute(
            select(models.SectorDaily).where(models.SectorDaily.sector_id == stock.sector_id)
            .order_by(models.SectorDaily.date.desc()).limit(1)
        ).scalars().first()
    has_risk = session.execute(
        select(func.count()).select_from(models.Event)
        .where(models.Event.stock_id == stock_id, models.Event.is_risk.is_(True))
    ).scalar_one() > 0

    return {
        "name": stock.name,
        "wave_level": _level(w.total_score) if w else "未知",
        "wave_passed": bool(w and w.passed),
        "long_level": _level(ll.total_score) if ll else "未知",
        "long_passed": bool(ll and ll.passed),
        "chip_net": (inst.foreign_net or 0) + (inst.trust_net or 0) if inst else None,
        "revenue_trend": _mom(rev.yoy) if rev else "未知",
        "pe_level": _pe_level(val.pe if val else None),
        "sector_trend": (sd.trend_short or "未知") if sd else "未知",
        "has_risk": has_risk,
    }


def context_facts(session: Session, context: dict) -> str:
    """依情境組可用事實（接地）。"""
    td = _market_date(session)
    lines: list[str] = []
    if not td:
        return "（目前尚無盤後資料）"

    sid = context.get("stock_id")
    if sid:
        hf = health_facts(session, sid, td)
        if hf:
            lines.append(
                f"使用者正在看個股「{hf['name']}」：波段評分定位{hf['wave_level']}"
                f"（{'達門檻' if hf['wave_passed'] else '未達門檻'}）、長線定位{hf['long_level']}"
                f"（{'達門檻' if hf['long_passed'] else '未達門檻'}）；法人{_net(hf['chip_net'])}；"
                f"月營收趨勢{hf['revenue_trend']}；估值{hf['pe_level']}；所屬類股方向{hf['sector_trend']}；"
                f"近期重大利空：{'有' if hf['has_risk'] else '無'}。"
            )

    sec_id = context.get("sector_id")
    if sec_id:
        row = session.execute(
            select(models.SectorDaily, models.Sector.name)
            .join(models.Sector, models.SectorDaily.sector_id == models.Sector.id)
            .where(models.SectorDaily.sector_id == sec_id, models.SectorDaily.date == td)
        ).first()
        if row:
            sd, name = row
            lines.append(
                f"使用者正在看類股「{name}」：強弱{_level(sd.strength_score)}、"
                f"短波段{sd.trend_short}、中長期{sd.trend_long}、輪動階段{sd.rotation_stage}。"
            )

    # 持股概況（read-only）
    open_n = session.execute(
        select(func.count()).select_from(models.Holding).where(models.Holding.status == "open")
    ).scalar_one()
    if open_n:
        lines.append(f"使用者目前持有 {open_n} 檔股票（持股的出場狀態與訊號已由系統算好）。")

    wave_n = session.execute(
        select(func.count()).select_from(models.Score)
        .where(models.Score.track == "wave", models.Score.date == td, models.Score.passed.is_(True))
    ).scalar_one()
    lines.append(f"今日波段軌達門檻推薦 {wave_n} 檔。")
    return "\n".join(lines) if lines else "（目前情境無特定個股／類股）"


def assistant_system(session: Session, context: dict) -> str:
    return f"{ASSISTANT_SYSTEM_BASE}\n\n[目前情境與可用事實]\n{context_facts(session, context)}"
