"""一次性歷史回補：全市場三大法人總表（BFI82U）+ 加權指數，補到指定起日（預設 2020-01-01）。

市場級資料 PK=date、無 stock_id，故與 backfill_history 分開。逐日端點（TWSE，限流
1 req/s）→ 以「月」為單位、由各表最早日往回抓，每月一個交易單位 commit。可重複執行：
每輪重新讀各表 MIN(date)，自然從缺口續跑；跑壞了再跑一次即可（upsert 冪等）。

用法：
    cd backend
    python -m scripts.backfill_market_flow                 # 兩項，補到 2020-01-01
    python -m scripts.backfill_market_flow 2019-01-01      # 指定起日
    python -m scripts.backfill_market_flow 2020-01-01 inst_market   # 只補指定資料集
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

# key → (source 名稱, repo 類名, fetch 方法, ORM model)
DATASETS: dict[str, tuple[str, str, str, type]] = {
    "inst_market": ("twse", "InstitutionalMarketTotalRepository", "fetch_institutional_market_total", models.InstitutionalMarketTotal),
    "market_index": ("twse", "MarketIndexRepository", "fetch_index", models.MarketIndex),
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


def backfill_one(key: str, start: date) -> None:
    source_name, repo_cls, method, model = DATASETS[key]
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
    _log(f"回補資料集 {keys}；起日 {start}")
    for key in keys:
        backfill_one(key, start)
    _log("全部完成。")


if __name__ == "__main__":
    main(sys.argv)
