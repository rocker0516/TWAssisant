"""情境路由矩陣 runtime 載入器。

矩陣本身在 data/ctx_matrix.json（挖掘凍結產物，見
scripts/ctx_matrix_mine.py 或對應腳本），這裡只負責讀取＋快取。
mtime 快取而非永久快取／lru_cache：重跑挖掘產出新檔後不重啟後端也能讀到新版
（與 routes.py 的 `_prob_table()` / `_ml_consensus_picks()` 同款寫法）。
"""
from __future__ import annotations

import json
import os

_MATRIX_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "ctx_matrix.json")

_cache: dict = {}


def load_matrix() -> dict | None:
    """讀凍結的情境路由矩陣。檔案不存在回 None（情境路由軌未啟用）。"""
    if not os.path.exists(_MATRIX_PATH):
        return None
    mtime = os.path.getmtime(_MATRIX_PATH)
    if _cache.get("mtime") != mtime:
        try:
            with open(_MATRIX_PATH, encoding="utf-8") as fh:
                _cache["data"] = json.load(fh)
            _cache["mtime"] = mtime
        except (ValueError, OSError):
            return _cache.get("data")  # 讀壞了就沿用上一版，不要整站沒矩陣
    return _cache.get("data")
