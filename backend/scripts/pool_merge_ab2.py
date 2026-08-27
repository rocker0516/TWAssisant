"""§0b 合流 A/B 第二輪：修掉三個瑕疵後，新特徵到底有沒有資訊？

第一輪（pool_merge_ab.py）結論：加特徵讓模型變差（all 98 欄 控波動 6.93pp
vs base 47 欄 10.77pp，−3.84pp 實質退步）。但那一輪有三個瑕疵，其中兩個
會讓結論本身失效——本腳本逐一修掉後重測。

瑕疵與修正
----------
① 指標 NaN（繼承自 ml_feature_ab.py 的既有 bug）
   holdout 有 5,953 列（0.85%）`atr_bucket` 是 NaN，`groupby(...).transform`
   會把它們丟成 NaN；只要前 2% 選中任一列，整個 top_ctrl 就變 NaN。
   → 修正：NaN 併成獨立桶 -1（保留全部列，指標恆有定義）。

② **容量混淆（最嚴重）**
   第一輪 15 次擬合**全部跑滿 600/600 棵**，early stopping 一次都沒觸發。
   原因：`validation_fraction=0.1` 是**隨機**切內部驗證集，同一交易日的相鄰列
   同時進 train 與 internal-val → 驗證分數一路上升 → 永不停止。
   ⇒ 模型是**容量受限**，不是過擬合受限。固定 600 棵卻把特徵從 47 加到 98，
     等於把同一份分裂預算攤薄到兩倍候選上。那測的是「預算夠不夠攤」，
     不是「特徵有沒有資訊」。
   → 修正：關掉壞掉的 early stopping，改做**容量掃描** max_iter ∈ _ITERS。
     若 base 在每一個容量點都贏，容量解釋才算被排除，負面結論才成立。

③ 新特徵量尺缺陷
   51 個新特徵：31 個 0/1 旗標、20 個連續值，其中 **13 個量尺是壞的**
   （max/p99 最高 39,212 倍；margin_chg10/20 有 2,240/3,768 個 inf）。
   `foreign_net`/`trust_net` 是**絕對金額**——大股票天生數字大，等於市值代理。
   籌碼行為族本來是為**規則挖掘**蓋的（旗標正是規則要的形式），被原封不動倒進 GBM。
   → 修正：inf→NaN；連續型新特徵改成**同日橫斷面百分位**（scale-free，
     這就是「重構」本身）。base 47 欄**一律不動**，否則基線失去可比性。

特徵集
------
  base       47   現行（不動）
  all_raw    98   合流原樣（僅 inf→NaN 的純 bug 修正，不算重構）
  all_rank   98   連續型新特徵 → 同日橫斷面百分位；旗標原樣
  all_lean   ~    all_rank 再砍掉點火率 <_MIN_FIRE 的稀有旗標（近常數＝噪音）

判準：控波動增量差距 ≤ 合併標準差 ＝ 沒有實質差異（沿用 no_flip 那次的教訓）。

用法
  PYTHONUTF8=1 .venv/Scripts/python scripts/pool_merge_ab2.py
  PYTHONUTF8=1 .venv/Scripts/python scripts/pool_merge_ab2.py --sets base all_rank --iters 600
輸出：data/pool_merge_ab2.json（逐擬合明細，可續跑）+ stdout
"""
from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np
import pandas as pd

_BASE = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0]
_IN = _BASE + "/data/merged_features.pkl"
_OUT = _BASE + "/data/pool_merge_ab2.json"

_TRAIN_HI, _HOLD_LO = "2024-12-31", "2025-01-01"
_TOP_PCT = 0.02
_DAILY_TOP = 3
_ITERS = (600, 1500)          # 容量掃描
_SEEDS = (42, 43)
_MIN_FIRE = 0.02              # 旗標點火率下限（all_lean 用）

_HP = dict(learning_rate=0.05, max_leaf_nodes=63, min_samples_leaf=200,
           l2_regularization=5.0, early_stopping=False)   # ② 壞掉的早停關掉


def _log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def _xs_rank(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """③ 同日橫斷面百分位（0~1）。NaN 保持 NaN，讓 GBM 走自己的缺失分支。"""
    g = df.groupby("date", observed=True)
    return pd.concat({c: g[c].rank(pct=True).astype("float32") for c in cols}, axis=1)


def main() -> None:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score

    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", nargs="*", default=None)
    ap.add_argument("--iters", nargs="*", type=int, default=list(_ITERS))
    ap.add_argument("--seeds", nargs="*", type=int, default=list(_SEEDS))
    args = ap.parse_args()

    t0 = time.time()
    df = pd.read_pickle(_IN)
    groups = df.attrs["groups"]
    base = groups["base"]
    new = groups["fund"] + groups["etf"] + groups["chip"] + groups["regime"]

    # ③-a inf → NaN（純 bug 修正，套用到全部欄）
    n_inf = int(np.isinf(df[base + new].to_numpy("float64")).sum())
    df[base + new] = df[base + new].replace([np.inf, -np.inf], np.nan)
    _log(f"inf→NaN：{n_inf:,} 個值")

    # 旗標 / 連續 分류
    flags = [c for c in new if set(df[c].dropna().unique()) <= {0.0, 1.0}]
    conts = [c for c in new if c not in flags]
    fire = {c: float(df[c].fillna(0).mean()) for c in flags}
    rare = [c for c in flags if fire[c] < _MIN_FIRE or fire[c] > 1 - _MIN_FIRE]
    _log(f"新特徵 {len(new)}：旗標 {len(flags)}（稀有 {len(rare)}）／連續 {len(conts)}")

    # ③-b 連續型新特徵 → 同日橫斷面百分位
    _log(f"重構：{len(conts)} 個連續型新特徵 → 同日橫斷面百分位 …")
    rk = _xs_rank(df, conts).add_suffix("__xr")
    df = pd.concat([df, rk], axis=1)
    rank_new = [c + "__xr" for c in conts] + flags

    sets = {
        "base": base,
        "all_raw": base + new,
        "all_rank": base + rank_new,
        "all_lean": base + [c for c in rank_new if c not in rare],
    }
    if args.sets:
        sets = {k: v for k, v in sets.items() if k in args.sets}

    d = df["date"].to_numpy().astype(str)
    tr, ho = d <= _TRAIN_HI, d >= _HOLD_LO
    y = df["hit10"].to_numpy(np.int32)
    dates_ho = d[ho]
    # ① NaN atr_bucket 併成獨立桶 -1，指標恆有定義
    atr_ho = np.nan_to_num(df["atr_bucket"].to_numpy("float64")[ho], nan=-1.0)
    base_rate = float(y[ho].mean()) * 100
    _log(f"train {tr.sum():,} / holdout {ho.sum():,}（基率 {base_rate:.2f}%）")
    _log(f"特徵集 {[(k, len(v)) for k, v in sets.items()]}；容量 {args.iters}；種子 {args.seeds}")

    def evaluate(p: np.ndarray) -> dict:
        yy = y[ho]
        k = max(1, int(len(p) * _TOP_PCT))
        top = np.argpartition(-p, k)[:k]
        dd = pd.DataFrame({"y": yy, "date": dates_ho, "atr": atr_ho, "p": p})
        exp = dd.groupby(["date", "atr"], observed=True)["y"].transform("mean").to_numpy()
        day = dd.groupby("date", observed=True)["y"].transform("mean").to_numpy()
        dd["exc"] = (yy - day) * 100
        picks = dd.sort_values("p", ascending=False).groupby("date", observed=True).head(_DAILY_TOP)
        per_day = picks.groupby("date", observed=True)["exc"].mean()
        t = float(per_day.mean() / (per_day.std(ddof=1) / np.sqrt(len(per_day))))
        return {"auc": round(float(roc_auc_score(yy, p)), 4),
                "top_hit": round(float(yy[top].mean()) * 100, 2),
                "top_ctrl": round(float((yy[top] - exp[top]).mean()) * 100, 2),
                "daily_exc": round(float(per_day.mean()), 2), "daily_t": round(t, 1)}

    runs: list[dict] = []
    if os.path.exists(_OUT):
        try:
            runs = json.load(open(_OUT, encoding="utf-8")).get("runs", [])
            _log(f"沿用既有 {len(runs)} 筆")
        except Exception:
            runs = []
    done = {(r["set"], r["iters"], r["seed"]) for r in runs}

    for name, feats in sets.items():
        X = df[feats].to_numpy(np.float32)
        for it in args.iters:
            for seed in args.seeds:
                if (name, it, seed) in done:
                    continue
                ts = time.time()
                m = HistGradientBoostingClassifier(max_iter=it, random_state=seed, **_HP)
                m.fit(X[tr], y[tr])
                r = evaluate(m.predict_proba(X[ho])[:, 1])
                r.update(set=name, iters=it, seed=seed, n_feats=len(feats),
                         secs=round(time.time() - ts))
                runs.append(r)
                _log(f"  {name:<9} it{it:<5} seed{seed} 欄{len(feats):>3}"
                     f" → AUC {r['auc']} 前2% {r['top_hit']}% 控波動 {r['top_ctrl']}pp"
                     f" 每日前3 {r['daily_exc']}pp(t={r['daily_t']}) [{r['secs']}s]")
                json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M"),
                           "base_rate": round(base_rate, 2), "rare_flags": rare,
                           "n_inf_fixed": n_inf, "runs": runs},
                          open(_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        del X

    # ── 彙總：逐容量點對照 ────────────────────────────────────────────────
    R = pd.DataFrame(runs)
    R = R[R["set"].isin(sets) & R["iters"].isin(args.iters)]
    print(f"\n=== holdout 2025+（基率 {base_rate:.2f}%；{len(args.seeds)} 種子 mean±sd）===")
    for it in args.iters:
        S = R[R["iters"] == it]
        if S.empty:
            continue
        a = S.groupby("set", observed=True).agg(
            n=("n_feats", "first"), auc=("auc", "mean"),
            hit=("top_hit", "mean"), hit_sd=("top_hit", "std"),
            ctrl=("top_ctrl", "mean"), ctrl_sd=("top_ctrl", "std"),
        ).reindex([k for k in sets if k in S["set"].unique()])
        print(f"\n-- max_iter = {it} --")
        print(f"{'特徵集':<10}{'欄':>4}{'AUC':>9}{'前2%命中':>16}{'控波動增量':>16}{'vs base':>12}")
        b = a.loc["base"] if "base" in a.index else None
        for name, r in a.iterrows():
            delta = ""
            if b is not None and name != "base":
                dc = r.ctrl - b.ctrl
                sd = float(np.hypot(r.ctrl_sd, b.ctrl_sd))
                mark = "✓" if dc > sd else ("~" if abs(dc) <= sd else "✗")
                delta = f"{dc:+7.2f}pp {mark}"
            print(f"{name:<10}{int(r.n):>4}{r.auc:>9.4f}{r.hit:>11.2f}±{r.hit_sd:<4.2f}"
                  f"{r.ctrl:>11.2f}±{r.ctrl_sd:<4.2f}{delta:>12}")
    print("\n✓＝超出合併sd（實質增益）  ~＝噪音內  ✗＝實質退步")
    _log(f"完成（{time.time()-t0:.0f}s）→ {_OUT}")


if __name__ == "__main__":
    main()
