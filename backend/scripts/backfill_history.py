"""一次性歷史回補：把日K / 三大法人 / 融資券 / 估值 補到指定起日（預設 2020-01-01）。

逐日端點（TWSE+TPEX，限流 1 req/s）→ 以「月」為單位、由各表最早日往回抓，
每月一個交易單位 commit。可重複執行：每輪重新讀各表 MIN(date)，自然從缺口續跑，
跑壞了直接再跑一次即可（upsert 冪等）。

全部資料補完後重算 indicators（IndicatorEngine 一次重算全歷史 → K線均線/KD/MACD 自動補齊）。

用法：
    cd backend
    python -m scripts.backfill_history                 # 全部四項，補到 2020-01-01
    python -m scripts.backfill_history 2019-01-01      # 指定起日
    python -m scripts.backfill_history 2020-01-01 price valuation   # 只補指定資料集
"""

from __future__ import annotations

import sys
import time
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func, select

from app.engines.indicators import IndicatorEngine
from app.sources import registry
from app.sources.base import SourceError
from app.storage import models, repositories as repo
from app.storage.database import SessionLocal, init_db

# key → (capability, repo 類名, fetch 方法, ORM model)
DATASETS: dict[str, tuple[str, str, str, type]] = {
    "price": ("price", "DailyPriceRepository", "fetch_prices", models.DailyPrice),
    "institutional": ("chip", "InstitutionalRepository", "fetch_institutional", models.Institutional),
    "margin": ("chip", "MarginRepository", "fetch_margin", models.Margin),
    "valuation": ("fundamental", "ValuationRepository", "fetch_valuation", models.Valuation),
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


def backfill_one(key: str, start: date, ids: set[str]) -> None:
    capability, repo_cls, method, model = DATASETS[key]
    src = registry.provider(capability)
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

        if not df.empty:
            df = df[df["stock_id"].astype(str).isin(ids)]
        n = 0
        if not df.empty:
            with SessionLocal() as s:
                n = repository.upsert_many(s, _records(df))
                s.commit()
        total += n
        _log(f"{key}: {chunk_start}~{cursor_end} → {n} 筆（累計 {total}）")
        cursor_end = chunk_start - timedelta(days=1)

    _log(f"{key}: 完成，本次新增/覆寫 {total} 筆")


def recompute_indicators() -> None:
    _log("重算 indicators（全歷史）…")
    with SessionLocal() as s:
        res = IndicatorEngine().run(s, date.today(), full=True)  # 回補了更舊歷史，需全量重算
        s.commit()
    _log(f"indicators 完成：{res}")


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
    ids = _known_ids()
    _log(f"已知股號 {len(ids)} 檔；回補資料集 {keys}；起日 {start}")

    for key in keys:
        backfill_one(key, start, ids)

    if "price" in keys:
        recompute_indicators()

    _log("全部完成。")


if __name__ == "__main__":
    main(sys.argv)
