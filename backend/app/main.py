"""FastAPI 入口（P0 最小版）。

P0 只放「驗證地基」需要的端點：系統狀態、來源 health / 測試連線、手動跑
pipeline。完整 ⑥ API 層（6 頁讀寫端點、SSE 助手）於 P1+ 逐步加。
本機單人跑 → CORS 只開 localhost、無登入。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import BackgroundTasks, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func, select

from .api.routes import router as api_router
from .api.routes_assistant import router as assistant_router
from .api.routes_holdings import router as holdings_router
from .api.routes_intel import router as intel_router
from .api.routes_overview import router as overview_router
from .api.routes_sectors import router as sectors_router
from .api.routes_settings import router as settings_router
from .api.routes_watchlists import router as watchlists_router
from .config import settings
from .credentials import set_token
from .sources import registry
from .storage import models
from .storage.database import init_db, session_scope

app = FastAPI(title="TWAssistant", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(holdings_router)
app.include_router(sectors_router)
app.include_router(overview_router)
app.include_router(intel_router)
app.include_router(watchlists_router)
app.include_router(settings_router)
app.include_router(assistant_router)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    # 後端常開時的內建排程：啟動 catch-up + 每日 21:30 自動載入。
    from .scheduler.service import get_scheduler

    get_scheduler().start()


@app.on_event("shutdown")
def _shutdown() -> None:
    from .scheduler.service import get_scheduler

    get_scheduler().shutdown()


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/system/status")
def system_status() -> dict:
    tables = {
        "stocks": models.Stock,
        "sectors": models.Sector,
        "daily_prices": models.DailyPrice,
        "institutional": models.Institutional,
        "margin": models.Margin,
        "shareholding": models.ShareholdingDistribution,
        "revenue_monthly": models.RevenueMonthly,
        "valuation": models.Valuation,
    }
    with session_scope() as s:
        counts = {
            name: s.execute(select(func.count()).select_from(m)).scalar_one()
            for name, m in tables.items()
        }
        last_run = (
            s.query(models.PipelineRun).order_by(models.PipelineRun.id.desc()).first()
        )
        last = (
            {
                "trading_date": last_run.trading_date.isoformat() if last_run.trading_date else None,
                "status": last_run.status,
                "finished_at": last_run.finished_at.isoformat() if last_run.finished_at else None,
                "steps": last_run.steps,
            }
            if last_run
            else None
        )
    from .scheduler.service import is_running

    return {
        "db": settings.db_filename,
        "counts": counts,
        "last_pipeline_run": last,
        "pipeline_running": is_running(),
    }


@app.get("/sources")
def sources_health() -> list[dict]:
    return [src.health() for src in registry.all_sources().values()]


class TokenBody(BaseModel):
    token: str | None = None
    save: bool = False  # 測通後是否寫入 Keychain


@app.post("/sources/{name}/test")
def test_source(name: str, body: TokenBody) -> dict:
    """設定頁[測試連線]：可先測再存（測通才存）。"""
    try:
        src = registry.get_source(name)
    except KeyError:
        return {"ok": False, "reason": f"未知來源：{name}"}
    result = src.test(body.token)
    if result["ok"] and body.save and body.token:
        set_token(name, body.token)
        registry.reset()
    return result


@app.post("/pipeline/run")
def trigger_pipeline(background: BackgroundTasks) -> dict:
    """設定頁[立即載入]：背景補齊「所有缺的交易日（含分數）」到最新。

    立即回 accepted；已在跑則回 already_running。target＝目前理應已完成的最近交易日
    （盤前/未到排程時間 → 上一交易日，不抓還沒齊的當天）。
    """
    from .scheduler.service import _current_sched_time, _expected_ready_date, backfill_to_latest, is_running

    target = _expected_ready_date(datetime.now(), _current_sched_time())
    if is_running():
        return {"accepted": False, "reason": "already_running", "trading_date": target.isoformat()}
    background.add_task(backfill_to_latest, trigger="manual")
    return {"accepted": True, "trading_date": target.isoformat()}
