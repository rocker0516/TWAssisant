"""§23、§26 Production monitoring 與 Fail-Closed：Data Quality / Feature Drift / Prediction Health / Recommendation drift。

每個 gate 回傳 GateResult(ok, reason, details)。任一 hard fail → daily_run 產 SYSTEM_NO_TRADE（仍存 shadow 預測）。
門檻為 monitoring.yaml 的 config；本檔不硬寫數值。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

QS = ("0.01", "0.05", "0.25", "0.5", "0.75", "0.95", "0.99")


@dataclass
class GateResult:
    ok: bool
    reason: str | None = None            # NO_TRADE 原因碼（DATA_HEALTH_FAIL / FEATURE_DRIFT / MODEL_HEALTH_FAIL / CALIBRATION_FAIL）
    details: dict = field(default_factory=dict)


_REF_MASS = (0.01, 0.04, 0.20, 0.25, 0.25, 0.20, 0.04, 0.01)   # 由 QS 分位切出的 8 桶參考質量


def psi(ref_q: dict, x: np.ndarray) -> float:
    """Population Stability Index：以參考分位切桶（相同邊界合併桶），比較目前樣本落桶比例。"""
    edges = np.array([ref_q[q] for q in QS], dtype=float)
    mass = list(_REF_MASS)
    # 合併重複邊界（離散特徵）
    keep_edges, keep_mass = [], [mass[0]]
    for i, e in enumerate(edges):
        if keep_edges and e <= keep_edges[-1]:
            keep_mass[-1] += mass[i + 1]
        else:
            keep_edges.append(e); keep_mass.append(mass[i + 1])
    x = x[np.isfinite(x)]
    if len(x) == 0 or not keep_edges:
        return float("nan")
    ref_p = np.array(keep_mass) / sum(keep_mass)
    cur = np.histogram(x, bins=np.concatenate([[-np.inf], keep_edges, [np.inf]]))[0] / len(x)
    eps = 1e-4
    return float(np.sum((cur - ref_p) * np.log((cur + eps) / (ref_p + eps))))


def data_quality_gate(universe_count: int, ref_universe: dict, core_missing: dict[str, float], cfg: dict) -> GateResult:
    """Completeness / Coverage：U_t 規模比訓練參考少太多、或核心價量欄缺值率過高 → DATA_HEALTH_FAIL。"""
    det = {"universe_count": universe_count, "ref_median": ref_universe["median"], "core_missing": core_missing}
    if universe_count < cfg["min_universe_ratio"] * ref_universe["median"]:
        return GateResult(False, "DATA_HEALTH_FAIL", {**det, "why": "universe shrink"})
    bad = {k: v for k, v in core_missing.items() if v > cfg["max_core_missing_rate"]}
    if bad:
        return GateResult(False, "DATA_HEALTH_FAIL", {**det, "why": "core missing", "bad": bad})
    return GateResult(True, None, det)


def feature_health_gate(snapshot: pd.DataFrame, feature_ref: dict, cfg: dict) -> GateResult:
    """股票層級特徵：當日 PSI > max(psi_hard, 該特徵訓練期每日 PSI 的 p99) → drift；
    日／類股層級特徵：當日中位值超出訓練期每日值的 [0.5%, 99.5%] → drift。
    drift 特徵數或缺值率位移特徵數 > max_features_drifted → FEATURE_DRIFT（fail-closed）。"""
    drifted, miss_shift, psis, out_of_range = [], [], {}, []
    drifted_psi: dict[str, dict] = {}
    for name, ref in feature_ref.items():
        if name not in snapshot.columns:
            continue
        x = snapshot[name].to_numpy(dtype=float)
        mr = float(np.isnan(x).mean())
        if mr - ref["missing_rate"] > cfg["max_missing_rate_shift"]:
            miss_shift.append(name)
        if ref.get("day_level"):
            med = float(np.nanmedian(x)) if np.isfinite(x).any() else np.nan
            dq = ref.get("daily_q", {})
            if np.isfinite(med) and dq and not (dq["0.005"] <= med <= dq["0.995"]):
                out_of_range.append(name); drifted.append(name)
            continue
        v = psi(ref["q"], x)
        psis[name] = round(v, 4) if np.isfinite(v) else None
        thr = max(cfg["psi_hard"], ref.get("psi_p99", cfg["psi_hard"]))
        if np.isfinite(v) and v > thr:
            drifted.append(name)
            if len(drifted_psi) < 20:
                drifted_psi[name] = {"psi": round(float(v), 4), "thr": round(float(thr), 4)}
    det = {"n_drifted": len(drifted), "drifted": drifted[:20], "out_of_range_day_level": out_of_range[:20],
           "missing_shift": miss_shift[:20], "psi_max": max((v for v in psis.values() if v is not None), default=0.0),
           "drifted_psi": drifted_psi}
    if len(drifted) > cfg["max_features_drifted"] or len(miss_shift) > cfg["max_features_drifted"]:
        return GateResult(False, "FEATURE_DRIFT", det)
    return GateResult(True, None, det)


def prediction_health_gate(preds: dict[str, np.ndarray], pred_ref: dict, cfg: dict) -> GateResult:
    """預測分布異常：mean 偏離參考超過 z 個 ref std、或全為常數 / NaN → MODEL_HEALTH_FAIL。"""
    det, bad = {}, []
    for t, p in preds.items():
        p = np.asarray(p, dtype=float)
        if not np.isfinite(p).any() or np.nanstd(p) == 0:
            bad.append(t); det[t] = "degenerate"; continue
        ref = pred_ref.get(t)
        if not ref:
            continue
        z = abs(np.nanmean(p) - ref["mean"]) / max(ref["std"], 1e-9)
        det[t] = {"mean": float(np.nanmean(p)), "ref_mean": ref["mean"], "z": round(float(z), 3)}
        if z > cfg["max_mean_shift_z"]:
            bad.append(t)
    if bad:
        return GateResult(False, "MODEL_HEALTH_FAIL", {**det, "bad": bad})
    return GateResult(True, None, det)


def recommendation_drift(day_row: dict, recent_days: pd.DataFrame | None) -> dict:
    """§23.4 只監控不阻擋：候選數／推薦數相對近 60 日中位。"""
    out = {"qualified_count": day_row["qualified_count"], "recommendation_count": day_row["recommendation_count"]}
    if recent_days is not None and len(recent_days):
        out["qualified_median_60d"] = float(recent_days["qualified_count"].median())
        out["no_trade_rate_60d"] = float(recent_days["no_trade"].mean())
    return out
