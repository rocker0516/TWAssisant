"""反向挖掘：補 mega_mine 的兩個單向盲點。

A. 避開名單（負向條件）：同一批 1000+ 條件，改保留「10 日碰到率控波動後
   顯著為負」者（t≤-4、3fold 全負）——掛這些條件的股票拖累清單命中，
   可做推薦頁的避開濾網。
B. 下跌目標（做空候選）：標籤換成 down10＝10 日內碰 −10%（mae10≤−10），
   同護欄挖「顯著更會跌」的條件。註：台股做空有制度成本（平盤下限制、
   處置股禁空、強制回補），此清單先當風險警示用。

用法：PYTHONIOENCODING=utf-8 python scripts/reverse_mine.py
輸出：data/reverse_mine_results.json + stdout 摘要
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np
import pandas as pd

_BASE = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0]
sys.path.insert(0, _BASE)
sys.path.insert(0, _BASE + "/scripts")

import pop_condition_judge as judge  # noqa: E402
import mega_mine as mm  # noqa: E402

_OUT = _BASE + "/data/reverse_mine_results.json"


def _log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def run_side(name: str, mine: pd.DataFrame, hold: pd.DataFrame,
             conds_m, conds_h, negative: bool) -> list[dict]:
    """negative=True：保留顯著為負（避開名單）；False：一般正向（下跌目標用）。"""
    ev = mm.Evaluator(mine)
    dts = np.sort(mine["date"].unique())
    fold_evs = []
    for fi in range(3):
        fd = set(dts[fi * len(dts) // 3:(fi + 1) * len(dts) // 3])
        fold_evs.append((mm.Evaluator(mine[mine["date"].isin(fd)]), mine["date"].isin(fd).to_numpy()))

    results = []
    for k, (cname, mask) in enumerate(conds_m):
        if k % 300 == 0:
            _log(f"  [{name}] {k}/{len(conds_m)}")
        mask = np.asarray(mask, dtype=bool)
        r = ev.run(mask)
        if r is None:
            continue
        if negative:
            if r["t_ctrl"] > -mm._T_GATE or r["ctrl"] >= 0:
                continue
        else:
            if r["t_ctrl"] < mm._T_GATE or r["ctrl"] <= 0:
                continue
        ok = True
        for fev, fmask in fold_evs:
            fr = fev.run(mask[fmask])
            good = fr is not None and ((fr["ctrl"] < 0) if negative else (fr["ctrl"] > 0))
            if not good:
                ok = False
                break
        if not ok:
            continue
        results.append({"cond": cname, **r})

    ev_h = mm.Evaluator(hold)
    hmap = dict(conds_h)
    out = []
    for r in results:
        hm = hmap.get(r["cond"])
        hr = ev_h.run(np.asarray(hm, dtype=bool)) if hm is not None else None
        out.append({**r, "holdout": hr})
    out.sort(key=lambda x: (x["holdout"]["ctrl"] if x["holdout"] else 0) * (1 if negative else -1))
    _log(f"[{name}] 晉級 {len(out)} 條")
    return out


def main() -> None:
    feat_cache = _BASE + "/data/mega_mine_features.pkl"
    df = pd.read_pickle(feat_cache)
    if "mae10" not in df.columns:  # 舊快取（v3 判官）→ 從 v4 重建
        _log("特徵快取無 mae10，重建…")
        os.remove(feat_cache)
        df = mm.build_features()
        df.to_pickle(feat_cache)

    mine = df[(df["date"].astype(str) >= mm._MINE_LO) & (df["date"].astype(str) <= mm._MINE_HI)].reset_index(drop=True)
    hold = df[df["date"].astype(str) >= mm._HOLD_LO].reset_index(drop=True)

    # ── A. 避開名單：目標=10日碰+10%（hit10），保留顯著為負 ──
    mine_a = mine.copy(); mine_a["hit"] = mine_a["hit10"]
    hold_a = hold.copy(); hold_a["hit"] = hold_a["hit10"]
    conds_m = mm.gen_conditions_fixed(mine_a, mine_a)
    conds_h = mm.gen_conditions_fixed(mine_a, hold_a)
    _log(f"條件數 {len(conds_m)}；A 基率 {mine_a['hit'].mean()*100:.1f}%")
    avoid = run_side("避開", mine_a, hold_a, conds_m, conds_h, negative=True)

    # ── B. 下跌目標：hit=10日碰−10%（mae10≤−10），保留顯著為正 ──
    mine_b = mine.copy(); mine_b["hit"] = (mine_b["mae10"] <= -10).astype(float).where(mine_b["mae10"].notna())
    hold_b = hold.copy(); hold_b["hit"] = (hold_b["mae10"] <= -10).astype(float).where(hold_b["mae10"].notna())
    _log(f"B 基率（10日碰-10%）{mine_b['hit'].mean()*100:.1f}%")
    conds_mb = mm.gen_conditions_fixed(mine_b, mine_b)
    conds_hb = mm.gen_conditions_fixed(mine_b, hold_b)
    down = run_side("下跌", mine_b, hold_b, conds_mb, conds_hb, negative=False)

    with open(_OUT, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M"),
                   "avoid": avoid, "down": down}, fh, ensure_ascii=False, indent=1)

    print(f"\n=== A. 避開名單 Top 15（10日碰+10% 顯著拖後腿；依 holdout ctrl 升冪）===")
    print(f"{'條件':<58}{'挖掘ctrl':>9}{'t':>7}{'hold ctrl':>10}{'hold命中':>9}")
    for r in avoid[:15]:
        h = r["holdout"] or {}
        print(f"{r['cond']:<58}{r['ctrl']:>8}pp{r['t_ctrl']:>7}{(str(h.get('ctrl'))+'pp') if h else '—':>10}{h.get('hit','—'):>8}%")

    print(f"\n=== B. 下跌目標 Top 15（10日碰−10% 顯著更高；依 holdout ctrl 降冪）===")
    print(f"{'條件':<58}{'挖掘ctrl':>9}{'t':>7}{'hold ctrl':>10}{'hold跌中':>9}")
    for r in down[:15]:
        h = r["holdout"] or {}
        print(f"{r['cond']:<58}{r['ctrl']:>8}pp{r['t_ctrl']:>7}{(str(h.get('ctrl'))+'pp') if h else '—':>10}{h.get('hit','—'):>8}%")


if __name__ == "__main__":
    main()
