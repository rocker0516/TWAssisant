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
    _migrate_multiuser()


# create_all 只補缺表、不補既有表的新欄；本機 SQLite 用輕量 ADD COLUMN 補欄（冪等）
_COLUMN_ADDITIONS: dict[str, dict[str, str]] = {
    "scores": {
        "coverage": "FLOAT", "confidence": "FLOAT", "stability": "FLOAT",
        "details": "JSON", "passed_styles": "JSON", "style_totals": "JSON",
        "style_coverage": "JSON", "style_confidence": "JSON", "style_stability": "JSON",
        "strict_filter": "BOOLEAN",  # 當日原始硬篩（波段遲滯狀態機隔日回看用）
    },
    "indicators": {"ma120": "FLOAT", "ma240": "FLOAT"},  # 半年線/年線（長期支撐）
    "company_profile": {"listed_date": "DATE"},  # 正確上市/上櫃日（t187ap03）
    "holdings": {"entry_snapshot": "JSON",
                 "user_id": "INTEGER REFERENCES users(id)"},
    # 多租戶隔離（分層設計第 6 節）：使用者資料四表補 user_id
    "transactions": {"user_id": "INTEGER REFERENCES users(id)"},
    "watchlists": {"user_id": "INTEGER REFERENCES users(id)"},
    "watchlist_items": {"user_id": "INTEGER REFERENCES users(id)"},
}

# ALTER ADD COLUMN 補不了索引；user_id 是每個使用者資料查詢的必要條件，補上
_INDEX_ADDITIONS = [
    "CREATE INDEX IF NOT EXISTS ix_holdings_user_id ON holdings(user_id)",
    "CREATE INDEX IF NOT EXISTS ix_transactions_user_id ON transactions(user_id)",
    "CREATE INDEX IF NOT EXISTS ix_watchlists_user_id ON watchlists(user_id)",
    "CREATE INDEX IF NOT EXISTS ix_watchlist_items_user_id ON watchlist_items(user_id)",
]


def _ensure_columns() -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
        for table, cols in _COLUMN_ADDITIONS.items():
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            for name, ddl in cols.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


_BACKFILL_OWNER = [
    "UPDATE holdings SET user_id = :uid WHERE user_id IS NULL",
    "UPDATE transactions SET user_id = :uid WHERE user_id IS NULL",
    "UPDATE watchlists SET user_id = :uid WHERE user_id IS NULL",
    "UPDATE watchlist_items SET user_id = :uid WHERE user_id IS NULL",
]


def _migrate_multiuser() -> None:
    """單人 → 多租戶的冪等遷移（分層設計第 6 節「遷移」）。

    1. 補 user_id 索引（ALTER ADD COLUMN 做不到的部分）。
    2. bootstrap 管理員：.env 的 TWA_AUTH_USERNAME/PASSWORD 若已設定，確保
       users 表有對應的 admin 帳號（密碼雜湊存 DB；.env 明文只在 bootstrap
       與舊版相容路徑用到）。
    3. 既有無主資料（user_id IS NULL）全部歸第一個 admin——單人時代的資料
       本來就是站主的。

    每一步以「查了才做」達成冪等，重跑無副作用——與 pipeline 同一條慣例。
    """
    from sqlalchemy import text

    from .. import auth
    from ..config import settings as cfg

    with engine.begin() as conn:
        for ddl in _INDEX_ADDITIONS:
            conn.execute(text(ddl))

        if cfg.auth_password:
            email = cfg.auth_username if "@" in cfg.auth_username \
                else cfg.auth_username + "@local.twa"
            row = conn.execute(
                text("SELECT id, password_hash FROM users WHERE email = :e"), {"e": email}
            ).first()
            if row:
                admin_id = row[0]
                # .env 是 bootstrap admin 密碼的真相來源：改了 .env 就同步雜湊，
                # 否則使用者以為改了密碼、實際上舊密碼還能登入
                if not auth.verify_password(cfg.auth_password, row[1]):
                    conn.execute(
                        text("UPDATE users SET password_hash = :h, session_version = "
                             "session_version + 1 WHERE id = :i"),
                        {"h": auth.hash_password(cfg.auth_password), "i": admin_id},
                    )
            else:
                admin_id = conn.execute(
                    text(
                        "INSERT INTO users (email, password_hash, tier, role, "
                        "email_verified_at, session_version, failed_logins) "
                        "VALUES (:e, :h, 'pro', 'admin', CURRENT_TIMESTAMP, 1, 0)"
                    ),
                    {"e": email, "h": auth.hash_password(cfg.auth_password)},
                ).lastrowid
        else:
            # 開發模式（無密碼）：仍需一個帳號承接資料與 scoped 查詢
            row = conn.execute(text("SELECT id FROM users ORDER BY id LIMIT 1")).first()
            if row:
                admin_id = row[0]
            else:
                admin_id = conn.execute(
                    text(
                        "INSERT INTO users (email, password_hash, tier, role, "
                        "email_verified_at, session_version, failed_logins) "
                        "VALUES ('dev@local.twa', '!', 'pro', 'admin', "
                        "CURRENT_TIMESTAMP, 1, 0)"
                    )
                ).lastrowid

        for sql in _BACKFILL_OWNER:
            conn.execute(text(sql), {"uid": admin_id})


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
