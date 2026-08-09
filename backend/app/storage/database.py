"""SQLite 連線與 session 管理。

只本機單人跑 → 單一檔案 DB，啟用 WAL 讓盤後寫入與白天讀取不互卡。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ..config import settings


class Base(DeclarativeBase):
    """所有 ORM model 的宣告基底。"""


engine: Engine = create_engine(
    settings.db_url,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    # 多行程（app + 排程 + 手動腳本）並存：碰到寫鎖時等待而非立即報
    # "database is locked"。WAL 下讀寫本就並行，這裡再給寫者互讓的緩衝。
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """建立所有表（冪等）。import models 以註冊 metadata。"""
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_columns()


# create_all 只補缺表、不補既有表的新欄；本機 SQLite 用輕量 ADD COLUMN 補欄（冪等）
_COLUMN_ADDITIONS: dict[str, dict[str, str]] = {
    "scores": {
        "coverage": "FLOAT", "confidence": "FLOAT", "stability": "FLOAT",
        "details": "JSON", "passed_styles": "JSON", "style_totals": "JSON",
        "style_coverage": "JSON", "style_confidence": "JSON", "style_stability": "JSON",
        "strict_filter": "BOOLEAN",  # 當日原始硬篩（波段遲滯狀態機隔日回看用）
    },
    "indicators": {"ma120": "FLOAT", "ma240": "FLOAT"},  # 半年線/年線（長期支撐）
}


def _ensure_columns() -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
        for table, cols in _COLUMN_ADDITIONS.items():
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            for name, ddl in cols.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


@contextmanager
def session_scope() -> Iterator[Session]:
    """交易範圍：正常 commit，例外 rollback，最後關閉。"""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
