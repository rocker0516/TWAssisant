# backend/app/mlentry/monitoring/diagnostics.py
"""§23 Observation diagnostics（Spec A）：只記錄、不阻擋、不改 run status。

四個診斷共用固定 envelope：
  正常     {"evaluated": True,  "attention": bool, ...values}
  無法評估 {"evaluated": False, "attention": False, "reason": "THRESHOLD_NOT_CONFIGURED"|"NO_DATA"|"NOT_APPLICABLE"}
  例外     {"evaluated": False, "attention": False, "error_type": "<ClassName>"}   ← 訊息只進 log
門檻全部來自 monitoring.yaml 的 diagnostics 區；本檔不硬寫數值。
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3

import numpy as np
import pandas as pd

from ..data.calendar import TradingCalendar
from ..data.quality import HardFlag
from ..data.universe import EligFlag
from .health import QS

log = logging.getLogger(__name__)
_P = tuple(float(q) for q in QS)          # 參考分位對應的累積機率 (0.01 … 0.99)


# ── envelope ────────────────────────────────────────────────────────────────

def envelope_ok(attention: bool, **values) -> dict:
    return {"evaluated": True, "attention": bool(attention), **values}


def envelope_skip(reason: str, **values) -> dict:
    return {"evaluated": False, "attention": False, "reason": reason, **values}


def envelope_error(exc: BaseException) -> dict:
    return {"evaluated": False, "attention": False, "error_type": type(exc).__name__}


def safe(fn, *args, **kwargs) -> dict:
    """任何例外只進 log；回傳只含 error_type。"""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:                                 # noqa: BLE001 — 診斷永不讓 run 失敗
        log.exception("diagnostic %s failed", getattr(fn, "__name__", repr(fn)))
        return envelope_error(exc)


def _r(x, nd: int = 4):
    """numpy／NaN 安全的 JSON 數值。"""
    if x is None:
        return None
    x = float(x)
    return None if not math.isfinite(x) else round(x, nd)


# ── freshness ───────────────────────────────────────────────────────────────

def _trading_lag(cal: TradingCalendar, as_of: str, d: str | None) -> int | None:
    """as_of 與 d（皆 ISO 日期）之間的交易日差；d 非交易日取其前一交易日；d 在 as_of 之後視為 0。"""
    if d is None:
        return None
    i = int(np.searchsorted(cal.dates, str(d), side="right")) - 1
    if i < 0:
        return None
    return max(0, int(cal.pos(as_of) - i))


def latest_pipeline_watermark(con, step: str) -> dict | None:
    """最近一筆已完成（status != running）且該 step 為 ok 的 pipeline_runs；現行 pipeline 尚未落地故看不到自己。"""
    try:
        rows = con.execute(
            "SELECT id, trading_date, finished_at, steps FROM pipeline_runs "
            "WHERE status != 'running' AND steps IS NOT NULL ORDER BY trading_date DESC, id DESC LIMIT 30").fetchall()
    except sqlite3.OperationalError:                          # 合成 DB 無此表：視為無水位
        return None
    for rid, tdate, fin, steps in rows:
        try:
            parsed = json.loads(steps) if isinstance(steps, str) else steps
        except ValueError:
            continue
        if any(isinstance(s, dict) and s.get("name") == step and s.get("status") == "ok" for s in parsed or []):
            return {"pipeline_run_id": int(rid), "business_date": str(tdate), "completed_at": str(fin) if fin else None}
    return None


def freshness(as_of: str, cal: TradingCalendar, business_dates: dict[str, str | None], con, cfg: dict | None) -> dict:
    """來源逐一比對；business_date 用已載入資料的最後有值日，ingestion_watermark 用 pipeline_runs 水位。"""
    if not cfg:
        return envelope_skip("THRESHOLD_NOT_CONFIGURED")
    sources, attention = {}, False
    for name, c in cfg.items():
        c = c or {}
        mode, max_lag = c.get("mode", "business_date"), c.get("max_lag_days")
        if mode == "ingestion_watermark":
            wm = latest_pipeline_watermark(con, c.get("step", name))
            lag = _trading_lag(cal, as_of, wm["business_date"]) if wm else None
            item = {"mode": mode, "watermark_pipeline_run_id": wm["pipeline_run_id"] if wm else None,
                    "watermark_business_date": wm["business_date"] if wm else None,
                    "watermark_completed_at": wm["completed_at"] if wm else None, "lag_trading_days": lag}
        else:
            d = business_dates.get(name)
            lag = _trading_lag(cal, as_of, d)
            item = {"mode": mode, "max_date": d, "lag_days": lag}
        if lag is None:
            item["attention"] = True                          # 來源缺資料本身就值得提醒
        elif max_lag is None:
            item["attention"], item["reason"] = False, "THRESHOLD_NOT_CONFIGURED"
        else:
            item["attention"] = bool(lag > max_lag)
        attention = attention or item["attention"]
        sources[name] = item
    return envelope_ok(attention, sources=sources)


# ── sanity ──────────────────────────────────────────────────────────────────

def sanity_summary(elig_row: pd.Series, hard_row: pd.Series, cfg: dict | None) -> dict:
    """結構性髒資料彙總；只有真正的 hard flag 才進 attention，停牌／歷史不足不算。"""
    e = elig_row.to_numpy().astype(np.uint16)
    h = hard_row.reindex(elig_row.index).fillna(0).to_numpy().astype(np.uint16)
    present_mask = (e & int(EligFlag.NO_PRICE)) == 0
    present = int(present_mask.sum())
    elig = {f.name: int(((e & int(f)) != 0).sum()) for f in EligFlag}
    hard = {f.name: int(((h & int(f)) != 0).sum()) for f in HardFlag}
    hard_count = int(((h != 0) & present_mask).sum())
    ratio = (hard_count / present) if present else None
    values = {"universe_present": present, "elig": elig, "hard": hard, "hard_flag_count": hard_count, "hard_flag_ratio": _r(ratio, 6)}
    if not cfg or "min_hard_flag_count" not in cfg or "max_hard_flag_ratio" not in cfg:
        return envelope_skip("THRESHOLD_NOT_CONFIGURED", **values)
    if present == 0:
        return envelope_skip("NO_DATA", **values)
    return envelope_ok(hard_count >= int(cfg["min_hard_flag_count"]) and ratio > float(cfg["max_hard_flag_ratio"]), **values)
