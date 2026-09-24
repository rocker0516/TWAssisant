"""帳戶層 KPI（FRS §6）。純函式，回測與 live 展示層共用。

主 KPI＝成本後 NAV 對大盤累積超額；硬約束＝帳戶 MDD ≤ 大盤同期 MDD。
"""

from __future__ import annotations

import math

import pandas as pd


def max_drawdown(nav: pd.Series) -> float:
    """最大回撤（負值分數，如 -0.23）。"""
    peak = nav.cummax()
    return float((nav / peak - 1).min())


def daily_returns(nav: pd.Series) -> pd.Series:
    return nav.pct_change().dropna()


def excess_t_stat(port: pd.Series, bench: pd.Series) -> tuple[float, float]:
    """日超額報酬的 (mean, t)。兩序列須同 index（NAV/指數皆可，先轉日報酬）。"""
    ex = (daily_returns(port) - daily_returns(bench)).dropna()
    if len(ex) < 2 or ex.std(ddof=1) == 0:
        return float(ex.mean()) if len(ex) else 0.0, 0.0
    t = ex.mean() / ex.std(ddof=1) * math.sqrt(len(ex))
    return float(ex.mean()), float(t)


def summarize(nav: pd.Series, bench: pd.Series,
              fills: pd.DataFrame) -> dict:
    """單一組合的 KPI 摘要。bench 以同起點指數化後比較。"""
    bench = bench.reindex(nav.index).ffill()
    port_ret = float(nav.iloc[-1] / nav.iloc[0] - 1)
    bench_ret = float(bench.iloc[-1] / bench.iloc[0] - 1)
    mean_ex, t_ex = excess_t_stat(nav, bench)
    filled = fills[fills.status == "filled"] if len(fills) else fills
    traded = float((filled.price * filled.qty).sum()) if len(filled) else 0.0
    costs = float((filled.fee + filled.tax).sum()) if len(filled) else 0.0
    years = max(len(nav) / 244, 1e-9)          # 台股年約 244 交易日
    avg_nav = float(nav.mean())
    n_def = int((filled.reason == "defense").sum()) if len(filled) else 0
    return {
        "days": int(len(nav)),
        "return_pct": round(port_ret * 100, 2),
        "bench_return_pct": round(bench_ret * 100, 2),
        "excess_pct": round((port_ret - bench_ret) * 100, 2),
        "daily_excess_bp": round(mean_ex * 1e4, 2),
        "excess_t": round(t_ex, 2),
        "mdd_pct": round(max_drawdown(nav) * 100, 2),
        "bench_mdd_pct": round(max_drawdown(bench) * 100, 2),
        "mdd_within_bench": bool(max_drawdown(nav) >= max_drawdown(bench)),
        "turnover_annual": round(traded / 2 / avg_nav / years, 2),
        "total_costs": round(costs, 0),
        "cost_drag_pct": round(costs / nav.iloc[0] * 100, 2),
        "n_fills": int(len(filled)),
        "n_defense_exits": n_def,
    }
