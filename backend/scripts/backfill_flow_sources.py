"""一次性歷史回補：借券賣出餘額 + 個股當沖 + 期貨籌碼（TAIFEX），補到指定起日（預設 2020-01-01）。

與 backfill_market_flow 同模式：由各表最早日往回、逐月 commit、可中斷續跑（每輪
重讀 MIN(date)、upsert 冪等）。個股級資料落庫前過濾未知股號（FK 安全）。

董監持股（insider）為月快照、官方無歷史端點，靠每日 pipeline upsert 自然累積，不在此列。

用法：
    cd backend
    .venv/bin/python -m scripts.backfill_flow_sources                  # 全部，補到 2020-01-01
    .venv/bin/python -m scripts.backfill_flow_sources 2023-01-01       # 指定起日
    .venv/bin/python -m scripts.backfill_flow_sources 2020-01-01 short_lending  # 只補一項
"""

from __future__ import annotations

import sys
import time
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func, select

from app.sources import registry
from app.sources.base import SourceError
from app.storage import models, repositories as repo
from app.storage.database import SessionLocal, init_db

# key → (source 名稱, repo 類名, fetch 方法, ORM model, 是否個股級)
DATASETS: dict[str, tuple[str, str, str, type, bool]] = {
    "short_lending": ("twmarket", "ShortLendingRepository", "fetch_short_lending", models.ShortLending, True),
    "day_trading": ("twmarket", "DayTradingRepository", "fetch_day_trading", models.DayTrading, True),
    "derivatives": ("taifex", "MarketDerivativesRepository", "fetch_market_derivatives", models.MarketDerivatives, False),
}


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _records(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    return df.astype(object).where(pd.notna(df), None).to_dict("records")


def _min_date(model: type) -> date | None:
    with SessionLocal() as s:
        return s.execute(select(func.min(model.date))).scalar_one_or_none()


def _known_ids() -> set[str]:
    with SessionLocal() as s:
        return set(s.execute(select(models.Stock.id)).scalars().all())


def backfill_one(key: str, start: date, known_ids: set[str]) -> None:
    source_name, repo_cls, method, model, per_stock = DATASETS[key]
    src = registry.get_source(source_name)
    repository = getattr(repo, repo_cls)()

    cur_min = _min_date(model)
    cursor_end = (cur_min - timedelta(days=1)) if cur_min else date.today()
    if cursor_end < start:
        _log(f"{key}: 已涵蓋至 {cur_min}（≤ {start}），略過")
        return

    _log(f"{key}: 從 {cursor_end} 往回補到 {start}（現有最早 {cur_min}）")
    total = 0
    while cursor_end >= start:
        chunk_start = max(start, cursor_end.replace(day=1))
        try:
            df = getattr(src, method)(chunk_start, cursor_end, None)
        except SourceError as exc:
            _log(f"{key}: {chunk_start}~{cursor_end} 來源失敗（{exc.reason}），跳過此月")
            cursor_end = chunk_start - timedelta(days=1)
            continue

        if per_stock and not df.empty:
            df = df[df["stock_id"].astype(str).isin(known_ids)]

        n = 0
        if not df.empty:
            with SessionLocal() as s:
                n = repository.upsert_many(s, _records(df))
                s.commit()
        total += n
        _log(f"{key}: {chunk_start}~{cursor_end} → {n} 筆（累計 {total}）")
        cursor_end = chunk_start - timedelta(days=1)

    _log(f"{key}: 完成，本次新增/覆寫 {total} 筆")


def main(argv: list[str]) -> None:
    start = date(2020, 1, 1)
    keys = list(DATASETS.keys())

    rest = argv[1:]
    if rest and "-" in rest[0]:
        start = date.fromisoformat(rest[0])
        rest = rest[1:]
    if rest:
        keys = rest
        bad = [k for k in keys if k not in DATASETS]
        if bad:
            raise SystemExit(f"未知資料集：{bad}；可選 {list(DATASETS)}")

    init_db()
    known = _known_ids()
    _log(f"回補資料集 {keys}；起日 {start}；已知股號 {len(known)} 檔")
    for key in keys:
        backfill_one(key, start, known)
    _log("全部完成。")


if __name__ == "__main__":
    main(sys.argv)
