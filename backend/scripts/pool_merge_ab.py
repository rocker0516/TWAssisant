"""§0 特徵池合流 A/B：新增的 51 個特徵能不能動 holdout？

問題
----
`ml_synth.FEATS` 只有 47 個特徵、全來自 #1 池。merge_feature_pools.py 合流後有 98 個。
本腳本用**與 ml_feature_ab.py 完全相同的協定**測「多出來的 51 個有沒有用」，
並把增益歸因到族（基本面／ETF事件／籌碼行為）。

同口徑（任何漂移都會讓對比失效，勿改）
--------------------------------------
  切分   train 2021-01-01~2024-12-31 / holdout 2025-01-01+（時序，非隨機）
  模型   HistGradientBoostingClassifier(600, lr .05, leaves 63, min_leaf 200, l2 5.0,
         early_stopping, val_frac .1, n_iter_no_change 50, scoring roc_auc)
  目標   hit10
  指標   AUC ／ 全 holdout 前 2% 命中 ／ **前 2% 控波動增量**（同日同 ATR 桶期望命中之差）
  基線   現行 47 特徵 = ml_feature_ab.json 的 `no_flip`
         （AUC .7134 / 前2%命中 49.48% / 控波動 9.90pp；基率 16.62%）

相對 ml_feature_ab.py 的兩處增補
--------------------------------
  1. **3 個種子**取平均±標準差。既有結論吃過一次虧：`no_flip` 曾以 1.18pp 勝出，
     market_index 回補後被推翻，證實單種子的 1pp 級差距不可信。
     判準沿用該教訓——**差距落在一個標準差內 ＝ 沒有實質差異**。
  2. 加報 **每日前 3 檔** 的同日配對超額。wave_hit_challenge §5.3 判 GBM「零移轉」
     用的就是這個量尺（挖掘 +18.7pp → holdout +0.8pp），加報才能跨研究比較。

用法
  PYTHONUTF8=1 .venv/Scripts/python scripts/pool_merge_ab.py
  PYTHONUTF8=1 .venv/Scripts/python scripts/pool_merge_ab.py --sets base all --seeds 42
輸出：data/pool_merge_ab.json（逐種子明細＋彙總）+ stdout
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

_BASE = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0]
_IN = _BASE + "/data/merged_features.pkl"
_OUT = _BASE + "/data/pool_merge_ab.json"

_TRAIN_LO, _TRAIN_HI = "2021-01-01", "2024-12-31"
_HOLD_LO = "2025-01-01"
_TOP_PCT = 0.02
_DAILY_TOP = 3
_SEEDS = (42, 43, 44)

_HP = dict(max_iter=600, learning_rate=0.05, max_leaf_nodes=63,
           min_samples_leaf=200, l2_regularization=5.0,
           early_stopping=True, validation_fraction=0.1, n_iter_no_change=50,
           scoring="roc_auc")


def _log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def _sets(groups: dict) -> dict:
    b = groups["base"]
    return {
        "base": b,                                                   # 現行 47
        "+fund": b + groups["fund"],                                 # ＋基本面族
        "+etf": b + groups["etf"],                                   # ＋ETF事件族
        "+chip": b + groups["chip"] + groups["regime"],              # ＋籌碼行為族
        "all": b + groups["fund"] + groups["etf"] + groups["chip"] + groups["regime"],
    }


def _evaluate(p, y, dates, atr) -> dict:
    """與 ml_feature_ab._evaluate 同口徑，另加每日前 N 檔的同日配對超額。"""
    from sklearn.metrics import roc_auc_score

    auc = float(roc_auc_score(y, p))
    k = max(1, int(len(p) * _TOP_PCT))
    top = np.argpartition(-p, k)[:k]
    hit_top = float(y[top].mean()) * 100

    d = pd.DataFrame({"y": y, "date": dates, "atr": atr})
    exp = d.groupby(["date", "atr"], observed=True)["y"].transform("mean").to_numpy()
    ctrl = float((y[top] - exp[top]).mean()) * 100

    # 每日前 N 檔：命中 − 當日全市場基率（wave_hit_challenge §5.3 同量尺）
    dd = pd.DataFrame({"y": y, "date": dates, "p": p})
    day_base = dd.groupby("date", observed=True)["y"].transform("mean").to_numpy()
    dd["exc"] = (dd["y"] - day_base) * 100
    picks = dd.sort_values("p", ascending=False).groupby("date", observed=True).head(_DAILY_TOP)
    per_day = picks.groupby("date", observed=True)["exc"].mean()
    n_d = len(per_day)
    t = float(per_day.mean() / (per_day.std(ddof=1) / np.sqrt(n_d))) if n_d > 1 else float("nan")

    return {"auc": round(auc, 4), "top_hit": round(hit_top, 2), "top_ctrl": round(ctrl, 2),
            "daily_exc": round(float(per_day.mean()), 2), "daily_t": round(t, 1),
            "n_top": int(k), "n_days": int(n_d)}


def main() -> None:
    from sklearn.ensemble import HistGradientBoostingClassifier

    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", nargs="*", default=None)
    ap.add_argument("--seeds", nargs="*", type=int, default=list(_SEEDS))
    args = ap.parse_args()

    t0 = time.time()
    df = pd.read_pickle(_IN)
    groups = df.attrs["groups"]
    sets = _sets(groups)
    if args.sets:
        sets = {k: v for k, v in sets.items() if k in args.sets}

    d = df["date"].to_numpy().astype(str)
    tr = (d >= _TRAIN_LO) & (d <= _TRAIN_HI)
    ho = d >= _HOLD_LO
    y = df["hit10"].to_numpy(np.int32)
    dates_ho, atr_ho = d[ho], df["atr_bucket"].to_numpy()[ho]
    base_rate = float(y[ho].mean()) * 100
    _log(f"train {tr.sum():,} / holdout {ho.sum():,}（holdout hit10 基率 {base_rate:.2f}%）")
    _log(f"特徵集 {list(sets)}；種子 {args.seeds}")

    runs: list[dict] = []
    if os.path.exists(_OUT):                      # 續跑：已完成的 (set,seed) 不重算
        try:
            runs = json.load(open(_OUT, encoding="utf-8")).get("runs", [])
            if runs:
                _log(f"沿用既有 {len(runs)} 筆結果（同 set+seed 不重算）")
        except Exception:
            runs = []
    done = {(r["set"], r["seed"]) for r in runs}

    for name, feats in sets.items():
        X = df[feats].to_numpy(np.float32)
        for seed in args.seeds:
            if (name, seed) in done:
                continue
            ts = time.time()
            m = HistGradientBoostingClassifier(random_state=seed, **_HP)
            m.fit(X[tr], y[tr])
            r = _evaluate(m.predict_proba(X[ho])[:, 1], y[ho], dates_ho, atr_ho)
            r.update(set=name, seed=seed, n_feats=len(feats), trees=int(m.n_iter_),
                     secs=round(time.time() - ts))
            runs.append(r)
            _log(f"  {name:<6} seed{seed} 特徵{len(feats):>3} 棵{m.n_iter_:>4}"
                 f" → AUC {r['auc']} 前2%命中 {r['top_hit']}% 控波動 {r['top_ctrl']}pp"
                 f" 每日前3超額 {r['daily_exc']}pp(t={r['daily_t']}) [{r['secs']}s]")
            json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M"),
                       "base_rate": round(base_rate, 2), "top_pct": _TOP_PCT,
                       "daily_top": _DAILY_TOP, "groups": groups, "runs": runs},
                      open(_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        del X

    # ── 彙總 ──────────────────────────────────────────────────────────────
    R = pd.DataFrame(runs)
    R = R[R["set"].isin(sets)]
    agg = R.groupby("set", observed=True).agg(
        n_feats=("n_feats", "first"), seeds=("seed", "count"),
        auc=("auc", "mean"),
        hit=("top_hit", "mean"), hit_sd=("top_hit", "std"),
        ctrl=("top_ctrl", "mean"), ctrl_sd=("top_ctrl", "std"),
        exc=("daily_exc", "mean"), exc_sd=("daily_exc", "std"),
    ).reindex([k for k in sets if k in R["set"].unique()])

    print(f"\n=== holdout 2025+（基率 {base_rate:.2f}%，{len(args.seeds)} 種子 mean±sd）===")
    print(f"{'特徵集':<8}{'欄':>4}{'AUC':>8}{'前2%命中':>16}{'控波動增量':>16}{'每日前3超額':>16}")
    for name, r in agg.iterrows():
        print(f"{name:<8}{int(r.n_feats):>4}{r.auc:>8.4f}"
              f"{r.hit:>11.2f}±{r.hit_sd:<4.2f}"
              f"{r.ctrl:>11.2f}±{r.ctrl_sd:<4.2f}"
              f"{r.exc:>11.2f}±{r.exc_sd:<4.2f}")

    if "base" in agg.index:
        b = agg.loc["base"]
        print(f"\n--- 相對 base 的增量（判準：差距 ≤ 1 個 sd ＝ 沒有實質差異）---")
        for name, r in agg.iterrows():
            if name == "base":
                continue
            dh, dc = r.hit - b.hit, r.ctrl - b.ctrl
            pool_sd = float(np.hypot(r.ctrl_sd, b.ctrl_sd))
            verdict = "實質增益" if dc > pool_sd else ("噪音內" if abs(dc) <= pool_sd else "實質退步")
            print(f"  {name:<7} 前2%命中 {dh:+6.2f}pp   控波動 {dc:+6.2f}pp"
                  f"（合併sd {pool_sd:.2f}）  → {verdict}")

    _log(f"完成（{time.time()-t0:.0f}s）→ {_OUT}")


if __name__ == "__main__":
    main()
