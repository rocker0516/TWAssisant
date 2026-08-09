"""條件樹挖掘：決策樹在挖掘窗自動列出候選條件組合，人再挑、判官再判。

防呆設計（沿用判官四護欄精神）：
  - 目標不是原始 hit，是 **殘差** hit − E[hit | 當天, ATR五分桶]
    → 樹學不到「高波動/多頭年」這兩個已知假答案，長出來的每片葉子
      天生就是「控波動後增量」的候選。
  - 只用挖掘窗 2021-01-01~2024-12-31；holdout 2025+ 完全不進樹。
  - 淺樹（深度≤4）＋大葉（≥8000 列）→ 每片葉子是可讀的 2~4 條件組合。
  - 每片葉子報：殘差均值（=控波動增量）、日層級 t、三段 fold 殘差、
    MAE 代價（同樣桶配對殘差）——直接照判官的尺。

用法：python scripts/pop_condition_tree.py [depth] [min_leaf]
（吃 pop_condition_judge.py 的快取；沒有會自動建）
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
from sklearn.tree import DecisionTreeRegressor

sys.path.insert(0, os.path.dirname(__file__))
from pop_condition_judge import _CACHE, _MINE_LO, _MINE_HI, _build_cache  # noqa: E402

_FEATS = [
    # 位置/趨勢
    "c_over_ma20", "bias_20", "bias_60", "ma_align", "ma20_up5",
    "pos_52w", "dist_60d_high",
    # 動能/擺盪
    "ret5", "ret20", "kd_k", "kd_d", "macd_hist",
    # 量能
    "vol_ratio", "vol_trend",
    # 波動（殘差已控桶，樹若仍想切它=桶內細結構，留著觀察）
    "atr_pct",
    # 籌碼族
    "inst_f5", "inst_t5", "inst_tot10", "inst_streak",
    "sq_ratio", "short_chg5", "margin_chg5", "pe", "pb",
    # 市場情境
    "mkt_bias60", "mkt_ret20",
    # 關聯性族（類股）
    "sec_ret20", "sec_breadth", "peer_surge5", "rel_ret20",
]


def _fmt_thr(f: str, v: float) -> str:
    if f in ("ma20_up5", "ma_align"):
        return f"{v:.1f}"
    if f in ("atr_pct", "c_over_ma20"):
        return f"{v:.3f}"
    return f"{v:.1f}"


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    depth = int(args[0]) if len(args) > 0 else 4
    min_leaf = int(args[1]) if len(args) > 1 else 8000
    screen = "--screen" in sys.argv
    excl = []
    for a in sys.argv[1:]:
        if a.startswith("--exclude="):
            excl = a.split("=", 1)[1].split(",")
    feats = [f for f in _FEATS if f not in excl]

    df = pd.read_pickle(_CACHE) if os.path.exists(_CACHE) else _build_cache()
    mine = df[(df["date"] >= _MINE_LO) & (df["date"] <= _MINE_HI)].copy()
    mine["ma20_up5"] = mine["ma20_up5"].astype(float)

    # 殘差目標：hit − E[hit | date, atr_bucket]；MAE 同法
    mine["exp_hit"] = mine.groupby(["date", "atr_bucket"])["hit"].transform("mean")
    mine["resid"] = mine["hit"] - mine["exp_hit"]
    mine["exp_mae"] = mine.groupby(["date", "atr_bucket"])["mae30"].transform("mean")
    mine["mae_resid"] = mine["mae30"] - mine["exp_mae"]

    dts_all = sorted(mine["date"].unique())
    fold_all = [set(dts_all[i * len(dts_all) // 3:(i + 1) * len(dts_all) // 3]) for i in range(3)]

    if screen:
        # 單因子掃描：逐日五分位 Q5−Q1 控波動殘差（每個特徵獨立、各自用可用列）
        print(f"=== 單因子掃描（逐日五分位 Q5−Q1 殘差 pp；fold 三段）n特徵={len(feats)} ===")
        out = []
        for f in feats:
            s = mine[["date", f, "resid"]].dropna()
            if s[f].nunique() < 10 or len(s) < 100_000:
                continue
            q = s.groupby("date")[f].transform(
                lambda x: pd.qcut(x.rank(method="first"), 5, labels=False, duplicates="drop"))
            s = s.assign(q=q)
            d = (s[s["q"] == 4].groupby("date")["resid"].mean()
                 - s[s["q"] == 0].groupby("date")["resid"].mean()).dropna()
            t = float(d.mean() / (d.std(ddof=1) / np.sqrt(len(d)))) if len(d) > 2 else np.nan
            fs = [float(d[d.index.isin(fd)].mean()) * 100 for fd in fold_all]
            out.append((f, d.mean() * 100, t, fs))
        out.sort(key=lambda r: abs(r[1]), reverse=True)
        for f, m, t, fs in out:
            sign = "✅" if abs(t) >= 3 and (fs[0] > 0) == (fs[1] > 0) == (fs[2] > 0) else "  "
            print(f"  {sign} {f:<14} Q5−Q1={m:+5.1f}pp (t={t:+5.1f})  folds[{fs[0]:+.1f}/{fs[1]:+.1f}/{fs[2]:+.1f}]")
        return

    X = mine[feats].to_numpy(dtype=float)
    ok = ~np.isnan(X).any(axis=1) & mine["resid"].notna().to_numpy()
    sub = mine[ok]
    X = X[ok]
    y = sub["resid"].to_numpy()
    print(f"挖掘窗 {sub['date'].nunique()} 日 {len(sub):,} 列，特徵 {len(feats)} 個"
          f"{'（排除 ' + ','.join(excl) + '）' if excl else ''}，"
          f"樹 depth≤{depth}、葉≥{min_leaf:,} 列，目標=控波動殘差\n")

    tree = DecisionTreeRegressor(max_depth=depth, min_samples_leaf=min_leaf, random_state=42)
    tree.fit(X, y)
    t = tree.tree_
    leaf_id = tree.apply(X)

    # 還原每片葉子的路徑條件
    paths: dict[int, list[str]] = {}

    def _walk(node: int, conds: list[str]) -> None:
        if t.children_left[node] == -1:
            paths[node] = conds
            return
        f, thr = feats[t.feature[node]], t.threshold[node]
        _walk(t.children_left[node], conds + [f"{f} <= {_fmt_thr(f, thr)}"])
        _walk(t.children_right[node], conds + [f"{f} > {_fmt_thr(f, thr)}"])

    _walk(0, [])

    dts = sorted(sub["date"].unique())
    fold_sets = [set(dts[i * len(dts) // 3:(i + 1) * len(dts) // 3]) for i in range(3)]

    rows = []
    for lid, conds in paths.items():
        m = leaf_id == lid
        g = sub[m]
        n = len(g)
        # 日層級殘差 t（≥5 檔/日的日子）
        by = g.groupby("date")["resid"]
        sizes = by.size()
        days = sizes[sizes >= 5].index
        d_res = by.mean().loc[days]
        tstat = float(d_res.mean() / (d_res.std(ddof=1) / np.sqrt(len(d_res)))) if len(d_res) > 2 else np.nan
        folds = [g[g["date"].isin(fs)]["resid"].mean() * 100 for fs in fold_sets]
        rows.append({
            "conds": conds, "n": n, "days": len(d_res),
            "picks_day": n / max(len(d_res), 1),
            "resid_pp": g["resid"].mean() * 100, "t": tstat,
            "hit": g["hit"].mean() * 100,
            "mae_cost": g["mae_resid"].mean(),
            "f1": folds[0], "f2": folds[1], "f3": folds[2],
        })
    rows.sort(key=lambda r: r["resid_pp"], reverse=True)

    print("每片葉子＝一組條件（AND）。resid=控波動增量pp；fold三段同號才算穩。")
    print("=" * 100)
    for i, r in enumerate(rows):
        sign = "✅" if (r["f1"] > 0) == (r["f2"] > 0) == (r["f3"] > 0) and abs(r["t"]) >= 3 else "  "
        print(f"[{i+1:2d}]{sign} 增量{r['resid_pp']:+5.1f}pp (t={r['t']:+5.1f})  "
              f"命中{r['hit']:4.1f}%  MAE代價{r['mae_cost']:+5.1f}pp  "
              f"日均{r['picks_day']:5.0f}檔  folds[{r['f1']:+.1f}/{r['f2']:+.1f}/{r['f3']:+.1f}]")
        for c in r["conds"]:
            print(f"        {c}")
    print("\n下一步：挑幾片葉子（或自行調整門檻成整數）→ pop_condition_judge.py 逐一判官複驗，")
    print("定稿後才開 --holdout。樹的門檻是樣本內最適切點，判官複驗時建議取整（防過擬合）。")


if __name__ == "__main__":
    main()
