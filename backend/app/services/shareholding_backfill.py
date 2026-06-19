"""集保股權分散『單檔歷史』背景回補（曲線用）。

TDCC 開放資料只給最新週，歷史要逐檔爬智慧網（慢、易壞）。策略＝「看哪檔就回補哪檔」：
個股頁要曲線時，若 DB 該檔週數不足，啟一條背景執行緒爬近一年補進 shareholding 表，
之後常駐快取、重整即顯示。同檔同時只跑一次（去重），不跑全市場。
"""

from __future__ import annotations

import logging
import threading
import time

from sqlalchemy.exc import OperationalError

from ..sources import registry
from ..sources.base import SourceError
from ..storage import models, repositories as repo
from ..storage.database import session_scope

log = logging.getLogger(__name__)

# 夜間 catch-up/排程跑 IndicatorStep 時會長時間獨佔 SQLite 寫鎖（>busy_timeout）。
# 回補爬完才寫，遇鎖時重試而非丟棄已爬資料；期間執行緒仍佔 _in_progress（不會重觸發風暴）。
_WRITE_RETRIES = 12
_WRITE_BACKOFF = 15.0  # 秒（每次重試固定間隔，~3 分鐘總額，足以等過多數寫批次）

_lock = threading.Lock()
_in_progress: set[str] = set()


def is_backfilling(stock_id: str) -> bool:
    with _lock:
        return stock_id in _in_progress


def trigger_backfill(stock_id: str, max_weeks: int = 52) -> bool:
    """啟動背景回補。已在跑或啟動成功皆回 True（代表前端可顯示『回補中』）。"""
    with _lock:
        if stock_id in _in_progress:
            return True
        _in_progress.add(stock_id)
    threading.Thread(
        target=_run, args=(stock_id, max_weeks), name=f"holdbackfill-{stock_id}", daemon=True
    ).start()
    return True


def _write(stock_id: str, recs: list[dict]) -> bool:
    """落庫，遇 SQLite 寫鎖（排程大量寫入時）重試。全失敗回 False（保留下次重補）。"""
    for attempt in range(_WRITE_RETRIES):
        try:
            with session_scope() as s:
                if s.get(models.Stock, stock_id) is None:  # FK 安全：未知股號不落庫
                    return True
                repo.ShareholdingRepository().upsert_many(s, recs)
            return True
        except OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == _WRITE_RETRIES - 1:
                raise
            time.sleep(_WRITE_BACKOFF)
    return False


def _run(stock_id: str, max_weeks: int) -> None:
    try:
        src = registry.provider("holding")
        df = src.fetch_holding_history(stock_id, max_weeks)
        if df.empty:
            return
        recs = df.astype(object).where(df.notna(), None).to_dict("records")
        if _write(stock_id, recs):
            log.info("集保歷史回補完成 %s：%d 週", stock_id, len(recs))
    except SourceError as exc:
        log.warning("集保歷史回補失敗 %s：%s", stock_id, exc.reason)
    except Exception:  # noqa: BLE001 — 背景執行緒不可炸
        log.exception("集保歷史回補異常 %s", stock_id)
    finally:
        with _lock:
            _in_progress.discard(stock_id)
