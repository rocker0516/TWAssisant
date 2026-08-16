"""ML 共識特徵表 A/B：稽核建議的三種特徵集，用時序 holdout 實測誰真的比較好。

為什麼要 A/B 而不是直接照稽核結果砍
----------------------------------
feature_contamination_audit.py 的判準是**單因子邊際**（控 ATR 的五分位命中價差）。
那對規則挖掘是對的——一條規則就是靠單因子門檻組成的。但 GBM 不同：一個單因子邊際
翻號的特徵，仍可能在交互作用裡有價值。所以挖掘池照規則砍，機率模型要實測。

三組對照
--------
  legacy    舊表：sec_ret20 + sec_breadth（市場成分 0.67/0.71，holdout −0.72/+0.13pp）
  neutral   中性化：改用 sec_rel5/10/20 + sec_breadth_rel + peer_surge5_rel（含翻號4個）
  no_flip   neutral 再砍掉 4 個 holdout 翻號的單因子（＝現行 ml_synth.FEATS）
            （dist_60d_high / ret5_accel / dh_chg5 / vol_trend_chg）

注意：mkt_bias60 / mkt_ret20 / fg / fg_chg5 三組都有，因為它們的修正（覆蓋率
1.3~4.5% → 100%）是無條件的，不構成對照維度。

評估用時序切分（train 2021-2024 / holdout 2025+），不是 build_ml_consensus 目前的
隨機 validation_fraction —— 隨機切會讓同一天的相鄰列同時進 train 和 val，高估表現。

用法：PYTHONIOENCODING=utf-8 python scripts/ml_feature_ab.py
輸出：data/ml_feature_ab.json + stdout
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

_CACHE = _BASE + "/data/mega_mine_features.pkl"   # 本專案自產的研究特徵快取
_OUT = _BASE + "/data/ml_feature_ab.json"
_TOP_PCT = 0.02       # 模型當確認器用，看的是最高分那一小撮的精準度


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


_LEGACY_SWAP = {
    "sec_rel5": None, "sec_rel10": None, "sec_rel20": "sec_ret20",
    "sec_breadth_rel": "sec_breadth", "peer_surge5_rel": None,
}


def _legacy_feats() -> list[str]:
    out = []
    for f in ml_synth.FEATS_WITH_FLIP:
        if f in _LEGACY_SWAP:
            r = _LEGACY_SWAP[f]
            if r:
                out.append(r)
        else:
            out.append(f)
    return out


def _evaluate(model, X, y, dates, atr, name: str) -> dict:
    from sklearn.metrics import roc_auc_score

    p = model.predict_proba(X)[:, 1]
    auc = float(roc_auc_score(y, p))

    # 最高分 _TOP_PCT 的命中率（確認器最在意的是這一段）
    k = max(1, int(len(p) * _TOP_PCT))
    top = np.argpartition(-p, k)[:k]
    hit_top = float(y[top].mean()) * 100

    # 控 ATR：同日同 ATR 桶的期望命中，看選中者贏多少
    d = pd.DataFrame({"y": y, "date": dates, "atr": atr})
    exp = d.groupby(["date", "atr"], observed=True)["y"].transform("mean").to_numpy()
    ctrl = float((y[top] - exp[top]).mean()) * 100
    return {"name": name, "auc": round(auc, 4), "base": round(float(y.mean()) * 100, 2),
            "top_hit": round(hit_top, 2), "top_ctrl": round(ctrl, 2), "n_top": k}


def main() -> None:
    from sklearn.ensemble import HistGradientBoostingClassifier

    t0 = time.time()
    df = pd.read_pickle(_CACHE)
    df = df[df["hit10"].notna()].reset_index(drop=True)
    for c in ("att_notice5", "att_punish10"):
        if c in df.columns:
            df[c] = df[c].astype(float)

    d = df["date"].astype(str)
    tr_m = ((d >= "2021-01-01") & (d <= "2024-12-31")).to_numpy()
    ho_m = (d >= "2025-01-01").to_numpy()
    _log(f"train {tr_m.sum():,} / holdout {ho_m.sum():,}"
         f"（hit10 基率 {df.loc[tr_m,'hit10'].mean()*100:.1f}%"
         f" / {df.loc[ho_m,'hit10'].mean()*100:.1f}%）")

    sets = {"legacy": _legacy_feats(),
            "neutral": list(ml_synth.FEATS_WITH_FLIP),
            "no_flip": list(ml_synth.FEATS)}

    y = df["hit10"].to_numpy(np.int32)
    dates = df["date"].to_numpy()
    atr = df["atr_bucket"].to_numpy()
    rows = []
    for name, feats in sets.items():
        feats = [f for f in feats if f in df.columns and df[f].notna().mean() >= 0.05]
        X = df[feats].to_numpy(np.float64)
        m = HistGradientBoostingClassifier(
            max_iter=600, learning_rate=0.05, max_leaf_nodes=63,
            min_samples_leaf=200, l2_regularization=5.0,
            early_stopping=True, validation_fraction=0.1, n_iter_no_change=50,
            random_state=42, scoring="roc_auc")
        m.fit(X[tr_m], y[tr_m])
        r = _evaluate(m, X[ho_m], y[ho_m], dates[ho_m], atr[ho_m], name)
        r["n_feats"] = len(feats)
        r["trees"] = int(m.n_iter_)
        rows.append(r)
        _log(f"  {name:<9} 特徵 {len(feats)} 棵 {m.n_iter_}"
             f" → AUC {r['auc']} / 前{_TOP_PCT*100:.0f}%命中 {r['top_hit']}%"
             f" / 控波動 {r['top_ctrl']}pp")

    print(f"\n=== holdout 2025+ 對照（基率 {rows[0]['base']}%）===")
    print(f"{'特徵集':<10}{'特徵數':>7}{'AUC':>9}{'前2%命中':>10}{'控波動增量':>12}")
    for r in rows:
        print(f"{r['name']:<10}{r['n_feats']:>7}{r['auc']:>9.4f}"
              f"{r['top_hit']:>9.2f}%{r['top_ctrl']:>10.2f}pp")
    best = max(rows, key=lambda r: r["top_ctrl"])
    print(f"\n最佳：{best['name']}（依前 {_TOP_PCT*100:.0f}% 控波動增量）")

    with open(_OUT, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M"),
                   "top_pct": _TOP_PCT, "results": rows, "best": best["name"]},
                  fh, ensure_ascii=False, indent=1)
    _log(f"完成（{time.time()-t0:.0f}s）→ {_OUT}")


if __name__ == "__main__":
    main()
