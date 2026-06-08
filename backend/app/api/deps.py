"""API 依賴。"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from ..storage.database import SessionLocal


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
