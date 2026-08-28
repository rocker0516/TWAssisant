"""Level 1 Evaluation（FRS §12–13）。

所有指標都以「日」為單位先算橫斷面，再彙總：
- Rank IC：每日 Spearman(score, fwd_ret | U_t)。矩陣制向量化（先 rank 再逐列 Pearson）。
- Mean IC / IC t-stat / ICIR（= Mean IC / Std IC）。
- 分位分析：每日把 score 切 n 分位，看各分位的實際未來報酬 → 單調性（§13）。
- Top-K spread：Top 分位均值 − Bottom 分位均值。

score 與 fwd 皆為矩陣（index=date, columns=stock_id）；fwd 已套 U_t 遮罩
（來自 targets.build_targets），score 在遮罩外的值會被 fwd 的 NaN 自然排除。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def daily_rank_ic(score: pd.DataFrame, fwd: pd.DataFrame) -> pd.Series:
    """每日 Spearman IC。兩邊都 NaN 對齊：任一為 NaN 的格子不進當日相關。"""
    s = score.where(fwd.notna())
    f = fwd.where(s.notna())
    sr = s.rank(axis=1)
    fr = f.rank(axis=1)
    sx = sr.sub(sr.mean(axis=1), axis=0)
    fx = fr.sub(fr.mean(axis=1), axis=0)
    num = (sx * fx).sum(axis=1)
    den = np.sqrt((sx**2).sum(axis=1) * (fx**2).sum(axis=1))
    ic = num / den.replace(0, np.nan)
    n = s.notna().sum(axis=1)
    return ic.where(n >= 30)  # 橫斷面太小的日子不計


def ic_summary(ic: pd.Series) -> dict:
    ic = ic.dropna()
    if ic.empty:
        return {"n_days": 0}
    mean, std = float(ic.mean()), float(ic.std())
    return {
        "n_days": int(len(ic)),
        "mean_ic": round(mean, 4),
        "std_ic": round(std, 4),
        "icir": round(mean / std, 3) if std > 0 else None,
        "t_stat": round(mean / std * np.sqrt(len(ic)), 2) if std > 0 else None,
        "pct_positive": round(float((ic > 0).mean()), 3),
    }


def quantile_returns(score: pd.DataFrame, fwd: pd.DataFrame, n_q: int = 10) -> pd.DataFrame:
    """每日依 score 分位（1=最低, n_q=最高），回各分位的日均 fwd（index=date）。"""
    s = score.where(fwd.notna())
    q = s.rank(axis=1, pct=True).mul(n_q).apply(np.ceil).clip(1, n_q)
    rows = {}
    for k in range(1, n_q + 1):
        rows[k] = fwd.where(q == k).mean(axis=1)
    return pd.DataFrame(rows)


def quantile_summary(score: pd.DataFrame, fwd: pd.DataFrame, n_q: int = 10) -> dict:
    """分位 → 平均未來報酬（%）、單調性（分位序 vs 均值的 Spearman）、Top-Bottom spread。"""
    qr = quantile_returns(score, fwd, n_q)
    means = qr.mean() * 100  # %
    order = pd.Series(range(1, n_q + 1), index=means.index)
    mono = float(order.corr(means, method="spearman"))
    return {
        "quantile_mean_pct": [round(float(v), 3) for v in means],
        "monotonicity": round(mono, 3),
        "top_bottom_spread_pct": round(float(means.iloc[-1] - means.iloc[0]), 3),
        "top_minus_mid_pct": round(float(means.iloc[-1] - means.iloc[n_q // 2]), 3),
    }


def topk_summary(score: pd.DataFrame, fwd: pd.DataFrame,
                 ks: tuple[int, ...] = (20, 50)) -> dict:
    """Top-K 專用指標（§8 產品形態是 Top-K，直接量頂端而非全分布）。

    每日取 score 前 K 名（fwd 缺值者不計名額）：
    - mean_ret_pct / median_ret_pct：Top-K 日均/日中位未來報酬
    - excess_pct：Top-K 日均 − 當日 Universe 均值（橫斷面超額）
    - day_win_rate：Top-K 當日均值贏過 Universe 均值的日子占比
    """
    out = {}
    univ_mean = fwd.mean(axis=1)
    rk = score.where(fwd.notna()).rank(axis=1, ascending=False, method="first")
    for k in ks:
        sel = fwd.where(rk <= k)
        daily = sel.mean(axis=1)
        excess = (daily - univ_mean).dropna()
        out[f"top{k}"] = {
            "mean_ret_pct": round(float(daily.mean()) * 100, 3),
            "median_ret_pct": round(float(sel.stack().median()) * 100, 3),
            "excess_pct": round(float(excess.mean()) * 100, 3),
            "day_win_rate": round(float((excess > 0).mean()), 3),
        }
    return out


def evaluate(score: pd.DataFrame, fwd: pd.DataFrame, n_q: int = 10) -> dict:
    """單一 (score, horizon) 的完整評估包。"""
    ic = daily_rank_ic(score, fwd)
    out = ic_summary(ic)
    out.update(quantile_summary(score, fwd, n_q))
    out["topk"] = topk_summary(score, fwd)
    return out


def evaluate_by_period(score: pd.DataFrame, fwd: pd.DataFrame,
                       periods: dict[str, tuple[str, str]]) -> dict:
    """依期間切開評估（穩定性檢查，§12「跨時間區間」）。periods: 名稱 → (起, 迄)。"""
    out = {}
    for name, (a, b) in periods.items():
        idx = (score.index >= a) & (score.index <= b)
        if idx.sum() == 0:
            continue
        out[name] = evaluate(score.loc[idx], fwd.loc[idx])
    return out
