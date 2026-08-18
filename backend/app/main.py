"""FastAPI 入口（P0 最小版）。

P0 只放「驗證地基」需要的端點：系統狀態、來源 health / 測試連線、手動跑
pipeline。完整 ⑥ API 層（6 頁讀寫端點、SSE 助手）於 P1+ 逐步加。
本機單人跑 → CORS 只開 localhost、無登入。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, select

from . import auth
from .api.routes import router as api_router
from .api.routes_assistant import router as assistant_router
from .api.routes_auth import router as auth_router
from .api.routes_corners import router as corners_router
from .api.routes_flow import router as flow_router
from .api.routes_holdings import router as holdings_router
from .api.routes_intel import router as intel_router
from .api.routes_lab import router as lab_router
from .api.routes_overview import router as overview_router
from .api.routes_sectors import router as sectors_router
from .api.routes_settings import router as settings_router
from .api.routes_watchlists import router as watchlists_router
from .config import settings
from .credentials import set_token
from .sources import registry
from .web.routes_public import router as public_router
from .storage import models
from .storage.database import init_db, session_scope

# API 一律掛在真前綴下。曾經的做法是前端打 /api/*、middleware 剝掉前綴再交給
# 掛在根的路由——於是瀏覽器路徑與 API 路徑共用同一個命名空間：`/holdings`、
# `/recommendations`、`/sectors` 既是前端 client route 也是後端 API 路由，靠
# 「GET + Accept: text/html 就回 SPA 殼」硬閃過去。公開頁要進來時這條路走不通，
# 因為公開頁本身就是「GET + text/html」。改成真前綴後兩者結構上不可能撞。
_API = "/api"

app = FastAPI(title="TWAssistant", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix=_API)
app.include_router(api_router, prefix=_API)
app.include_router(holdings_router, prefix=_API)
app.include_router(sectors_router, prefix=_API)
app.include_router(flow_router, prefix=_API)
app.include_router(overview_router, prefix=_API)
app.include_router(intel_router, prefix=_API)
app.include_router(watchlists_router, prefix=_API)
app.include_router(settings_router, prefix=_API)
app.include_router(assistant_router, prefix=_API)
app.include_router(corners_router, prefix=_API)
app.include_router(lab_router, prefix=_API)
# 公開頁：無前綴。命名空間約定見 web/routes_public.py 檔頭。
app.include_router(public_router)


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
    """存活探測：刻意留在根路徑，不隨 API 進 /api。"""
    return {"ok": True}


@app.get(_API + "/system/status")
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
        "institutional_market_total": models.InstitutionalMarketTotal,
        "market_index": models.MarketIndex,
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


@app.get(_API + "/sources")
def sources_health() -> list[dict]:
    return [src.health() for src in registry.all_sources().values()]


class TokenBody(BaseModel):
    token: str | None = None
    save: bool = False  # 測通後是否寫入 Keychain


@app.post(_API + "/sources/{name}/test")
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


@app.post(_API + "/pipeline/run")
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


# --- 單一伺服器模式（一鍵啟動）：後端同時服務打包好的前端 ---
# dev 時前端跑 Vite(:5173) 用 /api 代理；打包後 dist 存在，這裡就接手，
# 使用者只需開一個 :8000 就能看整個 App。dist 不存在（純開發）則完全略過。
_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
_SPA_MODE = _DIST.exists()  # 打包模式：後端兼服務 SPA（登入牆的「殼放行」只在此模式才成立）

if _SPA_MODE:
    _INDEX = _DIST / "index.html"

    @app.middleware("http")
    async def _spa_shell(request: Request, call_next):
        """/app/* → SPA 殼（前端 router 以 basename="/app" 接手）。

        只認 /app 前綴，不再認 Accept——公開頁本身就是 GET + text/html，
        以 Accept 判斷會把公開頁整層蓋掉。其餘路徑放行給 FastAPI 路由
        （/api、公開頁、/health），沒配到的自然 404。
        """
        path = request.scope["path"]
        if path == "/app" or path.startswith("/app/"):
            return FileResponse(_INDEX)
        return await call_next(request)

    @app.get("/", include_in_schema=False)
    def _root_redirect() -> RedirectResponse:
        """過渡措施：真正的公開首頁做好前，根路徑先導向 App（維持 start.bat
        開瀏覽器即見工具的既有體驗）。首頁上線時把這個 handler 換成模板。"""
        return RedirectResponse("/app", status_code=302)

    # 打包後的靜態資源（JS/CSS，index.html 以 /assets/* 引用）。
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")


# --- 登入保護（網站模式）---
# 註冊在 _spa_shell 之後 → 在洋蔥最外層，看到的是原始路徑。
# 命名空間就是授權邊界：/api/* 需 session（AUTH_EXEMPT 除外）；其餘一律匿名——
# 那裡只有公開頁（web/routes_public.py，僅全站共用盤後資料）、/app 的 SPA 殼
# （純靜態、資料仍要打 /api）與 /health。判斷依據只有路徑；Accept 等 header
# 由客戶端控制，拿它當授權依據曾是實際漏洞（curl -H "Accept: text/html" 繞過）。
_AUTH_EXEMPT = {_API + "/auth/login", _API + "/auth/logout", _API + "/auth/me"}


@app.middleware("http")
async def _require_login(request: Request, call_next):
    if not auth.auth_enabled():
        return await call_next(request)
    if request.method == "OPTIONS":  # CORS preflight（dev）交給 CORS middleware
        return await call_next(request)
    path = request.scope["path"]
    if not (path == _API or path.startswith(_API + "/")):
        return await call_next(request)  # 非 /api = 公開命名空間
    if path in _AUTH_EXEMPT:
        return await call_next(request)
    if auth.verify_token(request.cookies.get(auth.SESSION_COOKIE)):
        return await call_next(request)
    from fastapi.responses import JSONResponse

    return JSONResponse({"detail": "not authenticated"}, status_code=401)
