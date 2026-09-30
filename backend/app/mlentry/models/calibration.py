"""B4 Calibration（§14）與 B5 Horizon Consistency（§15）。

- 只用 OOF prediction fit（禁止 train predict → fit）。
- 逐 fold 比較 Uncalibrated / Platt / Isotonic：Brier、ECE、AUC（ranking 是否被破壞）。
  fit 的資料 = 該 fold 之前所有 fold 的 OOF（時間順序，不偷看當 fold）；第一個 fold 無前置 → 留空。
- calibration_version 保存 method / fit_period / input_model_version / fit_sample_count / metrics_before / after。
- horizon consistency：P3 <= P5 <= P10 的 pool-adjacent 投影（沿 horizon 軸做 isotonic），
  對 target 與 stop 各自套用。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from ..evaluation.model_metrics import binary_metrics

METHODS = ("none", "platt", "isotonic")


class Calibrator:
    def __init__(self, method: str):
        if method not in METHODS:
            raise ValueError(method)
        self.method = method
        self._m = None

    def fit(self, p: np.ndarray, y: np.ndarray, w: np.ndarray | None = None):
        p = np.asarray(p, dtype=float); y = np.asarray(y, dtype=float)
        if self.method == "platt":
            z = np.log(np.clip(p, 1e-6, 1 - 1e-6) / (1 - np.clip(p, 1e-6, 1 - 1e-6)))
            self._m = LogisticRegression(C=1e6, max_iter=500).fit(z[:, None], y, sample_weight=w)
        elif self.method == "isotonic":
            self._m = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(p, y, sample_weight=w)
        return self

    def transform(self, p: np.ndarray) -> np.ndarray:
        p = np.asarray(p, dtype=float)
        if self.method == "none":
            return p.astype("float32")
        if self.method == "platt":
            z = np.log(np.clip(p, 1e-6, 1 - 1e-6) / (1 - np.clip(p, 1e-6, 1 - 1e-6)))
            return self._m.predict_proba(z[:, None])[:, 1].astype("float32")
        return self._m.predict(p).astype("float32")

    # JSON 序列化（不用 pickle）：Platt 存 (coef, intercept)；Isotonic 存斷點，套用時線性插值。
    def to_dict(self) -> dict:
        if self.method == "none":
            return {"method": "none"}
        if self.method == "platt":
            return {"method": "platt", "coef": float(self._m.coef_[0][0]), "intercept": float(self._m.intercept_[0])}
        return {"method": "isotonic", "x": [float(v) for v in self._m.X_thresholds_],
                "y": [float(v) for v in self._m.y_thresholds_]}

    @classmethod
    def from_dict(cls, d: dict) -> "Calibrator":
        c = cls(d["method"])
        if d["method"] == "platt":
            c._m = _PlattParams(d["coef"], d["intercept"])
        elif d["method"] == "isotonic":
            c._m = _IsoParams(np.asarray(d["x"], dtype=float), np.asarray(d["y"], dtype=float))
        return c


class _PlattParams:
    def __init__(self, coef: float, intercept: float):
        self.coef_, self.intercept_ = np.array([[coef]]), np.array([intercept])

    def predict_proba(self, z):
        p1 = 1.0 / (1.0 + np.exp(-(self.coef_[0][0] * z[:, 0] + self.intercept_[0])))
        return np.column_stack([1 - p1, p1])


class _IsoParams:
    def __init__(self, x: np.ndarray, y: np.ndarray):
        self.X_thresholds_, self.y_thresholds_ = x, y

    def predict(self, p):
        return np.interp(np.asarray(p, dtype=float), self.X_thresholds_, self.y_thresholds_)


@dataclass
class CalibrationRecord:
    task: str
    input_model_version: str
    method: str
    fit_period: tuple[str, str]
    fit_sample_count: int
    metrics_before: dict
    metrics_after: dict
    calibration_version: str = ""

    def finalize(self):
        payload = {"task": self.task, "model": self.input_model_version, "method": self.method,
                   "fit_period": list(self.fit_period), "n": self.fit_sample_count}
        self.calibration_version = "c_" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:8]
        return self


def _keep(m: dict) -> dict:
    return {k: m[k] for k in ("n", "base_rate", "auc", "brier", "ece", "top_decile_lift") if k in m}


def compare_methods_by_fold(preds: pd.DataFrame, task: str, model_version: str,
                            methods: tuple[str, ...] = METHODS) -> tuple[pd.DataFrame, pd.DataFrame, list[CalibrationRecord]]:
    """時間順序：fold k 的 calibrator 用 fold < k 的 OOF fit，再套到 fold k 評估。

    → (逐 fold 指標表, 加了各 method 校準欄的 preds, 各 fold×method 的 CalibrationRecord)。
    """
    folds = sorted(preds["fold"].unique())
    out = preds.copy()
    lab = preds["y"].notna() & (preds["w"].fillna(0) > 0)
    preds = preds[lab]
    rows, records = [], []
    for m in methods:
        out[f"pred_{m}"] = np.nan
    for i, f in enumerate(folds):
        cur = preds["fold"] == f
        prev = preds["fold"].isin(folds[:i])
        cur_all = out["fold"] == f
        y, w, p = preds.loc[cur, "y"].to_numpy(), preds.loc[cur, "w"].to_numpy(), preds.loc[cur, "pred"].to_numpy()
        before = binary_metrics(y, p, w)
        for m in methods:
            if m != "none" and prev.sum() == 0:
                continue
            cal = Calibrator(m)
            if m != "none":
                cal.fit(preds.loc[prev, "pred"].to_numpy(), preds.loc[prev, "y"].to_numpy(), preds.loc[prev, "w"].to_numpy())
            q = cal.transform(p)
            out.loc[cur_all, f"pred_{m}"] = cal.transform(out.loc[cur_all, "pred"].to_numpy())
            after = binary_metrics(y, q, w)
            rows.append({"fold": f, "method": m, **_keep(after)})
            if m != "none":
                fp = (str(preds.loc[prev, "signal_date"].min()), str(preds.loc[prev, "signal_date"].max()))
                records.append(CalibrationRecord(task, model_version, m, fp, int(prev.sum()),
                                                 _keep(before), _keep(after)).finalize())
    return pd.DataFrame(rows), out, records


def choose_method(table: pd.DataFrame, auc_tolerance: float = 0.002) -> str:
    """規則：ECE 最低者勝，但其 AUC 不得比 none 低超過 tolerance（ranking 不得被破壞）；平手取較簡單者。"""
    t = table[table["fold"].isin(table.loc[table["method"] != "none", "fold"].unique())]
    if t.empty:
        return "none"
    agg = t.groupby("method")[["ece", "brier", "auc"]].mean()
    base_auc = agg.loc["none", "auc"] if "none" in agg.index else np.nan
    order = {"none": 0, "platt": 1, "isotonic": 2}
    cands = [m for m in agg.index if m == "none" or agg.loc[m, "auc"] >= base_auc - auc_tolerance]
    best = sorted(cands, key=lambda m: (round(agg.loc[m, "ece"], 4), order[m]))
    return best[0]


def project_monotone_horizons(p_by_h: dict[int, np.ndarray]) -> dict[int, np.ndarray]:
    """P3 <= P5 <= P10：沿 horizon 軸的 isotonic（pool-adjacent-violators），逐樣本做。

    3 個點的 PAV 可閉式：先合併違反的相鄰對取平均。
    """
    hs = sorted(p_by_h)
    P = np.vstack([np.asarray(p_by_h[h], dtype=float) for h in hs]).T   # (n, H)
    n, H = P.shape
    out = P.copy()
    for i in range(n):
        v = list(out[i]); blocks = [[j] for j in range(H)]; vals = [x for x in v]
        k = 0
        while k < len(vals) - 1:
            if vals[k] > vals[k + 1]:
                merged = blocks[k] + blocks[k + 1]
                vals[k] = float(np.mean([v[j] for j in merged])); blocks[k] = merged
                del vals[k + 1]; del blocks[k + 1]
                k = max(k - 1, 0)
            else:
                k += 1
        for val, b in zip(vals, blocks):
            for j in b:
                out[i, j] = val
    return {h: out[:, j].astype("float32") for j, h in enumerate(hs)}
