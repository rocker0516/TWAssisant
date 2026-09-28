"""Level 1 v3 評估（設計 2026-09-28 §2.5）。

v2 的 Rank IC／分位單調是「相對排序」指標；v3 目標是絕對淨報酬的 q25，驗收改看：
1. Top-K 實際淨報酬與「絕對」勝率（當日 Top-K 均值 > 0）
2. q25 校準：預測下緣被跌破的比例應 ≈ 25%
3. 「不進場」訊號：Score_max ≤ 0 的日子，U_t 等權淨報酬是否顯著低於其他日
4. Rank IC 只當診斷

score 與 net 皆為矩陣（index=date, columns=stock_id）；net 已套 U_t 且未成交為 NaN，
score 在 net 缺值處被自然排除（與 v2 evaluation 同一慣例）。
"""

from __future__ import annotations

import pandas as pd

from . import evaluation as ev


def topk_net_summary(score: pd.DataFrame, net: pd.DataFrame,
                     ks: tuple[int, ...] = (20, 50)) -> dict:
    univ = net.mean(axis=1)
    rk = score.where(net.notna()).rank(axis=1, ascending=False, method="first")
    out = {}
    for k in ks:
        sel = net.where(rk <= k)
        daily = sel.mean(axis=1).dropna()
        excess = (daily - univ.loc[daily.index]).dropna()
        out[f"top{k}"] = {
            "mean_net_pct": round(float(daily.mean()) * 100, 3),
            "median_net_pct": round(float(sel.stack().median()) * 100, 3),
            "day_win_rate": round(float((daily > 0).mean()), 3),
            "excess_pct": round(float(excess.mean()) * 100, 3),
            "n_days": int(len(daily)),
        }
    return out


def calibration_q25(score: pd.DataFrame, net: pd.DataFrame, alpha: float = 0.25) -> dict:
    both = score.notna() & net.notna()
    n = int(both.to_numpy().sum())
    if n == 0:
        return {"alpha": alpha, "breach_rate": None, "abs_error_pp": None, "n_cells": 0}
    breach = ((net < score) & both).to_numpy().sum()
    rate = float(breach) / n
    return {"alpha": alpha, "breach_rate": round(rate, 4),
            "abs_error_pp": round(abs(rate - alpha) * 100, 2), "n_cells": n}


def no_entry_summary(score: pd.DataFrame, net: pd.DataFrame) -> dict:
    smax = score.where(net.notna()).max(axis=1)
    univ = net.mean(axis=1)
    valid = smax.notna() & univ.notna()
    flagged = (smax <= 0) & valid
    other = (smax > 0) & valid
    rk = score.where(net.notna()).rank(axis=1, ascending=False, method="first")
    top20 = net.where(rk <= 20).mean(axis=1)
    n_f, n_v = int(flagged.sum()), int(valid.sum())
    f_mean = float(univ[flagged].mean()) * 100 if n_f else None
    o_mean = float(univ[other].mean()) * 100 if other.any() else None
    return {
        "n_days_flagged": n_f,
        "share_flagged": round(n_f / n_v, 4) if n_v else None,
        "univ_net_pct_flagged": round(f_mean, 3) if f_mean is not None else None,
        "univ_net_pct_other": round(o_mean, 3) if o_mean is not None else None,
        "diff_pp": (round(f_mean - o_mean, 3)
                    if (f_mean is not None and o_mean is not None) else None),
        "top20_net_pct_flagged": round(float(top20[flagged].mean()) * 100, 3) if n_f else None,
    }


def evaluate_v3(score: pd.DataFrame, net: pd.DataFrame) -> dict:
    out = ev.ic_summary(ev.daily_rank_ic(score, net))          # 診斷用
    n_eval = ev.daily_evaluation_n(score, net)
    out["evaluation_n_mean"] = round(float(n_eval.mean()), 1)
    out["evaluation_n_min"] = int(n_eval.min())
    out["topk"] = topk_net_summary(score, net)
    out["calibration"] = calibration_q25(score, net)
    out["no_entry"] = no_entry_summary(score, net)
    return out


def evaluate_v3_by_period(score: pd.DataFrame, net: pd.DataFrame,
                          periods: dict[str, tuple[str, str]]) -> dict:
    out = {}
    for name, (a, b) in periods.items():
        idx = (score.index >= a) & (score.index <= b)
        if idx.sum() == 0:
            continue
        out[name] = evaluate_v3(score.loc[idx], net.loc[idx])
    return out
