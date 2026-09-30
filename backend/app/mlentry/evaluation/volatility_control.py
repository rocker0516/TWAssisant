"""B6 Volatility-controlled diagnostics 與純波動度 hard benchmark。

問題已改寫為：在相同波動度條件下，模型能否辨識哪種波動偏向上行？
- ATR-decile conditional lift：每個（同日）ATR decile 內，target model top-q 的 Target 率 ÷ 該 bucket 基率；
  stop model bottom-q 的 Stop 率 ÷ 該 bucket 基率。聚合成 ConditionalTargetLift / ConditionalStopReduction。
- ATR Top-K benchmark：同日以 atr_pct 最高 K 檔為對照，所有推薦報告必須並列 ΔTargetRate / ΔStopRate /
  ΔReturn / ΔMAE。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def daily_pct_rank(df: pd.DataFrame, col: str) -> pd.Series:
    return df.groupby("signal_date")[col].rank(pct=True, method="first")


def daily_decile(df: pd.DataFrame, col: str, q: int = 10) -> pd.Series:
    return np.clip((daily_pct_rank(df, col) * q).astype(int), 0, q - 1)


def atr_conditional_lift(df: pd.DataFrame, score_col: str, y_col: str, *, top: bool,
                         q_score: float = 0.10, vol_col: str = "atr_pct") -> pd.DataFrame:
    """每個 ATR decile 內：score 的 top（或 bottom）q 的事件率 ÷ 該 decile 基率。

    df 需含 signal_date, vol_col, score_col, y_col, fold。回傳每 decile 一列 + 'all' 聚合列。
    """
    d = df.copy()
    d["atr_dec"] = daily_decile(d, vol_col)
    grp = d.groupby(["signal_date", "atr_dec"])[score_col]
    rk = grp.rank(method="first")
    m = grp.transform("size")
    k = np.maximum(1, np.round(q_score * m)).astype(int)
    d["sel"] = (rk > m - k) if top else (rk <= k)
    key = "n_folds_gt1" if top else "n_folds_lt1"
    rows = []
    for dec, g in d.groupby("atr_dec"):
        base = g[y_col].mean(); sel = g.loc[g["sel"], y_col].mean()
        ratios = []
        for _, x in g.groupby("fold"):
            b = x[y_col].mean()
            ratios.append(x.loc[x["sel"], y_col].mean() / b if b > 0 and x["sel"].any() else np.nan)
        ratios = np.array(ratios, dtype=float)
        rows.append({"atr_decile": int(dec), "base_rate": base, "selected_rate": sel,
                     "ratio": sel / base if base > 0 else np.nan, "n_selected": int(g["sel"].sum()),
                     "worst_fold_ratio": float(np.nanmin(ratios)) if np.isfinite(ratios).any() else np.nan,
                     key: int((ratios > 1).sum() if top else (ratios < 1).sum())})
    out = pd.DataFrame(rows)
    okr = out["ratio"].notna() & (out["n_selected"] > 0)
    agg = {"atr_decile": "all", "base_rate": out["base_rate"].mean(), "selected_rate": out["selected_rate"].mean(),
           "ratio": float(np.average(out.loc[okr, "ratio"], weights=out.loc[okr, "n_selected"])) if okr.any() else np.nan,
           "n_selected": int(out["n_selected"].sum()), "worst_fold_ratio": float(out["worst_fold_ratio"].min())}
    return pd.concat([out, pd.DataFrame([agg])], ignore_index=True)


def daily_topk(df: pd.DataFrame, score_col: str, k: int, ascending: bool = False) -> pd.Series:
    rk = df.groupby("signal_date")[score_col].rank(ascending=ascending, method="first")
    return rk <= k


def selection_stats(sel: pd.DataFrame, base_target: float, base_stop: float) -> dict:
    ev = sel["event_type"]
    return {"n": int(len(sel)), "days": int(sel["signal_date"].nunique()),
            "per_day_median": float(sel.groupby("signal_date").size().median()) if len(sel) else 0.0,
            "target_rate": float(sel["target"].mean()), "target_lift": float(sel["target"].mean() / base_target),
            "stop_rate": float(sel["stop"].mean()), "stop_ratio": float(sel["stop"].mean() / base_stop),
            "timeout_rate": float((ev == 4).mean()),
            "median_mfe": float(sel["mfe_10d"].median()), "median_mae": float(sel["mae_10d"].median()),
            "mean_mae": float(sel["mae_10d"].mean()), "mean_ret10": float(sel["return_10d"].mean())}


def benchmark_table(df: pd.DataFrame, selections: dict[str, pd.Series], k_label: str = "") -> pd.DataFrame:
    """selections: {name: bool mask}。第一個必須是 ATR benchmark；報每個相對 ATR 的 Δ。"""
    base_t, base_s = df["target"].mean(), df["stop"].mean()
    rows = {}
    for name, m in selections.items():
        rows[name] = selection_stats(df.loc[m], base_t, base_s)
        # fold stability
        bf = df.loc[m].groupby("fold")["target"].mean() / df.groupby("fold")["target"].mean()
        rows[name]["worst_fold_lift"] = float(bf.min()); rows[name]["positive_fold_ratio"] = float((bf > 1).mean())
    t = pd.DataFrame(rows).T
    ref = next(iter(selections))
    for c in ("target_rate", "stop_rate", "mean_ret10", "mean_mae"):
        t[f"d_{c}_vs_atr"] = t[c] - t.loc[ref, c]
    return t
