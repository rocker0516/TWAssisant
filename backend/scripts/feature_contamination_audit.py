"""特徵市場污染稽核：把挖掘池裡每個特徵拆成「市場層」與「選股層」，找出壞掉的。

背景
----
leadlag_sector.py 發現 sec_ret20（類股 ret20 中位數）未剔除大盤共同因子，控 ATR 後
holdout 分層價差是 −0.9pp（挖掘窗還有 +2.6pp）—— 挖掘窗有效只是因為那段大盤自身有
動能。sec_breadth、peer_surge5 是同型的「水位型」指標，同樣可疑。但逐個猜不是辦法，
本腳本系統性掃過整個挖掘池。

掃描範圍：特徵表裡**所有**數值欄（扣掉標籤與識別欄），不是手維護的挖掘清單 ——
手維護清單會讓已知有問題的欄位「剛好」不在稽核範圍裡。

三個判準（互相獨立，要分開看）
------------------------------
1) coverage = 非空比例
   低覆蓋 = 特徵形同不存在。mkt_bias60/mkt_ret20 源自 market_index 表，
   而該表只有 2026-03 起 106 列 —— 覆蓋率 1.3%，在挖掘窗裡幾乎全是 NaN。

2) ts_share = Var(每日橫斷面均值) / Var(全體)
   把特徵拆成「當日全市場均值」+「個股對均值的偏離」。均值成分是純市場層資訊，
   偏離成分才是選股資訊。ts_share 高 = 這個特徵主要在講「今天是什麼日子」，
   而不是「這檔股票是誰」。純市場層特徵無法做橫斷面選股，只能當時間濾網。

3) 控 ATR 桶後的五分位命中價差，挖掘窗 vs holdout 是否翻號／衰減
   污染的特徵在挖掘窗常常有效（該段大盤剛好同向），出樣本就崩。

污染與不穩是**兩件事**，不能混為一談：ts_share 低但翻號 = 特徵本身弱，不是污染；
ts_share 高但穩定 = 有市場成分但仍可用。分級同時標記兩個維度。

用法：PYTHONIOENCODING=utf-8 python scripts/feature_contamination_audit.py
輸出：data/feature_contamination.json + stdout
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

import mega_mine2 as mm  # noqa: E402

_OUT = _BASE + "/data/feature_contamination.json"
_N_Q = 5
_MIN_COVER = 0.50        # 覆蓋率下限
_HIGH_TS = 0.25          # 市場成分過重門檻
# 標籤與識別欄，不是特徵
_NOT_FEATURE = {"stock_id", "date", "sector_id", "node_id", "atr_bucket",
                "hit", "hit10", "mfe30", "mfe10", "mae30", "mae10", "ret30"}


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _spread(s: pd.DataFrame, col: str) -> float | None:
    """控 ATR 桶的高低組命中價差（pp）。

    連續特徵用五分位（最強−最弱）；旗標／離散特徵五分位切不出來，改用
    「非零 vs 零」二分 —— 否則整批事件旗標（處置、ETF窗、營收創新高）會全部
    落在「無法評估」，等於三分之一的池子沒被稽核到。
    """
    s = s[[col, "hit10", "atr_bucket"]].dropna()
    if len(s) < 20_000:
        return None
    v = s[col]
    if v.nunique() <= 3:                       # 旗標／低基數
        hi = v > v.min()
        if hi.sum() < 2_000 or (~hi).sum() < 2_000:
            return None
        # 逐 ATR 桶算差再以桶內樣本數加權，避免高波動桶主導
        t = s.assign(hi=hi).groupby(["atr_bucket", "hi"], observed=True)["hit10"].agg(
            ["mean", "size"]).unstack("hi")
        if t.shape[1] < 4:
            return None
        diff = (t["mean"][True] - t["mean"][False]) * 100
        w = t["size"][True]
        return float((diff * w).sum() / w.sum())
    q = s.groupby("atr_bucket", observed=True)[col].transform(
        lambda x: pd.qcut(x, _N_Q, labels=False, duplicates="drop"))
    t = s.assign(q=q).groupby("q", observed=True)["hit10"].mean() * 100
    if len(t) < _N_Q:
        return None
    return float(t.iloc[-1] - t.iloc[0])


def main() -> None:
    t0 = time.time()
    df = pd.read_pickle(mm._V2_CACHE)
    _log(f"載入 {len(df):,} 列 / {df.shape[1]} 欄")

    feats = [c for c in df.columns
             if c not in _NOT_FEATURE and pd.api.types.is_numeric_dtype(df[c])]
    in_pool = set(mm._NUMERIC) | {c for c, _ in mm._PRED_SPEC}
    _log(f"稽核 {len(feats)} 個數值欄（其中 {len(in_pool & set(feats))} 個在挖掘池裡）")

    d = df["date"].astype(str)
    mine = df[(d >= mm._MINE_LO) & (d <= mm._MINE_HI)]
    hold = df[d >= mm._HOLD_LO]
    n = len(df)

    rows = []
    for f in feats:
        v = df[f]
        cover = float(v.notna().mean())
        arr = v.to_numpy(dtype=float)
        var_all = float(np.nanvar(arr))
        if cover < 0.02 or var_all <= 1e-12:
            ts_share = np.nan
        else:
            daily = v.groupby(df["date"]).transform("mean").to_numpy(dtype=float)
            ts_share = float(np.nanvar(daily)) / var_all

        sm = _spread(mine, f) if cover >= 0.02 else None
        sh = _spread(hold, f) if cover >= 0.02 else None

        # 兩個獨立維度
        if cover < _MIN_COVER:
            health = "覆蓋不足"
        elif sm is None or sh is None:
            health = "無法評估"
        elif np.sign(sm) != np.sign(sh):
            health = "翻號"
        elif abs(sh) < abs(sm) * 0.4:
            health = "衰減>60%"
        else:
            health = "穩定"
        market = "市場層" if (not np.isnan(ts_share) and ts_share >= _HIGH_TS) else "選股層"
        verdict = ("必修" if health in ("覆蓋不足", "翻號", "衰減>60%") else
                   "可用" if health == "穩定" else "待查")

        rows.append({"feat": f, "in_pool": f in in_pool,
                     "cover": round(cover, 3),
                     "ts_share": None if np.isnan(ts_share) else round(ts_share, 3),
                     "spread_mine": round(sm, 2) if sm is not None else None,
                     "spread_hold": round(sh, 2) if sh is not None else None,
                     "market": market, "health": health, "verdict": verdict})

    order = {"必修": 0, "待查": 1, "可用": 2}
    rows.sort(key=lambda r: (order[r["verdict"]], r["cover"],
                             -(r["ts_share"] or 0)))

    print(f"\n{'特徵':<20}{'池':>3}{'覆蓋':>7}{'市場成分':>9}{'挖掘':>9}{'holdout':>10}"
          f"  {'體質':<10}判定")
    cur = None
    for r in rows:
        if r["verdict"] != cur:
            cur = r["verdict"]
            print(f"  ══ {cur} " + "═" * 60)
        f2 = lambda x, u="pp": f"{x:+.2f}{u}" if x is not None else "—"
        print(f"{r['feat']:<20}{'✓' if r['in_pool'] else ' ':>3}"
              f"{r['cover']*100:>6.1f}%{(r['ts_share'] if r['ts_share'] is not None else 0):>9.3f}"
              f"{f2(r['spread_mine']):>9}{f2(r['spread_hold']):>10}"
              f"  {r['health']:<10}{r['market']}")

    bad = [r["feat"] for r in rows if r["verdict"] == "必修" and r["in_pool"]]
    print(f"\n挖掘池裡必修的 {len(bad)} 個：{', '.join(bad) if bad else '（無）'}")

    with open(_OUT, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M"),
                   "must_fix_in_pool": bad, "audit": rows}, fh,
                  ensure_ascii=False, indent=1)
    _log(f"完成（{time.time()-t0:.0f}s）→ {_OUT}")


if __name__ == "__main__":
    main()
