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


# ── feature shift ───────────────────────────────────────────────────────────

def quantile_cdf_gap_7pt(ref_q: dict, x: np.ndarray) -> float | None:
    """7-point reference-quantile ECDF gap：max_i |ECDF_now(q_i) − p_i|。不是 two-sample KS，欄名固定。"""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return None
    edges = np.array([ref_q[q] for q in QS], dtype=float)
    ecdf = np.searchsorted(np.sort(x), edges, side="right") / len(x)
    return float(np.max(np.abs(ecdf - np.array(_P))))


def feature_shift(snapshot: pd.DataFrame, ref_full: dict, modes: dict[str, str], mode_source: str, cfg: dict | None) -> dict:
    """只評估 monitor_mode == continuous；mean_z／std_ratio 零除回 None＋REFERENCE_STD_ZERO。"""
    feature_ref: dict = ref_full.get("features", {})
    cfg = cfg or {}
    z_thr, gap_thr = cfg.get("mean_z_threshold"), cfg.get("gap_threshold")
    rows, skipped = [], []
    for name, ref in feature_ref.items():
        if modes.get(name) != "continuous":
            skipped.append(name); continue
        if name not in snapshot.columns:
            continue
        x = snapshot[name].to_numpy(dtype=float)
        xf = x[np.isfinite(x)]
        row = {"name": name, "mean_z": None, "std_ratio": None, "quantile_cdf_gap_7pt": None}
        if len(xf) == 0:
            row["reason"] = "NO_DATA"; rows.append(row); continue
        std = float(ref.get("std", 0.0) or 0.0)
        if std < 1e-12:
            row["reason"] = "REFERENCE_STD_ZERO"
        else:
            row["mean_z"] = _r((xf.mean() - float(ref["mean"])) / std)
            row["std_ratio"] = _r(xf.std() / std)
        row["quantile_cdf_gap_7pt"] = _r(quantile_cdf_gap_7pt(ref["q"], xf))
        rows.append(row)
    n_z = sum(1 for r in rows if r["mean_z"] is not None and z_thr is not None and abs(r["mean_z"]) > float(z_thr))
    n_gap = sum(1 for r in rows if r["quantile_cdf_gap_7pt"] is not None and gap_thr is not None and r["quantile_cdf_gap_7pt"] > float(gap_thr))
    top = sorted(rows, key=lambda r: -(r["quantile_cdf_gap_7pt"] if r["quantile_cdf_gap_7pt"] is not None else -1.0))[:10]
    values = {"n_evaluated": len(rows), "n_mean_z_gt": n_z, "n_gap_gt": n_gap, "top": top, "skipped": skipped,
              "monitor_mode_source": mode_source}
    if any(k not in cfg for k in ("mean_z_threshold", "gap_threshold", "max_features_mean_shift", "max_features_gap")):
        return envelope_skip("THRESHOLD_NOT_CONFIGURED", **values)
    if not rows:
        return envelope_skip("NO_DATA", **values)
    return envelope_ok(n_z >= int(cfg["max_features_mean_shift"]) or n_gap >= int(cfg["max_features_gap"]), **values)


# ── recommendation distribution ─────────────────────────────────────────────

def _sector_counts(sec: np.ndarray, mask: np.ndarray) -> dict[str, int]:
    v = sec[mask]
    v = v[np.isfinite(v)]
    u, c = np.unique(v.astype(int), return_counts=True)
    return {str(int(k)): int(n) for k, n in zip(u, c)}


def _terciles(series: pd.Series, sid: pd.Series, rec_mask: np.ndarray) -> dict:
    v = series.reindex(sid.to_numpy()).to_numpy(dtype=float)
    valid = np.isfinite(v)
    out = {"low": 0, "mid": 0, "high": 0, "missing_count": int((~valid).sum()), "cuts": None}
    if valid.sum() < 3:
        return out
    lo, hi = np.quantile(v[valid], [1 / 3, 2 / 3])
    rv = v[rec_mask & valid]
    out.update({"low": int((rv <= lo).sum()), "mid": int(((rv > lo) & (rv <= hi)).sum()), "high": int((rv > hi).sum()),
                "cuts": [_r(lo, 2), _r(hi, 2)]})
    return out


def recommendation_distribution(df: pd.DataFrame, sector_map: pd.Series, mcap: pd.Series, liquidity: pd.Series, cfg: dict | None) -> dict:
    """推薦相對全 U_t 的分布：score 分位、類股集中（相對 universe 佔比）、市值／流動性三分位。只記錄。"""
    sid = df["stock_id"].astype(str)
    q_mask = df["gate_pass"].to_numpy(dtype=bool); rec_mask = df["recommended"].to_numpy(dtype=bool)
    n_q, n_rec = int(q_mask.sum()), int(rec_mask.sum())
    score_q = None
    if n_q:
        s = pd.to_numeric(df.loc[q_mask, "recommendation_score"], errors="coerce").dropna()
        if len(s):
            score_q = {"p10": _r(s.quantile(0.10)), "p50": _r(s.quantile(0.50)), "p90": _r(s.quantile(0.90))}
    sec = sector_map.reindex(sid.to_numpy()).to_numpy(dtype=float)
    uni = _sector_counts(sec, np.ones(len(df), dtype=bool)); n_u = sum(uni.values())
    universe_share = {k: _r(v / n_u) for k, v in uni.items()} if n_u else {}
    rec_counts = _sector_counts(sec, rec_mask)
    max_sector = None
    if rec_counts:
        k = max(rec_counts, key=rec_counts.get)
        rs = rec_counts[k] / n_rec; us = (uni.get(k, 0) / n_u) if n_u else 0.0
        max_sector = {"sector_id": k, "recommendation_share": _r(rs), "universe_share": _r(us),
                      "overweight": _r(rs / us) if us > 0 else None}
    mcap_t = _terciles(mcap, sid, rec_mask)
    mcap_t.update({"mcap_basis": "current_company_profile_at_run_time",
                   "issued_shares_missing_count": int(mcap.reindex(sid.to_numpy()).isna().sum())})
    values = {"qualified_count": n_q, "recommendation_count": n_rec, "score_q": score_q,
              "sector": {"recommended": rec_counts, "qualified": _sector_counts(sec, q_mask), "universe_share": universe_share,
                         "max_sector": max_sector},
              "mcap_tercile": mcap_t, "liquidity_tercile": _terciles(liquidity, sid, rec_mask)}
    cfg = cfg or {}
    if any(k not in cfg for k in ("min_recommendations_for_concentration", "max_sector_share", "min_sector_overweight")):
        return envelope_skip("THRESHOLD_NOT_CONFIGURED", **values)
    att = bool(max_sector and n_rec >= int(cfg["min_recommendations_for_concentration"])
               and max_sector["recommendation_share"] > float(cfg["max_sector_share"])
               and max_sector["overweight"] is not None and max_sector["overweight"] > float(cfg["min_sector_overweight"]))
    return envelope_ok(att, **values)
