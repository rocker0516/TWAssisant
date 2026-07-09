"""會噴清單成效回測的背景重算（獨立子行程）。

統計窗口拉大後(poppability._N_DATES)單次重算約 3 分鐘。實測：放 uvicorn 內 daemon 執行緒會
與請求共用 SQLite 連線而鎖死(0% CPU 永不結束)；同一份程式在獨立程序卻能 3 分鐘跑完。故改
spawn 子行程 `python -m app.engines.poppability`(完全隔離、自帶連線)，主程序只追蹤狀態、不阻塞。
同時只跑一次；跑完子行程寫入 Setting('poppable_efficacy')，前端輪詢 status() 後重抓。
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parents[2]  # .../backend（含 app 套件，子行程 cwd）
_TIMEOUT = 1800  # 子行程上限 30 分（正常 ~3 分）

_lock = threading.Lock()
_state: dict = {"running": False, "started_at": None, "finished_at": None, "error": None}


def status() -> dict:
    with _lock:
        return dict(_state)


def trigger() -> dict:
    """啟動背景重算子行程。已在跑則回現況、不重複啟動。回傳當前狀態。"""
    with _lock:
        if _state["running"]:
            return dict(_state)
        _state.update(
            running=True, started_at=datetime.now().isoformat(timespec="seconds"),
            finished_at=None, error=None,
        )
    threading.Thread(target=_run, name="pop-efficacy-recompute", daemon=True).start()
    return status()


def _run() -> None:
    """在 daemon 執行緒裡 spawn 子行程並等它結束（執行緒只是 wait，不碰 DB → 不鎖死）。"""
    err: str | None = None
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "app.engines.poppability"],
            cwd=str(_BACKEND_DIR), capture_output=True, text=True, timeout=_TIMEOUT,
        )
        if proc.returncode != 0:
            err = (proc.stderr or "")[-800:] or f"exit {proc.returncode}"
            log.error("poppable efficacy recompute subprocess failed: %s", err)
    except subprocess.TimeoutExpired:
        err = f"逾時（>{_TIMEOUT}s）"
        log.error("poppable efficacy recompute timed out")
    except Exception as exc:  # noqa: BLE001
        err = str(exc)
        log.exception("poppable efficacy recompute failed to spawn")
    finally:
        with _lock:
            _state.update(
                running=False, finished_at=datetime.now().isoformat(timespec="seconds"), error=err,
            )
