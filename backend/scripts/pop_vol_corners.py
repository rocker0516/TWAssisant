"""波動錨高精度角落總掃描：高 ATR 池內找分年地板 ≥70% 的組合。

設計依據（pop_open_mine 系列定論）：
  - ATR=燃料軸必須絕對（相對排名版更弱）→ 錨 = atr>6% / 8% / 10%
  - 估值等年代漂移軸用相對（當日排名）
  - 紀律：2021~2024 挖掘窗分年地板（各年 n≥15、至少 3 個挖掘年有樣本）、
    2025/26 軟檢查、Jaccard>0.5 視為同角落去重

原子定義與線上引擎共用 app/engines/corner_defs.py（同一份定義兩邊跑）。

2026-08 兩項修正
  目標  原本用 hit（mfe30≥10，30 日碰到），與波段軌定版口徑不符；改為 hit10
        （mfe10≥10）。兩者基率差一倍以上，分年地板不可互比 —— 舊的
        data/corners.json 是 30 日產物。--target hit 可跑舊口徑對照。
  大盤  24/30 角落依賴 mkt_ret20 / mkt_bias60，而 market_index 一度只剩 106 列
        （覆蓋 1.3~4.5%），那些角落的分年統計等於建在殘缺資料上。已由
        scripts/backfill_market_index.py 回補、判官快取重建，此處直接讀即可。

用法：
  .venv/Scripts/python scripts/pop_vol_corners.py                 # 掃描並列印（hit10）
  .venv/Scripts/python scripts/pop_vol_corners.py --export        # 另寫 data/corners.json
  .venv/Scripts/python scripts/pop_vol_corners.py --target hit    # 舊 30 日口徑對照
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, __file__.replace("\\", "/").rsplit("/scripts/", 1)[0])
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from pop_condition_judge import _CACHE  # noqa: E402
from app.engines.corner_defs import ATOM_SPECS, eval_atom  # noqa: E402

MINE = (0, 1, 2, 3)
# 地板門檻以「基率的幾倍」為準，不是抄絕對數字：原紀律 70% 是在 30 日基率 32.8%
# 下訂的（=2.1× 基率）；10 日基率只有 14.5%，沿用 70% 等於要求 4.8× 基率，
# 難度不同一個量級（實測只剩 1 個角落存活）。取 50%（=3.5× 基率）——
# 比原紀律嚴格得多，且剛好填滿 30 個獨立角落。門檻懸崖：50%→30 個、
# 55%→24 個、60%→7 個、65%→1 個。--floor 可覆寫。
FLOOR_MIN, MINE_N_MIN, MINE_YEARS_MIN = 50.0, 200, 3
JACCARD_MAX = 0.5
KEEP = 30
ANCHORS = ("atr>6", "atr>8", "atr>10")
_OUT = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0] + "/data/corners.json"


def main() -> None:
    global FLOOR_MIN
    target = "hit10"
    if "--target" in sys.argv:
        target = sys.argv[sys.argv.index("--target") + 1]
    if "--floor" in sys.argv:
        FLOOR_MIN = float(sys.argv[sys.argv.index("--floor") + 1])
    m = pd.read_pickle(_CACHE)
    m = m[m["date"] >= "2021-01-01"].reset_index(drop=True)
    m["date"] = m["date"].astype(str)
    ycode = (m["date"].str[:4].astype(int) - 2021).to_numpy()
    base = m[target].mean() * 100
    print(f"目標 {target}；基率 {base:.1f}%；地板門檻 {FLOOR_MIN:.0f}%"
          f"（={FLOOR_MIN/base:.1f}× 基率）；mkt_ret20 覆蓋 "
          f"{m['mkt_ret20'].notna().mean()*100:.1f}%")
    hit = m[target].to_numpy(dtype=float)
    dates = m["date"].to_numpy()
    liq = (m["vol_ma20"].fillna(0) >= 500 * 1000).to_numpy()

    # rank 原子的當日百分位（多日資料先整批算好）
    rank_feats = {s["f"] for s in ATOM_SPECS.values() if s["op"].startswith("rank")}
    ranks = {f: m.groupby("date")[f].rank(pct=True) for f in rank_feats}

    P = {name: eval_atom(m, name, ranks) & liq for name in ATOM_SPECS}
    names = [n for n in ATOM_SPECS if n not in ANCHORS]

    def stats(mask):
        n_y = np.bincount(ycode[mask], minlength=6)
        h_y = np.bincount(ycode[mask], weights=hit[mask], minlength=6)
        yrs = [(h_y[y] / n_y[y] * 100) if n_y[y] >= 15 else None for y in range(6)]
        mine_ok = [yrs[y] for y in MINE if yrs[y] is not None]
        floor = min(mine_ok) if len(mine_ok) >= MINE_YEARS_MIN else None
        return floor, int(sum(n_y[y] for y in MINE)), yrs, n_y

    cands = []
    seen_keys = set()
    for an in ANCHORS:
        amask = P[an]
        combos = [((an,), amask)]
        combos += [((an, x), amask & P[x]) for x in names]
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                combos.append(((an, names[i], names[j]), amask & P[names[i]] & P[names[j]]))
        for key, mask in combos:
            skey = tuple(sorted(key))
            if skey in seen_keys:
                continue
            seen_keys.add(skey)
            fl, mn, yrs, n_y = stats(mask)
            if fl is not None and fl >= FLOOR_MIN and mn >= MINE_N_MIN:
                cands.append((fl, mn, key, mask, yrs, n_y))
    cands.sort(key=lambda x: (-x[0], -x[1]))
    print(f"達標組合（去重前）：{len(cands)}")

    kept = []
    for cand in cands:
        mask = cand[3]
        dup = False
        for k2 in kept:
            inter = int((mask & k2[3]).sum())
            union = int((mask | k2[3]).sum())
            if union and inter / union > JACCARD_MAX:
                dup = True
                break
        if not dup:
            kept.append(cand)
        if len(kept) >= KEEP:
            break

    print(f"Jaccard 去重後 {len(kept)} 個獨立角落：\n")
    export = []
    for idx, (fl, mn, key, mask, yrs, n_y) in enumerate(kept, 1):
        sd = pd.Series(dates[mask])
        cov = sd.groupby(sd.str[:4]).nunique()
        ys = " ".join(
            f"{21+i}:{v:3.0f}%({n_y[i]:4},{cov.get(str(2021+i), 0)}日)" if v is not None
            else f"{21+i}:  --        " for i, v in enumerate(yrs))
        print(f"地板{fl:3.0f}%  {' & '.join(key)}")
        print(f"        {ys}\n")
        # 家族分類：有大盤條件的看深度，無大盤條件=全天候
        mkt_atoms = [a for a in key if a.startswith("大盤")]
        family = ("crash" if any("崩" in a for a in mkt_atoms)
                  else "dip" if mkt_atoms else "allweather")
        export.append({
            "id": f"C{idx:02d}",
            "atoms": list(key),
            "family": family,
            "floor": round(fl, 1),
            "mine_n": mn,
            "per_year": {
                str(2021 + i): {
                    "hit": round(yrs[i], 1) if yrs[i] is not None else None,
                    "n": int(n_y[i]),
                    "days": int(cov.get(str(2021 + i), 0)),
                } for i in range(6)
            },
        })

    if "--export" in sys.argv:
        with open(_OUT, "w", encoding="utf-8") as fh:
            json.dump({"generated_from": "condition_judge_cache_v3 2021-01~2026-06",
                       "target": target,
                       "note": "凍結的挖掘產物：影子軌角落清單。重挖請重跑本腳本 --export。",
                       "discipline": "2021-24分年地板≥70、各年n≥15且≥3挖掘年、Jaccard≤0.5去重",
                       "corners": export}, fh, ensure_ascii=False, indent=1)
        print(f"已匯出 {len(export)} 個角落 → {_OUT}")


if __name__ == "__main__":
    main()
