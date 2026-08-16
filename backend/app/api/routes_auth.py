"""登入 / 登出 / 狀態查詢。"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import auth
from ..config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response) -> JSONResponse:
    if not auth.auth_enabled():
        return JSONResponse({"ok": True, "auth_enabled": False})
    ip = request.client.host if request.client else "?"
    locked = auth.is_locked_out(ip)
    if locked:
        return JSONResponse(
            {"ok": False, "reason": f"嘗試次數過多，請 {locked} 秒後再試"}, status_code=429
        )
    if not auth.check_credentials(body.username, body.password):
        auth.record_failure(ip)
        return JSONResponse({"ok": False, "reason": "帳號或密碼錯誤"}, status_code=401)
    auth.clear_failures(ip)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        auth.SESSION_COOKIE,
        auth.issue_token(body.username),
        max_age=settings.auth_session_days * 86400,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return resp


@router.post("/logout")
def logout() -> JSONResponse:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp


@router.get("/me")
def me(request: Request) -> dict:
    if not auth.auth_enabled():
        return {"authenticated": True, "auth_enabled": False}
    token = request.cookies.get(auth.SESSION_COOKIE)
    return {"authenticated": auth.verify_token(token), "auth_enabled": True}
