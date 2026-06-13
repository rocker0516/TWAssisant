"""設定端點（P6）：讀全部、分區更新、恢復預設、即時生效重算。"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from ..services.settings_service import DEFAULTS, SettingsService
from .deps import get_session, get_session_write

router = APIRouter(prefix="/settings", tags=["settings"])
_svc = SettingsService()


@router.get("")
def get_settings(session: Session = Depends(get_session)) -> dict:
    return _svc.all_effective(session)


@router.put("/{key}")
def update_settings(
    key: str, partial: dict = Body(...), session: Session = Depends(get_session_write)
) -> dict:
    if key not in DEFAULTS:
        return {"error": f"未知設定區 {key}"}
    result = _svc.update(session, key, partial)
    if key == "general":  # 排程時間/開關改了 → 用剛算好的設定重排（避開未 commit 的舊值）
        from ..scheduler.service import get_scheduler

        get_scheduler().reschedule(result)
    return result


@router.post("/{key}/reset")
def reset_settings(key: str, session: Session = Depends(get_session_write)) -> dict:
    result = _svc.reset(session, key)
    if key == "general":
        from ..scheduler.service import get_scheduler

        get_scheduler().reschedule(result)
    return result


@router.post("/recompute")
def recompute(session: Session = Depends(get_session_write)) -> dict:
    return _svc.recompute(session)
