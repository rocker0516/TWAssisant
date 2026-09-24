"""Discord 通知（架構⑤ NotifyStep）。

每日盤後組「持股提醒（🔴🟠 優先）＋推薦檔數」推播。webhook 未設定則略過。
"""

from __future__ import annotations

from datetime import date

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .credentials import get_discord_webhook
from .engines.exit_engine import ExitEngine, ExitStatus
from .services.holding_service import HoldingService
from .storage import models


def _latest_close(session: Session, stock_id: str, td: date) -> float | None:
    return session.execute(
        select(models.DailyPrice.close)
        .where(models.DailyPrice.stock_id == stock_id, models.DailyPrice.date <= td)
        .order_by(models.DailyPrice.date.desc()).limit(1)
    ).scalar()


def build_daily_message(session: Session, td: date) -> str | None:
    """組每日提醒訊息；無任何可報內容回 None。"""
    from .scheduler.steps import format_exit_lines  # 延遲匯入避免與 scheduler.steps 循環匯入

    svc, engine = HoldingService(), ExitEngine()
    rows: list[tuple[str, ExitStatus]] = []
    for h in session.execute(select(models.Holding).where(models.Holding.status == "open")).scalars().all():
        pos = svc.position(session, h)
        if pos.shares <= 0 or pos.avg_cost is None:
            continue
        close = _latest_close(session, h.stock_id, td)
        if close is None:
            continue
        st = engine.evaluate(session, h, td, avg_cost=pos.avg_cost, close=close)
        stock = session.get(models.Stock, h.stock_id)
        ret = (close / pos.avg_cost - 1) * 100
        label = f"{stock.name if stock else h.stock_id}（{ret:+.1f}%）"
        rows.append((label, st))
    alerts = format_exit_lines(rows)

    rec = {
        "wave": session.execute(
            select(func.count()).select_from(models.Score)
            .where(models.Score.track == "wave", models.Score.date == td,
                   models.Score.passed.is_(True))
        ).scalar_one()
    }

    # 籌碼異動（投信首買/借券暴增優先，最多 5 則；失敗不擋通知）
    chip_lines: list[str] = []
    try:
        from .engines.flow_engine import FlowEngine

        chip = FlowEngine().chip_alerts(session)
        picked = [i for i in chip["items"] if i["kind"] in ("trust_first_buy", "sbl_spike")][:5]
        chip_lines = [f"・{i['kind_label']} {i['name']}（{i['stock_id']}）：{i['detail']}" for i in picked]
    except Exception:  # noqa: BLE001 — 異動偵測掛了不影響主通知
        pass

    if not alerts and not any(rec.values()) and not chip_lines:
        return None
    lines = [f"📊 **TWAssistant 盤後提醒 {td}**", ""]
    if alerts:
        lines.append("**持股提醒**")
        lines.extend(alerts)
        lines.append("")
    if chip_lines:
        lines.append("**籌碼異動**")
        lines.extend(chip_lines)
        lines.append("")
    lines.append(f"**今日進場推薦**：波段 {rec['wave']} 檔")
    return "\n".join(lines)


def send_discord(content: str, webhook: str | None = None) -> bool:
    url = webhook or get_discord_webhook()
    if not url:
        return False
    resp = httpx.post(url, json={"content": content}, timeout=15)
    resp.raise_for_status()
    return True
