"""B6 Model-level 評估（附錄 B）：分類器診斷、校準、decile lift、fold 穩定性。

所有指標同時提供 row-weighted 與 day-weighted（§18.8）；day-weighted 用 w = 1/|樣本日|。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score


def ece(y: np.ndarray, p: np.ndarray, w: np.ndarray | None = None, bins: int = 10) -> float:
    w = np.ones_like(p) if w is None else w
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    tot = w.sum()
    out = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            wb = w[m].sum()
            out += wb / tot * abs(np.average(y[m], weights=w[m]) - np.average(p[m], weights=w[m]))
    return float(out)


def reliability(y: np.ndarray, p: np.ndarray, bins: int = 10) -> pd.DataFrame:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    rows = []
    for b in range(bins):
        m = idx == b
        rows.append({"bin": b, "p_mean": float(p[m].mean()) if m.any() else np.nan,
                     "y_rate": float(y[m].mean()) if m.any() else np.nan, "n": int(m.sum())})
    return pd.DataFrame(rows)


def decile_table(y: np.ndarray, p: np.ndarray, q: int = 10) -> pd.DataFrame:
    """prediction 分位（同一評估集內）→ 事件率／均值。q=10 為 decile。"""
    r = pd.Series(p).rank(pct=True, method="first").to_numpy()
    d = np.clip((r * q).astype(int), 0, q - 1)
    df = pd.DataFrame({"decile": d, "y": y, "p": p}).groupby("decile").agg(
        y_rate=("y", "mean"), p_mean=("p", "mean"), n=("y", "size")).reset_index()
    return df


def top_pct_rate(y: np.ndarray, p: np.ndarray, pct: float) -> float:
    k = max(1, int(round(len(p) * pct)))
    top = np.argsort(-p, kind="stable")[:k]
    return float(y[top].mean())


def binary_metrics(y: np.ndarray, p: np.ndarray, w: np.ndarray | None = None) -> dict:
    y = np.asarray(y, dtype=float); p = np.asarray(p, dtype=float)
    w = np.ones_like(p) if w is None else np.asarray(w, dtype=float)
    base = float(np.average(y, weights=w))
    out = {"n": int(len(y)), "base_rate": base}
    if 0 < y.sum() < len(y):
        out["auc"] = float(roc_auc_score(y, p, sample_weight=w))
        out["pr_auc"] = float(average_precision_score(y, p, sample_weight=w))
    else:
        out["auc"] = out["pr_auc"] = np.nan
    out["brier"] = float(np.average((p - y) ** 2, weights=w))
    out["ece"] = ece(y, p, w)
    for pct in (0.01, 0.05, 0.10):
        r = top_pct_rate(y, p, pct)
        out[f"top{int(pct*100)}_rate"] = r
        out[f"top{int(pct*100)}_lift"] = r / base if base > 0 else np.nan
    dec = decile_table(y, p)
    out["decile_rates"] = dec["y_rate"].round(4).tolist()
    out["decile_monotonic_pairs"] = float(np.mean(np.diff(dec["y_rate"].to_numpy()) > 0)) if len(dec) > 1 else np.nan
    out["top_decile_lift"] = float(dec["y_rate"].iloc[-1] / base) if base > 0 else np.nan
    return out


def regression_metrics(y: np.ndarray, p: np.ndarray, w: np.ndarray | None = None) -> dict:
    y = np.asarray(y, dtype=float); p = np.asarray(p, dtype=float)
    w = np.ones_like(p) if w is None else np.asarray(w, dtype=float)
    out = {"n": int(len(y)), "mae": float(np.average(np.abs(p - y), weights=w)),
           "y_mean": float(np.average(y, weights=w)), "y_median": float(np.median(y))}
    out["spearman"] = float(pd.Series(p).corr(pd.Series(y), method="spearman"))
    dec = decile_table(y, p)
    out["decile_y_mean"] = dec["y_rate"].round(4).tolist()
    out["decile_monotonic_pairs"] = float(np.mean(np.diff(dec["y_rate"].to_numpy()) > 0))
    return out


def by_fold(preds: pd.DataFrame, kind: str, day_weighted: bool = True) -> pd.DataFrame:
    """每個 fold 一列（含 day-weighted / row-weighted 指標），最後附 summary 列。"""
    fn = binary_metrics if kind == "binary" else regression_metrics
    rows = []
    for name, g in preds.groupby("fold", sort=True):
        m = fn(g["y"].to_numpy(), g["pred"].to_numpy(), g["w"].to_numpy() if day_weighted else None)
        m["fold"] = name
        rows.append(m)
    df = pd.DataFrame(rows).set_index("fold")
    return df


def fold_summary(df: pd.DataFrame, cols: tuple[str, ...]) -> pd.DataFrame:
    """Mean / Median / Worst / Std / Positive-fold ratio（§19.8）。worst 對 brier/ece/mae 取 max。"""
    lower_better = {"brier", "ece", "mae"}
    rows = {}
    for c in cols:
        if c not in df.columns:
            continue
        v = df[c].astype(float)
        rows[c] = {"mean": v.mean(), "median": v.median(),
                   "worst": v.max() if c in lower_better else v.min(), "std": v.std(ddof=0),
                   "n_folds": int(v.notna().sum())}
    return pd.DataFrame(rows).T
