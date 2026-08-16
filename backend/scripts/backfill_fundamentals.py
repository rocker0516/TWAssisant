"""一次性回補：月營收 / 季財報（單季化）歷史，預設補到 2020。

長線軌釣大魚（持續成長+展望好）需要成長持續性/加速度/利潤率趨勢 → 多期歷史；
openapi 快照只有最新期，故從 MOPS 彙總表補（月營收 t21sc03、季財報 t163sb04+06）。

- 月營收：逐月抓（上市+上櫃+KY），已有足量資料的月份自動略過（可續跑）。
- 季財報：MOPS 為累計制，逐年由 Q1 往 Q4 抓、差分還原單季後落庫；
  差分需要同年前一季的累計值，故以「年」為處理單位、整年重抓（每季 4 請求，便宜）。
- upsert 冪等，跑壞直接重跑。限流 1 req/s。

用法：
    cd backend
    python -m scripts.backfill_fundamentals              # 兩種都補，2020 起
    python -m scripts.backfill_fundamentals 2019         # 指定起年
    python -m scripts.backfill_fundamentals 2020 revenue # 只補月營收（revenue|financials）
"""

from __future__ import annotations

import sys
import time
from datetime import date

import pandas as pd
from sqlalchemy import func, select

from app.sources import mops
from app.sources.base import SourceError
from app.storage import models, repositories as repo
from app.storage.database import SessionLocal, init_db


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _records(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    return df.astype(object).where(pd.notna(df), None).to_dict("records")


def _known_ids() -> set[str]:
    with SessionLocal() as s:
        return set(s.execute(select(models.Stock.id)).scalars().all())


def _covered_months() -> set[tuple[int, int]]:
    """已有足量列數（>300 檔）的月份視為補過，續跑時略過。"""
    with SessionLocal() as s:
        rows = s.execute(
            select(models.RevenueMonthly.year, models.RevenueMonthly.month, func.count())
            .group_by(models.RevenueMonthly.year, models.RevenueMonthly.month)
        ).all()
    return {(y, m) for y, m, n in rows if n > 300}


def backfill_revenue(start_year: int, ids: set[str]) -> None:
    client = mops.client()
    repository = repo.RevenueMonthlyRepository()
    covered = _covered_months()
    today = date.today()
    months = [
        (y, m)
        for y in range(start_year, today.year + 1)
        for m in range(1, 13)
        if (y, m) < (today.year, today.month)  # 當月營收要下月 10 日才公布
    ]
    todo = [ym for ym in months if ym not in covered]
    _log(f"月營收：共 {len(months)} 個月，已覆蓋 {len(months) - len(todo)}，待補 {len(todo)}")

    for y, m in todo:
        try:
            df = client.revenue_month(y, m)
        except SourceError as exc:
            _log(f"月營收 {y}-{m:02d}：來源失敗（{exc.reason}），跳過")
            continue
        df = df[df["stock_id"].isin(ids)]
        n = 0
        if not df.empty:
            with SessionLocal() as s:
                n = repository.upsert_many(s, _records(df))
                s.commit()
        _log(f"月營收 {y}-{m:02d} → {n} 筆")


def backfill_financials(start_year: int, ids: set[str]) -> None:
    client = mops.client()
    repository = repo.FinancialQuarterRepository()
    today = date.today()
    last_ended = mops.due_quarters(today, n=1)[0]  # 最近一個已結束的季

    for y in range(start_year, today.year + 1):
        prev_cum: pd.DataFrame | None = None
        for q in (1, 2, 3, 4):
            if (y, q) > last_ended:
                break
            try:
                cum = client.financials_cumulative(y, q)
            except SourceError as exc:
                _log(f"季財報 {y}Q{q}：來源失敗（{exc.reason}），本年後續季缺差分基底，跳到下一年")
                break
            if cum.empty:  # 申報期未開始（如剛結束的季）
                _log(f"季財報 {y}Q{q}：無資料（申報未出），略過")
                break
            single = mops.single_quarter(cum, prev_cum)
            single = single[single["stock_id"].isin(ids)]
            n = 0
            if not single.empty:
                with SessionLocal() as s:
                    n = repository.upsert_many(s, _records(single))
                    s.commit()
            _log(f"季財報 {y}Q{q} → {n} 筆（單季化）")
            prev_cum = cum


def main(argv: list[str]) -> None:
    start_year = 2020
    kinds = ["revenue", "financials"]

    rest = argv[1:]
    if rest and rest[0].isdigit():
        start_year = int(rest[0])
        rest = rest[1:]
    if rest:
        kinds = rest
        bad = [k for k in kinds if k not in ("revenue", "financials")]
        if bad:
            raise SystemExit(f"未知資料集：{bad}；可選 revenue / financials")

    init_db()
    ids = _known_ids()
    _log(f"已知股號 {len(ids)} 檔；回補 {kinds}；起年 {start_year}")

    if "revenue" in kinds:
        backfill_revenue(start_year, ids)
    if "financials" in kinds:
        backfill_financials(start_year, ids)

    _log("全部完成。")
    _log("提醒：既有 Score 是評分當下的資料算的，不會自動吸收新補的基本面——")
    _log("　　　請接著跑 scripts/backfill_scores.py --start <受影響起日> --force 重算長線分數。")


if __name__ == "__main__":
    main(sys.argv)
