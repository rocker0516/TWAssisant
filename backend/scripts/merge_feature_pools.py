"""三特徵池合流 → data/merged_features.pkl（§0：特徵有沒有被活用）。

背景（2026-08-25 討論）
----------------------
專案裡有三個互不相通的特徵池，各由一條研究軌自建、隨軌封存：

  #1 mega_mine_features.pkl   2,474,777 × 82   價量/法人/類股   ← ML **唯一**吃的池
  #2 mega_mine2_features.pkl  2,474,777 × 79   ＋ETF事件族＋基本面族
  #3 ctx_features.pkl         2,543,238 × 60   籌碼行為族（情境矩陣軌）

`ml_synth.FEATS` 只有 47 個特徵，全部來自 #1。去掉識別欄與標籤後三池聯集 113 個，
**ML 沒見過 67 個**，其中 50 個已通過各自的污染稽核。三池主鍵 (stock_id, date) 一致
（#1/#2 同列序、#3 對 #1 覆蓋 100%），從來沒被 join 過——連稽核腳本都是逐池跑的，
所以連稽核本身都沒發現這件事。

合流規範
--------
1. 以 #1 為基準列（2,474,777 有標籤列），#2 同列序直接接欄，#3 依鍵 reindex。
2. **同名欄一律保留 #1/#2 版**（既有結論建立在那個定義上），只取各池獨有欄。
   另手動擋掉別名 `bias20`（＝#1 的 `bias_20`），避免近重複欄。
3. 排除：標籤、識別欄、原始價量水平（沿用 ml_synth「只用衍生 PIT 特徵」的紀律）、
   切分中繼欄（is_holdout/fold）、系統自身輸出（score_chg20——它是規則引擎的產物，
   放進來會讓問題從「原始訊號夠不夠」變成「元模型能不能疊在規則上」，混淆 §0 的判準）。
4. **train 窗覆蓋率**（非全樣本）≥ `_MIN_COVER`。這條是本腳本相對既有協定的修正：
   short_lending / day_trading 兩張表只有 2026-05 之後的資料，**整段落在 holdout 內**，
   train 全 NaN、holdout 才有值 —— 模型學不到它的分裂規則卻要用它推論，等於在
   holdout 引入未受訓的隨機路徑。全樣本覆蓋率擋不住這種時間錯位，train 窗覆蓋率可以。
5. bool → float；category（regime/resonance）→ 序數編碼，並記錄對照表。

輸出 data/merged_features.pkl 附 `.attrs["groups"]`：特徵分群（base/fund/etf/chip/regime），
供 pool_merge_ab.py 做增量歸因——回答「若有增益，是哪一族帶來的」。

用法：PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/merge_feature_pools.py
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

_P1 = _BASE + "/data/mega_mine_features.pkl"
_P2 = _BASE + "/data/mega_mine2_features.pkl"
_P3 = _BASE + "/data/ctx_features.pkl"
_OUT = _BASE + "/data/merged_features.pkl"

_TRAIN_END = "2024-12-31"      # 與 ml_feature_ab 同切分
_MIN_COVER = 0.05              # train 窗覆蓋率下限

# 標籤與識別欄（永不當特徵）
_LABELS = {"mfe30", "mae30", "ret30", "mfe10", "mae10", "hit", "hit10", "exc_mfe20"}
_IDENT = {"stock_id", "date", "sector_id", "node_id", "atr_bucket", "is_holdout", "fold"}
# 原始價量水平（ml_synth 紀律：只用衍生 PIT 特徵）
_RAW = {"open", "high", "low", "close", "volume",
        "ma5", "ma10", "ma20", "ma60", "ma120", "ma240", "vol_ma5", "vol_ma20",
        "macd", "macd_signal", "atr14", "kd_d"}
# 手動擋：別名／系統自身輸出
_ALIAS = {"bias20"}            # ＝ #1 的 bias_20
_SELF_OUTPUT = {"score_chg20"}  # 規則引擎自己的分數變化

_ORDINAL = {
    "regime": {"defense": 0, "hold": 1},
    "resonance": {"weak": 0, "neutral": 1, "strong": 2},
}


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _norm_keys(df: pd.DataFrame) -> pd.DataFrame:
    """stock_id→str、date→'YYYY-MM-DD' 字串，讓三池主鍵可比。"""
    out = df.copy()
    out["stock_id"] = out["stock_id"].astype(str)
    if np.issubdtype(np.dtype(out["date"].dtype), np.datetime64):
        out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    else:
        out["date"] = out["date"].astype(str)
    return out


def _to_numeric(s: pd.Series, name: str) -> pd.Series:
    """bool→float、category(regime/resonance)→序數；其餘保持數值。"""
    if name in _ORDINAL:
        return s.astype(object).map(_ORDINAL[name]).astype("float32")
    if s.dtype == bool or str(s.dtype) == "boolean":
        return s.astype("float32")
    if str(s.dtype) == "category":
        return pd.to_numeric(s.astype(object), errors="coerce").astype("float32")
    return pd.to_numeric(s, errors="coerce").astype("float32")


def main() -> None:
    t0 = time.time()

    _log("載入 #1 mega_mine_features …")
    p1 = pd.read_pickle(_P1)
    p1 = p1[p1["hit10"].notna()].reset_index(drop=True)
    _log(f"  基準列 {len(p1):,}（hit10 基率 {p1['hit10'].mean()*100:.1f}%）")

    _log("載入 #2 mega_mine2_features …")
    p2 = pd.read_pickle(_P2)
    same_order = p1["stock_id"].equals(p2["stock_id"]) and p1["date"].equals(p2["date"])
    if not same_order:                      # 保險：列序不同就走 reindex
        _log("  ! #2 列序與 #1 不同 → 改走鍵 reindex")
        p2 = _norm_keys(p2).set_index(["stock_id", "date"])
        p2 = p2.reindex(pd.MultiIndex.from_arrays(
            [p1["stock_id"].astype(str), p1["date"].astype(str)])).reset_index(drop=True)
    else:
        p2 = p2.reset_index(drop=True)

    _log("載入 #3 ctx_features（依鍵 reindex）…")
    p3 = _norm_keys(pd.read_pickle(_P3)).set_index(["stock_id", "date"])
    p3 = p3[~p3.index.duplicated(keep="first")]
    keys = pd.MultiIndex.from_arrays([p1["stock_id"].astype(str), p1["date"].astype(str)])
    hit_rate = p3.index.isin(keys).sum()
    p3 = p3.reindex(keys).reset_index(drop=True)
    _log(f"  #3 命中基準鍵 {hit_rate:,}／基準 {len(p1):,}")

    # ── 選欄：#1 全欄為底，#2/#3 只取獨有欄 ────────────────────────────────
    drop = _LABELS | _IDENT | _RAW | _ALIAS | _SELF_OUTPUT
    base_feats = [f for f in ml_synth.FEATS if f in p1.columns]
    c1 = set(p1.columns)
    add2 = [c for c in p2.columns if c not in c1 and c not in drop]
    add3 = [c for c in p3.columns if c not in c1 and c not in set(add2) and c not in drop]

    out = p1[["stock_id", "date", "atr_bucket", "hit10", "mae10", "mfe10", "mae30"]].copy()
    for c in base_feats:
        out[c] = _to_numeric(p1[c], c)
    for c in add2:
        out[c] = _to_numeric(p2[c], c)
    for c in add3:
        out[c] = _to_numeric(p3[c], c)
    del p1, p2, p3

    # ── train 窗覆蓋率過濾（本腳本相對既有協定的修正）────────────────────
    tr = (out["date"] <= _TRAIN_END).to_numpy()
    cand = base_feats + add2 + add3
    cover_tr = {c: float(out.loc[tr, c].notna().mean()) for c in cand}
    cover_all = {c: float(out[c].notna().mean()) for c in cand}
    dropped = {c: (round(cover_tr[c], 4), round(cover_all[c], 4))
               for c in cand if cover_tr[c] < _MIN_COVER}
    kept = [c for c in cand if cover_tr[c] >= _MIN_COVER]

    if dropped:
        _log(f"train 窗覆蓋 <{_MIN_COVER:.0%} 剔除 {len(dropped)} 欄"
             f"（括號＝train／全樣本覆蓋，落差大者即時間錯位）：")
        for c, (a, b) in sorted(dropped.items(), key=lambda kv: kv[1][1] - kv[1][0], reverse=True):
            flag = "  ← 時間錯位" if b - a > 0.02 else ""
            print(f"    {c:24s} ({a:.3f} / {b:.3f}){flag}")

    out = out[["stock_id", "date", "atr_bucket", "hit10", "mae10", "mfe10", "mae30"] + kept]

    groups = {
        "base": [c for c in base_feats if c in kept],
        "fund": [c for c in add2 if c in kept and not c.startswith("etf_")],
        "etf": [c for c in add2 if c in kept and c.startswith("etf_")],
        "chip": [c for c in add3 if c in kept and c not in _ORDINAL],
        "regime": [c for c in add3 if c in kept and c in _ORDINAL],
    }
    out.attrs["groups"] = groups
    out.attrs["ordinal_maps"] = _ORDINAL
    out.attrs["train_end"] = _TRAIN_END
    out.attrs["min_cover"] = _MIN_COVER
    out.attrs["dropped"] = dropped

    out.to_pickle(_OUT)

    print("\n=== 合流結果 ===")
    for g, fs in groups.items():
        print(f"  {g:8s} {len(fs):>3} 欄  {', '.join(fs[:8])}{' …' if len(fs) > 8 else ''}")
    total = sum(len(v) for v in groups.values())
    print(f"  {'合計':8s} {total:>3} 欄（ML 現行 {len(groups['base'])} → 新增 "
          f"{total - len(groups['base'])}）")
    print(f"  列數 {len(out):,}；train {int(tr.sum()):,} / holdout {int((~tr).sum()):,}")
    _log(f"完成（{time.time()-t0:.0f}s）→ {_OUT}")

    with open(_BASE + "/data/merged_features_manifest.json", "w", encoding="utf-8") as fh:
        json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M"),
                   "groups": groups, "dropped": dropped,
                   "n_rows": len(out), "train_end": _TRAIN_END,
                   "min_cover_train": _MIN_COVER}, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
