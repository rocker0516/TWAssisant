"""API 依賴。"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import auth
from ..storage.database import SessionLocal
from ..storage.user_data import UserData


def get_session() -> Iterator[Session]:
    """讀取用 session。"""
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def get_session_write() -> Iterator[Session]:
    """寫入用 session：正常 commit、例外 rollback。"""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def _resolve(request: Request, session: Session):
    """cookie → User。登入關閉（開發模式）→ bootstrap 的 dev admin。"""
    if not auth.auth_enabled():
        user = auth.dev_user(session)
    else:
        user = auth.resolve_user(session, request.cookies.get(auth.SESSION_COOKIE))
    if user is None:
        raise HTTPException(401, "not authenticated")
    return user


def get_current_user(request: Request, session: Session = Depends(get_session)):
    return _resolve(request, session)


def get_user_data(request: Request, session: Session = Depends(get_session)) -> UserData:
    """讀取用 scoped 使用者資料。route 對使用者四表的查詢只准經過這裡。"""
    return UserData(session, _resolve(request, session).id)


def get_user_data_write(
    request: Request, session: Session = Depends(get_session_write)
) -> UserData:
    """寫入用 scoped 使用者資料（commit/rollback 語意同 get_session_write）。"""
    return UserData(session, _resolve(request, session).id)
