"""身分端點：登入／登出／註冊／Email 驗證／忘記密碼／重設密碼。

回應語言原則：
  - 「查無此帳號」與「密碼錯誤」回同一句話（不洩漏帳號是否存在）。
  - /forgot 一律回 ok（同理）。
  - /signup 撞 Email 回 409——註冊頁本來就要告訴使用者「這個信箱已註冊，
    去登入或重設密碼」；這裡的存在性洩漏是功能，上面兩處的才是漏洞。
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth
from ..config import settings
from ..services import mailer
from ..storage import models
from .deps import get_session, get_session_write

router = APIRouter(prefix="/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MIN_PASSWORD = 8
_VERIFY_HOURS = 24
_RESET_HOURS = 1

_BAD_CREDS = "帳號或密碼錯誤"


def _set_session_cookie(resp: Response, user: models.User) -> None:
    resp.set_cookie(
        auth.SESSION_COOKIE,
        auth.issue_token(user.id, user.session_version),
        max_age=settings.auth_session_days * 86400,
        httponly=True,
        samesite="lax",
        path="/",
    )


def _find_user(session: Session, username: str) -> models.User | None:
    """以 email 找；相容舊 .env 帳號名（bootstrap admin 的 email 是 name@local.twa）。"""
    email = username if "@" in username else f"{username}@local.twa"
    return session.execute(
        select(models.User).where(models.User.email == email)
    ).scalars().first()


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginBody, request: Request, session: Session = Depends(get_session_write)) -> JSONResponse:
    if not auth.auth_enabled():
        return JSONResponse({"ok": True, "auth_enabled": False})
    ip = request.client.host if request.client else "?"
    locked = auth.is_locked_out(ip)
    if locked:
        return JSONResponse(
            {"ok": False, "reason": f"嘗試次數過多，請 {locked} 秒後再試"}, status_code=429
        )

    user = _find_user(session, body.username)
    if user is None:
        auth.record_failure(ip)
        return JSONResponse({"ok": False, "reason": _BAD_CREDS}, status_code=401)

    minutes = auth.account_locked_minutes(user)
    if minutes:
        return JSONResponse(
            {"ok": False, "reason": f"此帳號已暫時鎖定，請 {minutes} 分鐘後再試"},
            status_code=429)

    if not auth.verify_password(body.password, user.password_hash):
        auth.record_failure(ip)
        auth.record_account_failure(user)
        return JSONResponse({"ok": False, "reason": _BAD_CREDS}, status_code=401)

    if user.email_verified_at is None:
        return JSONResponse(
            {"ok": False, "reason": "請先完成 Email 驗證（查看註冊信）"}, status_code=403)

    auth.clear_failures(ip)
    auth.clear_account_failures(user)
    resp = JSONResponse({"ok": True})
    _set_session_cookie(resp, user)
    return resp


@router.post("/logout")
def logout() -> JSONResponse:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp


@router.post("/logout-all")
def logout_all(request: Request, session: Session = Depends(get_session_write)) -> JSONResponse:
    """登出所有裝置：session_version +1，所有已簽發 token 立即失效。"""
    user = auth.resolve_user(session, request.cookies.get(auth.SESSION_COOKIE))
    if user is None:
        raise HTTPException(401, "not authenticated")
    user.session_version += 1
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp


@router.get("/me")
def me(request: Request, session: Session = Depends(get_session)) -> dict:
    if not auth.auth_enabled():
        return {"authenticated": True, "auth_enabled": False}
    user = auth.resolve_user(session, request.cookies.get(auth.SESSION_COOKIE))
    if user is None:
        return {"authenticated": False, "auth_enabled": True}
    return {"authenticated": True, "auth_enabled": True,
            "email": user.email, "tier": user.tier, "role": user.role}


# ── 註冊 / 驗證 / 重設 ─────────────────────────────────────


class SignupBody(BaseModel):
    email: str
    password: str


@router.post("/signup")
def signup(body: SignupBody, session: Session = Depends(get_session_write)) -> dict:
    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(422, "Email 格式不正確")
    if len(body.password) < _MIN_PASSWORD:
        raise HTTPException(422, f"密碼至少 {_MIN_PASSWORD} 個字元")
    exists = session.execute(
        select(models.User.id).where(models.User.email == email)
    ).first()
    if exists:
        raise HTTPException(409, "此 Email 已註冊，可直接登入或使用忘記密碼")

    user = models.User(email=email, password_hash=auth.hash_password(body.password),
                       tier="free", role="user")
    session.add(user)
    session.flush()

    token = secrets.token_urlsafe(32)
    session.add(models.EmailVerification(
        token=token, user_id=user.id,
        expires_at=datetime.now() + timedelta(hours=_VERIFY_HOURS)))
    mailer.send_verification(email, token)
    return {"ok": True, "message": "驗證信已寄出，請於 24 小時內完成驗證"}


def consume_verification(session: Session, token: str) -> models.User | None:
    """驗證 token → 標記已驗證。公開頁 /verify/{token} 直接呼叫（伺服器端渲染）。"""
    row = session.get(models.EmailVerification, token)
    if row is None or row.expires_at < datetime.now():
        return None
    user = session.get(models.User, row.user_id)
    if user is None:
        return None
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now()
    session.delete(row)
    return user


class ForgotBody(BaseModel):
    email: str


@router.post("/forgot")
def forgot(body: ForgotBody, session: Session = Depends(get_session_write)) -> dict:
    email = body.email.strip().lower()
    user = session.execute(
        select(models.User).where(models.User.email == email)
    ).scalars().first()
    if user is not None:
        token = secrets.token_urlsafe(32)
        session.add(models.PasswordReset(
            token=token, user_id=user.id,
            expires_at=datetime.now() + timedelta(hours=_RESET_HOURS)))
        mailer.send_reset(email, token)
    # 帳號存在與否回同一句——這個端點是攻擊者枚舉信箱的第一站
    return {"ok": True, "message": "若該 Email 已註冊，重設信已寄出"}


class ResetBody(BaseModel):
    token: str
    password: str


@router.post("/reset")
def reset(body: ResetBody, session: Session = Depends(get_session_write)) -> dict:
    if len(body.password) < _MIN_PASSWORD:
        raise HTTPException(422, f"密碼至少 {_MIN_PASSWORD} 個字元")
    row = session.get(models.PasswordReset, body.token)
    if row is None or row.used_at is not None or row.expires_at < datetime.now():
        raise HTTPException(400, "重設連結無效或已過期，請重新申請")
    user = session.get(models.User, row.user_id)
    if user is None:
        raise HTTPException(400, "重設連結無效或已過期，請重新申請")
    user.password_hash = auth.hash_password(body.password)
    user.session_version += 1  # 撤銷所有既有 session——改密碼的意義就是把別人踢下線
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now()  # 能收到重設信＝信箱有效
    row.used_at = datetime.now()
    auth.clear_account_failures(user)
    return {"ok": True, "message": "密碼已更新，請重新登入"}
