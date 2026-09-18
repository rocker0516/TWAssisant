"""Level 1 分數的市值加權診斷（alpha 定義域研究 Step 0）。

問題：l1_lgbm_v2 的排名 edge 是在等權橫斷面量的；換到市值加權空間還在嗎？
（Level 2 M2 停損的根因研究——FRS §13 指向的 Level 1 端新題）

量測（全部用既有凍結分數，零建模、零調參）：
  1. 預測最弱分位（5D pct_rank 底 20%）的**市值佔比**——若趨近零，
     剔弱對市值加權組合無肉，此路結案。
  2. 剔弱組合：市值加權 U_t 剔除弱分位後再正規化，vs 市值加權 U_t 全體，
     日報酬超額（次日 close-to-close，無重疊窗）＋ t。
  3. 進攻對照：只持有頂分位（市值加權）vs 全體。
  4. 市值加權 U_t vs 加權指數的貼合度（tracking 誤差來源檢查）。

限制（文件化）：issued_shares 為現況快照（company_profile 無歷史），
增減資造成的權重誤差為二階；診斷可接受，凍結產品前需歷史股本回補。

用法：TWA_DATA_DIR=<data> python -m scripts.level1_capweight_diag
"""

from __future__ import annotations

import pickle
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

PERIODS = {"dev": ("2022-01-01", "2024-12-31"),
           "holdout": ("2025-01-01", "2099-12-31")}
WEAK = 0.2   # 弱分位門檻（與 Level 2 防禦一致）
TOP = 0.8


def _t(x: pd.Series) -> float:
    x = x.dropna()
    if len(x) < 2 or x.std(ddof=1) == 0:
        return 0.0
    return float(x.mean() / x.std(ddof=1) * np.sqrt(len(x)))


def main() -> None:
    data = Path(settings.data_dir)
    # 自產研究快取（信任來源，同 level1_run 慣例）
    payload = pickle.load(open(data / "level1_targets.pkl", "rb"))
    scores = pickle.load(open(data / "level2_scores.pkl", "rb"))["scores"]
    close = payload["close"].astype("float64")
    mask = payload["universe"]

    con = sqlite3.connect(data / "twa.db")
    sh = pd.read_sql_query(
        "SELECT stock_id, issued_shares FROM company_profile "
        "WHERE issued_shares IS NOT NULL", con,
        index_col="stock_id")["issued_shares"]
    mkt = pd.read_sql_query("SELECT date, close FROM market_index", con,
                            index_col="date")["close"]
    con.close()

    sh = sh.reindex(close.columns)
    print(f"股本覆蓋：{sh.notna().sum()}/{len(close.columns)} 檔")
    cap = close.mul(sh, axis=1).where(mask)          # 日市值（快照股本）
    pct5 = scores[5].rank(axis=1, ascending=True, pct=True)
    ret1 = close.pct_change(fill_method=None).shift(-1)   # 次日報酬（t+1）

    w_all = cap.div(cap.sum(axis=1), axis=0)
    days = pct5.dropna(how="all").index

    def cap_ret(weight_mask: pd.DataFrame) -> pd.Series:
        w = cap.where(weight_mask)
        w = w.div(w.sum(axis=1), axis=0)
        return (w * ret1).sum(axis=1, min_count=1)

    r_all = cap_ret(mask)
    r_exweak = cap_ret(mask & ~(pct5 <= WEAK))
    r_top = cap_ret(mask & (pct5 >= TOP))
    r_weak = cap_ret(mask & (pct5 <= WEAK))
    weak_capshare = w_all.where(pct5 <= WEAK).sum(axis=1, min_count=1)
    mkt_ret = mkt.reindex(close.index).ffill().pct_change().shift(-1)

    for name, (lo, hi) in PERIODS.items():
        d = days[(days >= lo) & (days <= hi)][:-1]   # 最後一日無次日報酬
        ws = weak_capshare.loc[d]
        ex_weak = (r_exweak - r_all).loc[d]
        ex_top = (r_top - r_all).loc[d]
        weak_vs = (r_weak - r_all).loc[d]
        track = (r_all - mkt_ret).loc[d]
        ann = 244
        print(f"\n── {name}（{d[0]} ~ {d[-1]}，{len(d)} 日）──")
        print(f"  弱分位市值佔比：median {ws.median():.1%} / "
              f"p10 {ws.quantile(.1):.1%} / p90 {ws.quantile(.9):.1%}")
        print(f"  剔弱 vs 全體（市值加權）：{ex_weak.mean()*1e4:+.2f} bp/日 "
              f"(t={_t(ex_weak):+.2f})  年化 {ex_weak.mean()*ann*100:+.2f}%")
        print(f"  弱分位自身 vs 全體：{weak_vs.mean()*1e4:+.2f} bp/日 "
              f"(t={_t(weak_vs):+.2f})")
        print(f"  頂分位 vs 全體（市值加權）：{ex_top.mean()*1e4:+.2f} bp/日 "
              f"(t={_t(ex_top):+.2f})  年化 {ex_top.mean()*ann*100:+.2f}%")
        print(f"  市值加權U_t vs 加權指數：{track.mean()*1e4:+.2f} bp/日 "
              f"(t={_t(track):+.2f})  年化 {track.mean()*ann*100:+.2f}%  "
              f"日std {track.std()*1e4:.1f} bp")


def turnover_probe() -> None:
    """頂分位市值加權組合的權重換手率（成本可行性前哨）。

    one-sided turnover = 0.5×Σ|w_t − w_{t-prev}|，以再平衡頻率 1/5/20 日量測；
    年化成本 ≈ 年換手 ×（買 0.1425% ＋ 賣 0.4425%）。
    """
    data = Path(settings.data_dir)
    payload = pickle.load(open(data / "level1_targets.pkl", "rb"))  # 自產快取
    scores = pickle.load(open(data / "level2_scores.pkl", "rb"))["scores"]
    close = payload["close"].astype("float64")
    mask = payload["universe"]
    con = sqlite3.connect(data / "twa.db")
    sh = pd.read_sql_query(
        "SELECT stock_id, issued_shares FROM company_profile "
        "WHERE issued_shares IS NOT NULL", con, index_col="stock_id"
    )["issued_shares"].reindex(close.columns)
    con.close()
    cap = close.mul(sh, axis=1).where(mask)
    pct5 = scores[5].rank(axis=1, ascending=True, pct=True)
    w = cap.where(pct5 >= TOP)
    w = w.div(w.sum(axis=1), axis=0)
    days = pct5.dropna(how="all").index
    for every in (1, 5, 20):
        reb = days[::every]
        wr = w.loc[reb].fillna(0.0)
        to = (wr.diff().abs().sum(axis=1) * 0.5).iloc[1:]
        ann_to = float(to.mean()) * (244 / every)
        cost = ann_to * (0.001425 + 0.004425)
        print(f"  每 {every:>2} 日再平衡：單邊換手 {to.mean():.1%}/次 "
              f"→ 年換手 {ann_to:.1f}x → 年成本 ≈ {cost*100:.2f}%")


if __name__ == "__main__":
    main()
    print("\n── 頂分位市值加權組合換手率 ──")
    turnover_probe()
