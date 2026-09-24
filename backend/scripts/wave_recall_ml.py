"""波段軌補漏實驗：Model A（ML 分數）能不能接住規則盒外的標的？

問題（2026-08-25）
------------------
波段軌四風格全是門檻交集盒（wave.py）：explosive atr>7%、strong pos>0.8、
story pb/pe/atr、crash atr>9%∧大盤≤−2.3∧流動篩。盒外標的一律不可見，漏的形式有二：
  a) 盒邊近失——差一個門檻就進盒（atr 6.9% 的完美排列股）
  b) 靜默日——大盤正常時 crash 整條熄火；其他盒也非天天有貨
Model A 已驗證「全市場層有真訊號」（pool_merge_ab base 47 欄：holdout 前 2% 命中
51.6%、控波動 +10.5pp）、「窄池內零移轉」（wave_challenge §5.3）。補漏用的是前者：
ML 當**回收網**，接規則沒接到的，而非在規則清單內精排（後者已否證）。

紀律
----
- 模型與協定沿用 pool_merge_ab：47 特徵、HistGBM it600、train ≤2024-12-31。
- 評估只在 holdout 2025+（模型無法對挖掘窗做合法評估——它是用挖掘窗訓的）。
- 一律報同日全市場配對超額；crash 話術照舊：段級離散必報，單一數字不可信。
- 風格遮罩是研究層重建（product 端另有遲滯/乾淨池細節），先 sanity check：
  重建 crash 的 holdout 命中須落在定版 67.1% 附近，偏太多＝遮罩重建錯，結果作廢。

章節
----
  §1 miss    規則日產量：四盒＋聯集的 檔/日 分布、零檔日占比（漏的規模有多大）
  §2 sanity  重建遮罩 vs 已知定版數字
  §3 recover ML 回收網：全市場乾淨池內每日 top-k（k=3/5/10），扣掉規則已選者，
             回收標的的 hit10／同日超額／同日同ATR桶增量——與規則清單同表對照
  §4 where   回收的是誰：距各盒門檻的距離分布（盒邊近失 vs 全新地帶）、
             規則亮燈日 vs 靜默日拆開報
輸出：stdout + data/wave_recall_ml.json
用法：PYTHONUTF8=1 .venv/Scripts/python scripts/wave_recall_ml.py
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
_OUT = _BASE + "/data/wave_recall_ml.json"
_TRAIN_HI, _HOLD_LO = "2024-12-31", "2025-01-01"
_KS = (3, 5, 10)

# wave.py 定版門檻（研究層重建）
_EXPL_ATR, _STRONG_POS, _STRONG_OVER = 0.07, 0.8, 0.23
_STORY_PB, _STORY_PE, _STORY_ATR = 5.5, 56.0, 0.049
_CRASH_ATR, _CRASH_MKT, _CRASH_PX, _CRASH_TURN = 0.09, -2.3, 20.0, 1e8

_HP = dict(max_iter=600, learning_rate=0.05, max_leaf_nodes=63, min_samples_leaf=200,
           l2_regularization=5.0, early_stopping=False, random_state=42)


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

    # ── 風格遮罩重建 ─────────────────────────────────────────────────────
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

    m_expl = clean & (atr > _EXPL_ATR) & (close > ma20) & up20
    m_strong = clean & (pos > _STRONG_POS) & (over20 > _STRONG_OVER)
    m_story = clean & (pb > _STORY_PB) & (pe > _STORY_PE) & (atr > _STORY_ATR)
    m_crash = clean & (atr > _CRASH_ATR) & (close >= _CRASH_PX) & (turn >= _CRASH_TURN) \
        & (mkt <= _CRASH_MKT)
    m_rule = m_expl | m_strong | m_story | m_crash
    masks = {"explosive": m_expl, "strong": m_strong, "story": m_story,
             "crash": m_crash, "union": m_rule}

    y = df["hit10"].to_numpy(np.int32)
    day_base = pd.Series(y, dtype=float).groupby(pd.Series(d)).transform("mean").to_numpy()

    # §1 產量 + §2 sanity ---------------------------------------------------
    n_days_ho = len(np.unique(d[ho]))
    print(f"\n=== §1 規則日產量（holdout 2025+，共 {n_days_ho} 個交易日）===")
    print(f"{'盒':<10}{'總檔':>7}{'檔/日':>8}{'亮燈日':>7}{'hit10':>8}{'同日超額':>9}")
    sanity = {}
    for name, m in masks.items():
        mh = m & ho
        n = int(mh.sum())
        days = len(np.unique(d[mh])) if n else 0
        hit = float(y[mh].mean()) * 100 if n else float("nan")
        exc = float((y[mh] - day_base[mh]).mean()) * 100 if n else float("nan")
        sanity[name] = {"n": n, "days": days, "hit": round(hit, 1), "exc": round(exc, 1)}
        print(f"{name:<10}{n:>7}{n/max(days,1):>8.1f}{days:>7}{hit:>7.1f}%{exc:>8.1f}pp")
    zero_days = n_days_ho - sanity["union"]["days"]
    print(f"聯集零檔日：{zero_days}/{n_days_ho}（{zero_days/n_days_ho*100:.0f}%）")
    print(f"§2 sanity：重建 crash holdout 命中 {sanity['crash']['hit']}%（定版 67.1%）")

    # §3 ML 回收網 ----------------------------------------------------------
    feats = [f for f in ml_synth.FEATS if f in df.columns and df[f].notna().mean() >= 0.05]
    _log(f"訓練 Model A（{len(feats)} 特徵，it600）…")
    m = HistGradientBoostingClassifier(**_HP)
    X = df[feats].to_numpy(np.float32)
    m.fit(X[tr], y[tr])
    p = np.full(len(df), np.nan)
    p[ho] = m.predict_proba(X[ho])[:, 1]

    # 回收池：乾淨池 ∧ 可交易（成交值≥1億）∧ holdout
    pool = clean & (turn >= _CRASH_TURN) & ho
    H = pd.DataFrame({"date": d[pool], "y": y[pool], "p": p[pool],
                      "rule": m_rule[pool], "day_base": day_base[pool],
                      "atrb": df["atr_bucket"].to_numpy(float)[pool],
                      "crash_day": (mkt[pool] <= _CRASH_MKT)})
    exp_atrb = H.groupby(["date", "atrb"], observed=True)["y"].transform("mean")
    H["ctrl"] = H["y"] - exp_atrb
    H["exc"] = (H["y"] - H["day_base"]) * 100

    rule_h = H[H["rule"]]
    print(f"\n=== §3 回收網 vs 規則（holdout，回收池={len(H):,} 列）===")
    print(f"{'清單':<16}{'總檔':>7}{'檔/日':>7}{'hit10':>8}{'同日超額':>9}{'控ATR增量':>10}")

    def row(label, S):
        n = len(S)
        days = S["date"].nunique()
        print(f"{label:<16}{n:>7}{n/max(days,1):>7.1f}{S['y'].mean()*100:>7.1f}%"
              f"{S['exc'].mean():>8.1f}pp{S['ctrl'].mean()*100:>9.1f}pp")
        return {"n": int(n), "per_day": round(n / max(days, 1), 1),
                "hit": round(float(S["y"].mean()) * 100, 1),
                "exc": round(float(S["exc"].mean()), 1),
                "ctrl": round(float(S["ctrl"].mean()) * 100, 1)}

    out3 = {"rule_union": row("規則聯集", rule_h)}
    for k in _KS:
        topk = H.sort_values("p", ascending=False).groupby("date", observed=True).head(k)
        rec = topk[~topk["rule"]]
        out3[f"ml_top{k}"] = row(f"ML top{k}(全)", topk)
        out3[f"recover_top{k}"] = row(f"  ├回收(盒外)", rec)
        out3[f"overlap_top{k}"] = row(f"  └重疊(盒內)", topk[topk["rule"]])

    # §4 回收的是誰 ---------------------------------------------------------
    k = 5
    topk = H.sort_values("p", ascending=False).groupby("date", observed=True).head(k)
    rec_idx = topk[~topk["rule"]].index
    rec_mask = np.zeros(len(df), bool)
    rec_mask[np.flatnonzero(pool)[H.index.get_indexer(rec_idx)]] = True

    print(f"\n=== §4 回收標的（top{k}，n={rec_mask.sum()}）長什麼樣 ===")
    qs = [10, 50, 90]
    for feat, arr, box in (("atr_pct", atr * 100, "expl 7% / crash 9%"),
                           ("pos_52w", pos, "strong 0.8"),
                           ("over_ma20", over20 * 100, "strong 23%"),
                           ("pe", pe, "story 56")):
        v = arr[rec_mask]
        v = v[~np.isnan(v)]
        if len(v):
            p10, p50, p90 = np.percentile(v, qs)
            print(f"  {feat:<10} p10/50/90 = {p10:>7.2f} /{p50:>7.2f} /{p90:>7.2f}   門檻: {box}")
    rec_df = topk[~topk["rule"]]
    for lbl, S in (("規則亮燈日", rec_df[rec_df["date"].isin(rule_h["date"].unique())]),
                   ("規則靜默日", rec_df[~rec_df["date"].isin(rule_h["date"].unique())]),
                   ("崩勢日(mkt≤−2.3)", rec_df[rec_df["crash_day"]]),
                   ("正常日", rec_df[~rec_df["crash_day"]])):
        if len(S):
            print(f"  {lbl:<14} n={len(S):>5}  hit {S['y'].mean()*100:5.1f}%"
                  f"  超額 {S['exc'].mean():+6.1f}pp  控ATR {S['ctrl'].mean()*100:+6.1f}pp")

    json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M"), "sanity": sanity,
               "zero_days": zero_days, "n_days_ho": n_days_ho, "recover": out3},
              open(_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    _log(f"完成（{time.time()-t0:.0f}s）→ {_OUT}")


if __name__ == "__main__":
    main()
