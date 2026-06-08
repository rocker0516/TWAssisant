"""FastAPI 入口（P0 最小版）。

P0 只放「驗證地基」需要的端點：系統狀態、來源 health / 測試連線、手動跑
pipeline。完整 ⑥ API 層（6 頁讀寫端點、SSE 助手）於 P1+ 逐步加。
本機單人跑 → CORS 只開 localhost、無登入。
"""

from __future__ import annotations

from datetime import date

from fastapi import BackgroundTasks, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func, select

from .api.routes import router as api_router
from .api.routes_holdings import router as holdings_router
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


@app.on_event("startup")
def _startup() -> None:
    init_db()


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
    return {"db": settings.db_filename, "counts": counts, "last_pipeline_run": last}


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


def _run_pipeline(target: date) -> None:
    from .scheduler.run import build_pipeline

    build_pipeline().run(target)


@app.post("/pipeline/run")
def trigger_pipeline(background: BackgroundTasks) -> dict:
    """手動重跑（設定頁 / 補跑）。背景執行，立即回 accepted。"""
    from .scheduler.trading_calendar import resolve_trading_date

    target = resolve_trading_date(date.today())
    background.add_task(_run_pipeline, target)
    return {"accepted": True, "trading_date": target.isoformat()}
