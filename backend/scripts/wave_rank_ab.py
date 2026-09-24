"""波段軌排序 A/B：四盒聯集內，ML 排序 vs 現行會噴分數排序，同 N 對打。

承 wave_recall_ml.py（2026-08-25）的發現：
  - 四盒聯集 holdout 日產 26.7 檔、零檔日 0——「漏標的」不是盒太窄，是排序沒把
    好標的浮上來（聯集 43% 裡藏著 規則∧ML 51% 的子集與 ~39% 的殘餘）。
  - 盒外回收是稀釋（36.4% < 43.1%），不做。
本腳本是上線前驗證：把「聯集內用 Model A 重排」跟「現行會噴分數排」放同一張表。

對照組
------
  pop   會噴分數（wave.py 定版）：同日橫斷面 (2×rank(atr_pct)+rank(ma_align))/3，
        在乾淨可交易池內算 rank（產品由 ScoringEngine 全市場算，這裡池差一階，
        但排序單調性不變）。單一確定性排序，無種子。
  ml    Model A（47 特徵 HistGBM it600，pool_merge_ab 協定）分數，3 種子分別報。
  藍圖上線形態是「pop 挑 ∧ ml 確認」，故另報 union 內 ml∧pop 前段的交集格。

紀律
----
  - holdout 2025+ 只評此窗（模型以挖掘窗訓練，無法對挖掘窗做合法評估）。
  - 指標：hit10／同日全市場配對超額／同日同 ATR 桶控波動增量。
  - 穩定性：3 種子 mean±sd；逐季拆解；崩勢日（mkt_bias60≤−2.3）單獨報。
  - 判準沿用：ML 勝出需 >1 個合併 sd，否則記「噪音內」。

用法：PYTHONUTF8=1 .venv/Scripts/python scripts/wave_rank_ab.py
輸出：stdout + data/wave_rank_ab.json
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np
import pandas as pd

_BASE = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0]
sys.path.insert(0, _BASE)
sys.path.insert(0, _BASE + "/scripts")

import ml_synth  # noqa: E402

_IN = _BASE + "/data/mega_mine_features.pkl"
_OUT = _BASE + "/data/wave_rank_ab.json"
_TRAIN_HI, _HOLD_LO = "2024-12-31", "2025-01-01"
_NS = (5, 10)
_SEEDS = (42, 43, 44)

_HP = dict(max_iter=600, learning_rate=0.05, max_leaf_nodes=63, min_samples_leaf=200,
           l2_regularization=5.0, early_stopping=False)


def _log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def main() -> None:
    from sklearn.ensemble import HistGradientBoostingClassifier

    t0 = time.time()
    df = pd.read_pickle(_IN)
    df = df[df["hit10"].notna()].reset_index(drop=True)
    for c in ("att_notice5", "att_punish10"):
        df[c] = df[c].astype(float)
    d = df["date"].astype(str).to_numpy()
    tr, ho = d <= _TRAIN_HI, d >= _HOLD_LO

    atr = df["atr_pct"].to_numpy(float)
    close = df["close"].to_numpy(float)
    vol = df["volume"].to_numpy(float)
    ma20 = df["ma20"].to_numpy(float)
    over20 = close / ma20 - 1.0
    turn = close * vol
    clean = ~((df["att_notice5"] > 0) | (df["att_punish10"] > 0)).to_numpy()
    up20 = df["ma20_up5"].to_numpy(float) > 0
    pos = df["pos_52w"].to_numpy(float)
    pb = df["pb"].to_numpy(float)
    pe = df["pe"].to_numpy(float)
    mkt = df["mkt_bias60"].to_numpy(float)

    m_rule = ((clean & (atr > 0.07) & (close > ma20) & up20)
              | (clean & (pos > 0.8) & (over20 > 0.23))
              | (clean & (pb > 5.5) & (pe > 56) & (atr > 0.049))
              | (clean & (atr > 0.09) & (close >= 20) & (turn >= 1e8) & (mkt <= -2.3)))
    pool = clean & (turn >= 1e8) & ho          # 乾淨可交易池
    uni = m_rule & pool                        # 評估域：聯集 ∩ 池

    y = df["hit10"].to_numpy(np.int32)
    day_base = pd.Series(y, dtype=float).groupby(pd.Series(d)).transform("mean").to_numpy()

    # 會噴分數（乾淨可交易池內同日 rank；單調性與產品一致）
    P = pd.DataFrame({"date": d[pool], "atrp": atr[pool], "align": df["ma_align"].to_numpy(float)[pool]})
    g = P.groupby("date")
    pop_score = (2 * g["atrp"].rank(pct=True) + g["align"].rank(pct=True)) / 3
    pop_full = np.full(len(df), np.nan)
    pop_full[np.flatnonzero(pool)] = pop_score.to_numpy()

    feats = [f for f in ml_synth.FEATS if f in df.columns and df[f].notna().mean() >= 0.05]
    X = df[feats].to_numpy(np.float32)

    U = pd.DataFrame({"date": d[uni], "y": y[uni], "pop": pop_full[uni],
                      "base": day_base[uni],
                      "atrb": df["atr_bucket"].to_numpy(float)[uni],
                      "crash_day": mkt[uni] <= -2.3,
                      "q": pd.Series(d[uni]).str[:7].to_numpy()})   # 月，聚成季用
    U["quarter"] = U["q"].str[:5] + ((U["q"].str[5:7].astype(int) - 1) // 3 * 3 + 1).astype(str).str.zfill(2)
    exp_atrb = U.groupby(["date", "atrb"])["y"].transform("mean")
    U["ctrl"] = (U["y"] - exp_atrb) * 100
    U["exc"] = (U["y"] - U["base"]) * 100
    _log(f"聯集∩池 {len(U):,} 列 / {U['date'].nunique()} 日（基率 {U['y'].mean()*100:.1f}%）")

    def eval_top(S, col, n):
        top = S.sort_values(col, ascending=False).groupby("date").head(n)
        return top

    def stats(top):
        return {"n": len(top), "hit": round(float(top["y"].mean()) * 100, 1),
                "exc": round(float(top["exc"].mean()), 1),
                "ctrl": round(float(top["ctrl"].mean()), 1)}

    results = {"pop": {}, "ml": {}, "both": {}}
    # pop 排序（確定性，一次）
    for n in _NS:
        results["pop"][n] = stats(eval_top(U, "pop", n))

    # ml 排序（3 種子）
    per_seed_tops = {n: [] for n in _NS}
    quarterly = []
    crash_rows = []
    for seed in _SEEDS:
        ts = time.time()
        m = HistGradientBoostingClassifier(random_state=seed, **_HP)
        m.fit(X[tr], y[tr])
        p = m.predict_proba(X[uni])[:, 1]
        U["ml"] = p
        for n in _NS:
            top = eval_top(U, "ml", n)
            per_seed_tops[n].append(stats(top))
            if n == 5:
                qq = top.groupby("quarter")["y"].agg(["mean", "size"])
                qq["seed"] = seed
                quarterly.append(qq)
                cd = top[top["crash_day"]]
                if len(cd):
                    crash_rows.append({"seed": seed, "n": len(cd),
                                       "hit": round(float(cd["y"].mean()) * 100, 1),
                                       "ctrl": round(float(cd["ctrl"].mean()), 1)})
        # 交集格：pop 前段 ∧ ml 前段（各取每日前 10 的交集）
        t_pop = eval_top(U, "pop", 10).index
        t_ml = eval_top(U, "ml", 10).index
        inter = U.loc[t_pop.intersection(t_ml)]
        results["both"].setdefault("pop10∧ml10", []).append(stats(inter))
        _log(f"  seed{seed} 完成 [{time.time()-ts:.0f}s]")

    for n in _NS:
        R = pd.DataFrame(per_seed_tops[n])
        results["ml"][n] = {"hit": round(R["hit"].mean(), 1), "hit_sd": round(R["hit"].std(), 2),
                            "exc": round(R["exc"].mean(), 1), "exc_sd": round(R["exc"].std(), 2),
                            "ctrl": round(R["ctrl"].mean(), 1), "ctrl_sd": round(R["ctrl"].std(), 2),
                            "n": int(R["n"].mean())}

    # ── 輸出 ─────────────────────────────────────────────────────────────
    print(f"\n=== 聯集內排序對打（holdout 2025+，聯集基率 {U['y'].mean()*100:.1f}%）===")
    print(f"{'排序':<14}{'N/日':>5}{'hit10':>12}{'同日超額':>12}{'控ATR增量':>12}")
    for n in _NS:
        rp, rm = results["pop"][n], results["ml"][n]
        print(f"pop 會噴分數    {n:>4}{rp['hit']:>11.1f}%{rp['exc']:>11.1f}pp{rp['ctrl']:>11.1f}pp")
        print(f"ml  ModelA     {n:>4}{rm['hit']:>8.1f}±{rm['hit_sd']:<4.1f}{rm['exc']:>8.1f}±{rm['exc_sd']:<4.1f}{rm['ctrl']:>8.1f}±{rm['ctrl_sd']:<4.1f}")
        diff = rm["hit"] - rp["hit"]
        verdict = "ML 實質勝出" if diff > rm["hit_sd"] else ("噪音內" if abs(diff) <= rm["hit_sd"] else "pop 勝出")
        print(f"   Δhit {diff:+.1f}pp（sd {rm['hit_sd']}）→ {verdict}")
    B = pd.DataFrame(results["both"]["pop10∧ml10"])
    print(f"\npop10∧ml10 交集格：{B['n'].mean():.0f} 檔  hit {B['hit'].mean():.1f}±{B['hit'].std():.1f}%"
          f"  超額 {B['exc'].mean():.1f}pp  控ATR {B['ctrl'].mean():.1f}pp")

    print(f"\n=== 逐季（ml top5，3 種子平均）vs pop top5 ===")
    Q = pd.concat(quarterly).groupby(level=0).agg(ml_hit=("mean", "mean"), n=("size", "mean"))
    pop5 = eval_top(U, "pop", 5)
    Qp = pop5.groupby("quarter")["y"].agg(["mean", "size"])
    print(f"{'季':<9}{'ml hit':>9}{'pop hit':>9}{'Δ':>7}{'檔':>6}")
    wins = 0
    for q in Q.index:
        mh = Q.loc[q, "ml_hit"] * 100
        ph = float(Qp.loc[q, "mean"]) * 100 if q in Qp.index else float("nan")
        wins += mh > ph
        print(f"{q:<9}{mh:>8.1f}%{ph:>8.1f}%{mh-ph:>+7.1f}{Q.loc[q,'n']:>6.0f}")
    print(f"ml 勝 {wins}/{len(Q)} 季")

    if crash_rows:
        C = pd.DataFrame(crash_rows)
        print(f"\n崩勢日（mkt≤−2.3，ml top5）：n≈{C['n'].mean():.0f}  "
              f"hit {C['hit'].mean():.1f}±{C['hit'].std():.1f}%  控ATR {C['ctrl'].mean():.1f}pp")
        print("（holdout 崩勢日僅一段連續行情，此格 n 小、僅供方向參考——話術紀律照舊）")

    json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M"),
               "union_base": round(float(U["y"].mean()) * 100, 1),
               "results": {k: (v if not isinstance(v, dict) else v) for k, v in results.items()},
               "quarterly_ml": {q: round(float(Q.loc[q, 'ml_hit']) * 100, 1) for q in Q.index},
               "quarterly_pop": {q: round(float(Qp.loc[q, 'mean']) * 100, 1) for q in Qp.index},
               "crash": crash_rows},
              open(_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    _log(f"完成（{time.time()-t0:.0f}s）→ {_OUT}")


if __name__ == "__main__":
    main()
