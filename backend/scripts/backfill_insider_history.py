"""一次性回補：董監持股月歷史（MOPS ajax_stapap1 單檔單月，預設 2021-01 起）。

情境路由矩陣污染稽核（docs/ctx-matrix-findings.md）判定 `insider_chg` 覆蓋率 0%
＝E 家族全滅：insider_holding 表只有 openapi t187ap11 最新月快照累積（2026-06 起）。
挖掘窗 2021-01~2024-12 的歷史只能從 MOPS 逐檔逐月補（口徑已與 openapi 快照比對一致，
見 sources/mops.py insider_month）。

- 逐月（舊→新）× 逐檔抓，已在庫的 (stock_id, year, month) 自動略過（可續跑）。
- upsert 冪等，跑壞直接重跑。各 worker 自帶 1 req/s 節流（MopsClient 內建）。
- 全池 ~2,500 檔 × ~66 月 ≈ 16.5 萬請求；MOPS WAF 實測上限 ~0.4 req/s，
  設計上就是數天級長跑背景工作。

用法：
    cd backend
    python -m scripts.backfill_insider_history                # 2021-01 ~ 上上月
    python -m scripts.backfill_insider_history 2021-01 2024-12
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from datetime import date
from queue import Queue

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.sources import mops
from app.sources.base import SourceError
from app.storage import models, repositories as repo
from app.storage.database import SessionLocal, init_db

_WRITE_RETRIES = 12
_WRITE_BACKOFF = 15.0  # 秒；夜間排程寫批次會長時間佔 SQLite 寫鎖（同 shareholding_backfill）
_BATCH = 40            # 每 N 檔落庫一次，減少寫鎖競爭
# MOPS 回應時間 2~6 秒主導吞吐（單執行緒實測 ≈0.2 req/s → 全池要 10 天），
# 以少量並行 worker（各自 1 req/s 節流）拉回；4 workers 實測仍屬禮貌流量。
_WORKERS = max(1, int(os.environ.get("INSIDER_WORKERS", "3")))


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _pool_ids() -> list[str]:
    """非 ETF 股票池（同 research/ctx_matrix/chip.py），再限 4 碼普通股代號。

    池裡的受益證券（01001T）、特別股（73193P）、創新板憑證（7105xx）等
    非公司代號在 MOPS 董監報表必然查無資料，直接濾掉省 ~1.2 萬請求。
    """
    with SessionLocal() as s:
        rows = s.execute(select(models.Stock.id, models.Stock.is_etf)).all()
    return sorted(
        sid
        for sid, is_etf in rows
        if not is_etf and re.fullmatch(r"[1-9]\d{3}", str(sid))
    )


def _done_keys() -> set[tuple[str, int, int]]:
    with SessionLocal() as s:
        rows = s.execute(
            select(
                models.InsiderHolding.stock_id,
                models.InsiderHolding.year,
                models.InsiderHolding.month,
            )
        ).all()
    return {(sid, y, m) for sid, y, m in rows}


def _months(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    out, (y, m) = [], start
    while (y, m) <= end:
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _flush(rows: list[dict]) -> None:
    if not rows:
        return
    repository = repo.InsiderHoldingRepository()
    for attempt in range(_WRITE_RETRIES):
        try:
            with SessionLocal() as s:
                repository.upsert_many(s, rows)
                s.commit()
            return
        except OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == _WRITE_RETRIES - 1:
                raise
            time.sleep(_WRITE_BACKOFF)


def main() -> None:
    def _ym(arg: str) -> tuple[int, int]:
        y, m = arg.split("-")
        return int(y), int(m)

    today = date.today()
    # 預設補到上上月（月報約月中前後才齊；最新 1~2 月由排程 openapi 快照接手）
    end_default = (today.year, today.month - 2) if today.month > 2 else (today.year - 1, today.month + 10)
    start = _ym(sys.argv[1]) if len(sys.argv) > 1 else (2021, 1)
    end = _ym(sys.argv[2]) if len(sys.argv) > 2 else end_default

    init_db()
    ids = _pool_ids()
    done = _done_keys()
    months = _months(start, end)
    todo_total = sum(1 for ym in months for sid in ids if (sid, *ym) not in done)
    _log(f"董監持股歷史回補：{len(ids)} 檔 × {len(months)} 月，待抓 {todo_total:,} 格")

    tasks: Queue[tuple[str, int, int] | None] = Queue()
    for y, m in months:
        for sid in ids:
            if (sid, y, m) not in done:
                tasks.put((sid, y, m))
    for _ in range(_WORKERS):
        tasks.put(None)  # 毒丸

    lock = threading.Lock()
    state = {"fetched": 0, "missed": 0, "errors": 0}
    buf: list[dict] = []
    t0 = time.monotonic()

    def _worker() -> None:
        client = mops.MopsClient()  # 各 worker 自帶連線與 1 req/s 節流
        while True:
            task = tasks.get()
            if task is None:
                return
            sid, y, m = task
            row = None
            err = None
            try:
                row = client.insider_month(sid, y, m)
            except SourceError as exc:
                err = exc
            except Exception as exc:  # noqa: BLE001 — 長跑 worker 不可炸
                err = exc
            pending: list[dict] | None = None
            with lock:
                if err is not None:
                    state["errors"] += 1
                    if state["errors"] % 50 == 1:
                        _log(f"  來源錯誤（第 {state['errors']} 次）{sid} {y}-{m:02d}：{err}")
                    continue
                state["fetched"] += 1
                if row is None:
                    state["missed"] += 1  # 該月未上市/查無資料，屬正常
                else:
                    buf.append(row)
                if len(buf) >= _BATCH:
                    pending, buf[:] = list(buf), []
                fetched = state["fetched"]
                if fetched % 500 == 0:
                    rate = fetched / (time.monotonic() - t0)
                    eta_h = (todo_total - fetched) / rate / 3600 if rate > 0 else 0
                    _log(
                        f"  進度 {fetched:,}/{todo_total:,}（{fetched / todo_total:.1%}）"
                        f" 空 {state['missed']:,} 錯 {state['errors']:,}，ETA {eta_h:.1f}h"
                    )
            if pending:
                _flush(pending)  # DB 寫在鎖外，不擋其他 worker 抓取

    threads = [threading.Thread(target=_worker, daemon=True) for _ in range(_WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    _flush(buf)
    _log(
        f"全部完成：抓 {state['fetched']:,}、入庫 {state['fetched'] - state['missed']:,}、"
        f"空 {state['missed']:,}、錯 {state['errors']:,}"
    )


if __name__ == "__main__":
    main()
