"""API 依賴。"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from ..storage.database import SessionLocal


def get_session() -> Iterator[Session]:
    """讀取用 session（不寫入，無需 commit）。"""
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
