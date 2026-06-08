"""llm_cache 讀寫（架構④：每日盤後批次生成、白天讀快取）。"""

from __future__ import annotations

from datetime import date

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..storage import models


def cache_key(kind: str, ref: str | int, d: date) -> str:
    return f"{kind}:{ref}:{d.isoformat()}"


def get_cached(session: Session, key: str) -> str | None:
    row = session.get(models.LlmCache, key)
    return row.content if row else None


def put_cached(session: Session, key: str, kind: str, ref: str | int, d: date, content: str, model: str) -> None:
    stmt = sqlite_insert(models.LlmCache).values(
        key=key, kind=kind, ref_id=str(ref), date=d, content=content, model=model,
    ).on_conflict_do_update(
        index_elements=["key"], set_={"content": content, "model": model, "date": d},
    )
    session.execute(stmt)
