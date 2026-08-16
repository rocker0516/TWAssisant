"""ML 合成挑戰：LightGBM 吃全部 PIT 特徵對 hit10 訓練，同口徑對打四因子手工分數。

紀律（時序 OOS ＋ 產品同口徑）：
  切分  train 2021-01~2023-12-15 → embargo 10 交易日 → val 2024（調參/早停）
        holdout 2025+（只看一次，最後裁判）
  比法  與線上完全相同的選股協定：硬篩（站上月線且上揚、|60日乖離|<15、流動性）
        內每日取模型分前 20% ↔ 四因子分數前 20%，日層級 命中/lift/ATR桶控波動
  特徵  只用衍生 PIT 特徵（排除原始價量水平、標籤、識別欄）
  另印  LightGBM gain 重要度 Top 20（機器認為的劇本）

用法：PYTHONIOENCODING=utf-8 python scripts/ml_synth.py
"""
from __future__ import annotations

import sys
import time

import numpy as np
import pandas as pd

_BASE = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0]
sys.path.insert(0, _BASE)
sys.path.insert(0, _BASE + "/scripts")

import mega_mine as mm  # noqa: E402

_MIN_VOL = 500 * 1000
_TOP = 0.80

# 衍生 PIT 特徵（不含原始價量水平/標籤/識別）
FEATS = [
    "atr_pct", "ma_align", "vol_ratio", "vol_trend", "c_over_ma20", "pos_52w",
    "dist_60d_high", "ret5", "ret20", "bias_20", "bias_60", "kd_k", "kd_d",
    "macd_hist", "pe", "pb",
    "inst_f5", "inst_t5", "inst_tot10", "inst_streak", "sq_ratio",
    "short_chg5", "margin_chg5",
    "mkt_bias60", "mkt_ret20",
    "sec_ret20", "sec_breadth", "peer_surge5", "rel_ret20",
    # 本輪新特徵：網絡/事件/變化值/情緒
    "sec_att5", "sec_att_chg", "node_att5", "node_att_chg", "node_surge5",
    "att_times", "att_notice5", "att_punish10",
    "inst_f5_chg", "inst_t5_chg", "vol_trend_chg", "atr_pct_chg", "ret5_accel",
    "bias20_chg", "pos52_chg20", "pe_chg20", "dh_chg5", "fg", "fg_chg5",
]


def _log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def hard_mask(df: pd.DataFrame) -> pd.Series:
    liq = df["vol_ma20"].notna() & (df["vol_ma20"] >= _MIN_VOL)
    ar = (df["c_over_ma20"] > 0) & df["ma20_up5"].fillna(False)
    return (ar & (df["bias_60"].abs() < 15) & liq).fillna(False)


def four_factor(df: pd.DataFrame) -> pd.Series:
    g = df.groupby("date")
    comp = (2 * g["atr_pct"].rank(pct=True) + g["ma_align"].rank(pct=True)
            + g["pos_52w"].rank(pct=True).fillna(0.5) + g["pb"].rank(pct=True).fillna(0.5)) / 5
    return comp


def select_top(df: pd.DataFrame, score: pd.Series) -> np.ndarray:
    """硬篩內每日前 20%（與線上橫桿同口徑）。"""
    s = score.where(df["_hard"], np.nan)
    r = s.groupby(df["date"]).rank(pct=True)
    return (df["_hard"] & (r >= _TOP)).to_numpy()


def report(tag: str, df: pd.DataFrame, mask: np.ndarray) -> None:
    ev = mm.Evaluator(df)
    r = ev.run(mask)
    if r is None:
        print(f"  {tag:<16} 樣本不足")
        return
    print(f"  {tag:<16} 命中 {r['hit']:>5}%  lift {r['lift']:>6}pp  控波動 {r['ctrl']:>5}pp (t={r['t_ctrl']})  日均選中 {r['avg_picks']} 檔")


def main() -> None:
    df = pd.read_pickle(_BASE + "/data/mega_mine_features.pkl")
    df = df[df["hit10"].notna()].reset_index(drop=True)
    df["hit"] = df["hit10"]  # Evaluator 讀 hit
    df["_hard"] = hard_mask(df)
    for c in ("att_notice5", "att_punish10"):
        df[c] = df[c].astype(float)

    ds = df["date"].astype(str)
    tr = df[ds <= "2023-12-15"].reset_index(drop=True)
    va = df[(ds >= "2024-01-01") & (ds <= "2024-12-31")].reset_index(drop=True)
    ho = df[ds >= "2025-01-01"].reset_index(drop=True)
    _log(f"train {len(tr):,} / val {len(va):,} / holdout {len(ho):,}（hit10 基率 "
         f"{tr['hit'].mean()*100:.1f}/{va['hit'].mean()*100:.1f}/{ho['hit'].mean()*100:.1f}%）")

    # 訓練窗內近乎全缺的特徵剔除（如大盤指數系列僅 2026 起有史料）
    global FEATS
    dropped = [f for f in FEATS if tr[f].notna().mean() < 0.05]
    FEATS = [f for f in FEATS if f not in dropped]
    if dropped:
        _log(f"剔除訓練窗缺料特徵：{dropped}（餘 {len(FEATS)} 個）")

    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score

    Xtr = tr[FEATS].to_numpy(np.float64)
    ytr = tr["hit"].to_numpy(np.int32)
    Xva = va[FEATS].to_numpy(np.float64)
    yva = va["hit"].to_numpy(np.int32)
    model = HistGradientBoostingClassifier(
        max_iter=600, learning_rate=0.05, max_leaf_nodes=63,
        min_samples_leaf=200, l2_regularization=5.0,
        early_stopping=True, validation_fraction=0.1, n_iter_no_change=50,
        random_state=42, scoring="roc_auc",
    )
    _log("訓練 HistGradientBoosting…")
    model.fit(Xtr, ytr)
    _log(f"迭代 {model.n_iter_} 棵；val(2024) AUC = "
         f"{roc_auc_score(yva, model.predict_proba(Xva)[:, 1]):.4f}")

    from sklearn.inspection import permutation_importance
    _log("permutation 重要度（val 抽樣 100k）…")
    rng = np.random.RandomState(0)
    idx = rng.choice(len(va), size=min(100_000, len(va)), replace=False)
    pi = permutation_importance(model, Xva[idx], yva[idx], n_repeats=3,
                                random_state=0, scoring="roc_auc", n_jobs=-1)
    imp = pd.Series(pi.importances_mean, index=FEATS).sort_values(ascending=False)
    print("\n=== Permutation 重要度（AUC 貢獻）Top 20 ===")
    for f, v in imp.head(20).items():
        print(f"  {f:<16} {v:+.4f}")

    for tag, d in (("VAL 2024", va), ("HOLDOUT 2025+", ho)):
        print(f"\n=== {tag}（同一硬篩、同前20%口徑）===")
        ml_score = pd.Series(model.predict_proba(d[FEATS].to_numpy(np.float64))[:, 1], index=d.index)
        report("ML 合成", d, select_top(d, ml_score))
        report("四因子(現行)", d, select_top(d, four_factor(d)))
        # 交集/互補觀察
        m_ml = select_top(d, ml_score)
        m_ff = select_top(d, four_factor(d))
        both = m_ml & m_ff
        print(f"  重疊率：ML∩四因子 / ML = {both.sum()/max(m_ml.sum(),1)*100:.0f}%")
        report("ML獨有", d, m_ml & ~m_ff)
        report("四因子獨有", d, m_ff & ~m_ml)


if __name__ == "__main__":
    main()
