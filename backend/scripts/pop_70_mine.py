"""衝 70%：不靠注意/處置，找 10 日碰到 +10% 達 70% 以上的角落。

為什麼要另開一支（而非調 pop_vol_corners.py 的 --floor 70）
------------------------------------------------------------
1. 深度不夠：原挖掘器只到「錨 + 2 原子」，70% 這個高度需要更窄的角落。本支到 4 原子。
2. 匯出沒把 holdout 當關卡：原本只把分年數字寫進 JSON、不 gate，C01 就是這樣帶著
   2025 年的 60.1% 被留下來的（挖掘窗地板 72.2%）。本支把 2025~26 列為硬關卡。
3. **少了同日對照**——最要命的一項。錨池本身基率就很高（atr>8=48.7%、atr>10=54.1%，
   全市場才 18.6%），而且角落清一色是 crash/dip 家族。若一個角落只在崩盤日亮燈，
   它的 70% 可能全部來自「那幾天大家都會噴」，不是它選中了對的股票。故對每個角落
   都算「同一批日子、同一個錨池」的命中率當對照，只看**增量**。

四道關卡（全過才輸出）
  ①  挖掘窗 2021-24 分年地板 ≥ 70%（各年 n≥15、至少 3 個年份有樣本）
  ②  holdout 2025-26 合併命中 ≥ 70%，且 n ≥ 30
  ③  同日同錨對照的增量 ≥ +10pp（挖掘窗與 holdout 都要）
  ④  亮燈日數 ≥ 20（同日個股高度相關，日數才是有效樣本數）

原子池 = app/engines/corner_defs.ATOM_SPECS，本來就不含注意/處置，
無須另外排除；全為價量／籌碼／估值／市場情境特徵。

用法：
  .venv/Scripts/python scripts/pop_70_mine.py                 # 掃描並列印
  .venv/Scripts/python scripts/pop_70_mine.py --floor 65      # 放寬地板
  .venv/Scripts/python scripts/pop_70_mine.py --depth 3       # 只到 3 原子（快）
  .venv/Scripts/python scripts/pop_70_mine.py --export        # 另寫 data/corners70.json
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, __file__.replace("\\", "/").rsplit("/scripts/", 1)[0])
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from pop_condition_judge import _CACHE  # noqa: E402
from app.engines.corner_defs import ATOM_SPECS, eval_atom  # noqa: E402

TARGET = "hit10"
MINE_YEARS = (0, 1, 2, 3)      # 2021~2024
HOLD_YEARS = (4, 5)            # 2025~2026
FLOOR_MIN = 70.0               # 關卡①
HOLD_MIN = 70.0                # 關卡②
HOLD_N_MIN = 30
EDGE_MIN = 10.0                # 關卡③：同日同錨對照的增量 pp
DAYS_MIN = 20                  # 關卡④
MINE_N_MIN = 200
YEAR_N_MIN = 15
MINE_YEARS_MIN = 3
JACCARD_MAX = 0.5
KEEP = 40
ANCHORS = ("atr>6", "atr>8", "atr>10")  # ATOM_SPECS 只定義到 10；且 atr>12 池基率反而更低
_OUT = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0] + "/data/corners70.json"


def _arg(flag: str, cast, default):
    return cast(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else default


class Pool:
    """單一錨池：把後續統計要用的向量全部預先切好，組合迴圈裡只做布林 AND。"""

    def __init__(self, name: str, mask: np.ndarray, hit, ycode, dates):
        self.name = name
        idx = np.flatnonzero(mask)
        self.idx = idx
        self.hit = hit[idx]
        self.ycode = ycode[idx]
        self.dcode, self.dvals = pd.factorize(dates[idx])
        self.nd = len(self.dvals)
        # 同日同錨對照：整個錨池在每一天的命中率（角落的對照組）
        pool_h = np.bincount(self.dcode, weights=self.hit, minlength=self.nd)
        pool_n = np.bincount(self.dcode, minlength=self.nd)
        self.pool_rate = np.divide(pool_h, pool_n, out=np.zeros(self.nd), where=pool_n > 0)
        self.pool_year = self.ycode[np.unique(self.dcode, return_index=True)[1]]

    def stats(self, mask: np.ndarray) -> dict | None:
        n = int(mask.sum())
        if n < MINE_N_MIN:
            return None
        y, h = self.ycode[mask], self.hit[mask]
        n_y = np.bincount(y, minlength=6)
        h_y = np.bincount(y, weights=h, minlength=6)
        yrs = [(h_y[k] / n_y[k] * 100) if n_y[k] >= YEAR_N_MIN else None for k in range(6)]
        ok = [yrs[k] for k in MINE_YEARS if yrs[k] is not None]
        if len(ok) < MINE_YEARS_MIN:
            return None
        mine_n = int(sum(n_y[k] for k in MINE_YEARS))
        hold_n = int(sum(n_y[k] for k in HOLD_YEARS))
        hold_hit = (sum(h_y[k] for k in HOLD_YEARS) / hold_n * 100) if hold_n else None
        # 同日同錨對照，權重＝角落在該日的檔數（讓對照組的日子分布與角落一致）
        d = self.dcode[mask]
        cnt = np.bincount(d, minlength=self.nd)
        fired = cnt > 0
        edge_all = h.mean() * 100 - float(np.average(self.pool_rate[fired],
                                                     weights=cnt[fired])) * 100
        edge_mine = self._edge(mask, MINE_YEARS)
        edge_hold = self._edge(mask, HOLD_YEARS)
        return {"n": n, "days": int(fired.sum()), "floor": min(ok), "per_year": yrs,
                "n_year": n_y, "mine_n": mine_n, "hold_n": hold_n, "hold_hit": hold_hit,
                "edge": edge_all, "edge_mine": edge_mine, "edge_hold": edge_hold}

    def _edge(self, mask: np.ndarray, years) -> float | None:
        sel = mask & np.isin(self.ycode, years)
        if sel.sum() < 20:
            return None
        d = self.dcode[sel]
        cnt = np.bincount(d, minlength=self.nd)
        fired = cnt > 0
        if not fired.any():
            return None
        ctrl = float(np.average(self.pool_rate[fired], weights=cnt[fired])) * 100
        return self.hit[sel].mean() * 100 - ctrl


def main() -> None:
    t0 = time.time()
    floor_min = _arg("--floor", float, FLOOR_MIN)
    hold_min = _arg("--hold", float, HOLD_MIN)
    depth = _arg("--depth", int, 4)

    m = pd.read_pickle(_CACHE)
    m = m[m["date"] >= "2021-01-01"].reset_index(drop=True)
    m["date"] = m["date"].astype(str)
    ycode = (m["date"].str[:4].astype(int) - 2021).to_numpy()
    hit = m[TARGET].to_numpy(dtype=float)
    dates = m["date"].to_numpy()
    liq = (m["vol_ma20"].fillna(0) >= 500 * 1000).to_numpy()

    rank_feats = {s["f"] for s in ATOM_SPECS.values() if s["op"].startswith("rank")}
    ranks = {f: m.groupby("date")[f].rank(pct=True) for f in rank_feats}
    P = {name: eval_atom(m, name, ranks) & liq for name in ATOM_SPECS}
    names = [n for n in ATOM_SPECS if n not in ANCHORS]

    # --clean：只留特徵污染稽核判為「可用」的原子。稽核已把 dist_60d_high 標為
    # 必修/翻號（挖掘 +2.09 → holdout −0.14）、inst_streak 標為待查/無法評估，
    # 而這兩者正好長在原始榜單最亮眼的幾個組合裡 —— 不濾掉等於拿已知會翻號的特徵去挖。
    if "--clean" in sys.argv:
        import os as _os
        fp = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0] + "/data/feature_contamination.json"
        if not _os.path.exists(fp):
            print("找不到 feature_contamination.json，--clean 無效")
        else:
            with open(fp, encoding="utf-8") as fh:
                aud = json.load(fh)["audit"]
            verdict = {a["feat"]: a["verdict"] for a in aud}
            dropped = {}
            keep = []
            for n in names:
                f = ATOM_SPECS[n]["f"]
                v = verdict.get(f, "未稽核")
                if v == "可用":
                    keep.append(n)
                else:
                    dropped.setdefault(v, []).append(f"{n}({f})")
            print(f"--clean：原子 {len(names)} → {len(keep)}")
            for v, lst in sorted(dropped.items()):
                print(f"    剔除[{v}] {', '.join(sorted(set(lst)))}")
            names = keep

    print(f"目標 {TARGET}；全市場基率 {hit[liq].mean()*100:.1f}%；"
          f"關卡：地板≥{floor_min:.0f}% / holdout≥{hold_min:.0f}%(n≥{HOLD_N_MIN}) / "
          f"同日增量≥+{EDGE_MIN:.0f}pp / 日數≥{DAYS_MIN}；深度 錨+{depth-1}")

    cands, seen = [], set()
    survey: list[dict] = []   # 所有通過基本規模門檻的組合，供漏斗與頂尖名單
    funnel: dict[str, int] = {}
    for an in ANCHORS:
        pool = Pool(an, P[an], hit, ycode, dates)
        pm = np.isin(pool.ycode, MINE_YEARS)
        hm = np.isin(pool.ycode, HOLD_YEARS)
        print(f"\n[{an}] 池 n={len(pool.idx)}  挖掘窗基率 "
              f"{pool.hit[pm].mean()*100:.1f}%  holdout 基率 "
              f"{(pool.hit[hm].mean()*100 if hm.any() else float('nan')):.1f}%  "
              f"日數={pool.nd}")
        sub = {x: P[x][pool.idx] for x in names}
        # 逐層擴展：加原子只會讓 n 變小，故 n<MINE_N_MIN 的分支可安全剪掉
        frontier = [((), np.ones(len(pool.idx), dtype=bool))]
        found_here = 0
        for d_ in range(1, depth):
            nxt = []
            for key, mask in frontier:
                start = names.index(key[-1]) + 1 if key else 0
                for x in names[start:]:
                    mk = mask & sub[x]
                    if mk.sum() < MINE_N_MIN:
                        continue
                    k2 = key + (x,)
                    nxt.append((k2, mk))
                    skey = (an,) + tuple(sorted(k2))
                    if skey in seen:
                        continue
                    seen.add(skey)
                    st = pool.stats(mk)
                    if st is None:
                        continue
                    st["key"] = (an,) + k2
                    survey.append(st)
                    gates = {
                        "地板≥%.0f" % floor_min: st["floor"] >= floor_min,
                        "日數≥%d" % DAYS_MIN: st["days"] >= DAYS_MIN,
                        "holdout n≥%d" % HOLD_N_MIN: st["hold_n"] >= HOLD_N_MIN,
                        "holdout≥%.0f" % hold_min: (st["hold_hit"] or -99) >= hold_min,
                        "同日增量≥+%.0f" % EDGE_MIN: ((st["edge_mine"] or -99) >= EDGE_MIN
                                                    and (st["edge_hold"] or -99) >= EDGE_MIN),
                    }
                    for g, ok_ in gates.items():
                        funnel[g] = funnel.get(g, 0) + int(ok_)
                    funnel["__total__"] = funnel.get("__total__", 0) + 1
                    if all(gates.values()):
                        full = np.zeros(len(hit), dtype=bool)
                        full[pool.idx[mk]] = True
                        cands.append((st, (an,) + k2, full))
                        found_here += 1
            frontier = nxt
            print(f"   深度{d_+1}: 候選 {len(nxt)} 個分支存活")
        print(f"   → 全關卡通過 {found_here} 個")

    print(f"\n掃描完成 {time.time()-t0:.0f}s，四關全過 {len(cands)} 個（去重前）")

    # ── 漏斗：每一關**單獨**通過幾個（不是逐關累積），一眼看出誰是瓶頸 ──
    tot = funnel.pop("__total__", 0)
    print(f"\n=== 各關卡單獨通過率（母體 {tot} 個組合）===")
    for g, c in sorted(funnel.items(), key=lambda x: -x[1]):
        print(f"  {g:<18} {c:>6} ({c/max(1,tot)*100:>5.1f}%)")

    def top(label: str, keyf, fmt, n=8, need=None):
        pool_ = [s for s in survey if need is None or need(s)]
        pool_ = [s for s in pool_ if keyf(s) is not None]
        pool_.sort(key=lambda s: -keyf(s))
        print(f"\n=== {label} ===")
        for s in pool_[:n]:
            print(f"  {fmt(s)}  {' ∧ '.join(s['key'])}")

    row = (lambda s: f"地板{s['floor']:5.1f}% holdout{(s['hold_hit'] or float('nan')):5.1f}%"
                     f"(n={s['hold_n']:>4}) 增量 挖{(s['edge_mine'] or 0):+5.1f}"
                     f"/後{(s['edge_hold'] or 0):+5.1f}pp {s['days']:>3}日")
    top("挖掘窗地板最高", lambda s: s["floor"], row)
    top("holdout 最高（n≥30）", lambda s: s["hold_hit"], row, need=lambda s: s["hold_n"] >= 30)
    top("同日增量最高（holdout，n≥30）", lambda s: s["edge_hold"], row,
        need=lambda s: s["hold_n"] >= 30 and s["days"] >= DAYS_MIN)

    # 最重要的一份：兩個窗**都**有正增量者取較小值排序。絕對命中率隨行情漂移
    # （全市場 10 日基率逐季 9.5%~35.2%），跨 regime 唯一可能穩定的是「同日同錨的超額」。
    def rowy(s):
        ys = " ".join(f"{21+k}:{v:2.0f}" if v is not None else f"{21+k}:--"
                      for k, v in enumerate(s["per_year"]))
        return (f"增量 挖{s['edge_mine']:+5.1f}/後{s['edge_hold']:+5.1f}pp  "
                f"地板{s['floor']:5.1f}% holdout{s['hold_hit']:5.1f}%(n={s['hold_n']:>4}) "
                f"{s['days']:>3}日  [{ys}]")

    def _stable_ok(s):
        return (s["hold_n"] >= 50 and s["days"] >= 30
                and s["edge_mine"] is not None and s["edge_hold"] is not None
                and s["edge_mine"] > 0 and s["edge_hold"] > 0)

    top("★ 兩窗都正的同日增量（取較小值排序；n≥50、日數≥30）",
        lambda s: min(s["edge_mine"], s["edge_hold"]), rowy, n=12, need=_stable_ok)

    # 穩定增量名單去重後另存：絕對 70% 不可得時，這才是真正能用的產物
    stable = sorted((s for s in survey if _stable_ok(s)),
                    key=lambda s: -min(s["edge_mine"], s["edge_hold"]))
    seen_st, stable_kept = set(), []
    for s in stable:
        k = tuple(sorted(s["key"]))
        if k in seen_st:
            continue
        seen_st.add(k)
        stable_kept.append(s)
        if len(stable_kept) >= KEEP:
            break

    def _dump(rows: list[dict], key: str, extra: dict) -> None:
        if "--export" not in sys.argv:
            return
        payload = {"generated_from": _CACHE.rsplit("/", 1)[-1], "target": TARGET,
                   "clean_pool": "--clean" in sys.argv,
                   "note": "不含注意/處置特徵；同日同錨對照用以剔除純行情產物。", **extra,
                   key: rows}
        with open(_OUT, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        print(f"已匯出 {len(rows)} 筆（{key}）→ {_OUT}")

    def _row(s: dict, i: int) -> dict:
        return {"id": f"S{i:02d}", "atoms": list(s["key"]),
                "floor": round(s["floor"], 1),
                "holdout_hit": round(s["hold_hit"], 1) if s["hold_hit"] is not None else None,
                "holdout_n": s["hold_n"], "mine_n": s["mine_n"], "days": s["days"],
                "edge_mine_pp": round(s["edge_mine"], 1) if s["edge_mine"] is not None else None,
                "edge_holdout_pp": round(s["edge_hold"], 1) if s["edge_hold"] is not None else None,
                "per_year": {str(2021 + k): {"hit": round(v, 1) if v is not None else None,
                                             "n": int(s["n_year"][k])}
                             for k, v in enumerate(s["per_year"])}}

    if not cands:
        print("\n沒有任何組合同時通過四道關卡"
              f"（挖掘窗地板≥{floor_min:.0f}% 與 holdout≥{hold_min:.0f}% 是不相交的兩群）。")
        _dump([_row(s, i) for i, s in enumerate(stable_kept, 1)], "stable_edge",
              {"discipline": ("退而求其次的產物：挖掘窗與 holdout **同日同錨增量皆為正**、"
                              f"holdout n≥50、日數≥30，依兩者較小值排序。絕對命中率隨行情"
                              "漂移（全市場 10 日基率逐季 9.5%~35.2%），跨 regime 唯一穩定"
                              "的是超額。"),
               "strict_gate_survivors": 0})
        return

    cands.sort(key=lambda c: (-min(c[0]["floor"], c[0]["hold_hit"]), -c[0]["mine_n"]))
    kept = []
    for c in cands:
        if any((c[2] & k[2]).sum() / max(1, (c[2] | k[2]).sum()) > JACCARD_MAX for k in kept):
            continue
        kept.append(c)
        if len(kept) >= KEEP:
            break

    print(f"Jaccard 去重後 {len(kept)} 個獨立角落：\n")
    export = []
    for i, (st, key, _full) in enumerate(kept, 1):
        ys = " ".join(f"{21+k}:{v:3.0f}%({st['n_year'][k]:4})" if v is not None
                      else f"{21+k}:  --      " for k, v in enumerate(st["per_year"]))
        print(f"C{i:02d} 地板{st['floor']:5.1f}%  holdout {st['hold_hit']:5.1f}%"
              f"(n={st['hold_n']})  同日增量 挖掘{st['edge_mine']:+5.1f}pp"
              f"/holdout{st['edge_hold']:+5.1f}pp  {st['days']}日")
        print(f"    {' ∧ '.join(key)}")
        print(f"    {ys}\n")
        export.append({
            "id": f"C{i:02d}", "atoms": list(key),
            "floor": round(st["floor"], 1),
            "holdout_hit": round(st["hold_hit"], 1), "holdout_n": st["hold_n"],
            "edge_mine_pp": round(st["edge_mine"], 1),
            "edge_holdout_pp": round(st["edge_hold"], 1),
            "mine_n": st["mine_n"], "days": st["days"],
            "per_year": {str(2021 + k): {"hit": round(v, 1) if v is not None else None,
                                         "n": int(st["n_year"][k])}
                         for k, v in enumerate(st["per_year"])},
        })

    if "--export" in sys.argv:
        with open(_OUT, "w", encoding="utf-8") as fh:
            json.dump({"generated_from": _CACHE.rsplit("/", 1)[-1],
                       "target": TARGET,
                       "discipline": (f"挖掘窗2021-24分年地板≥{floor_min:.0f}%、"
                                      f"holdout2025-26≥{hold_min:.0f}%(n≥{HOLD_N_MIN})、"
                                      f"同日同錨增量≥+{EDGE_MIN:.0f}pp、日數≥{DAYS_MIN}、"
                                      f"Jaccard≤{JACCARD_MAX}"),
                       "note": "不含注意/處置特徵；同日對照用以剔除純行情產物。",
                       "corners": export}, fh, ensure_ascii=False, indent=1)
        print(f"已匯出 {len(export)} 個 → {_OUT}")


if __name__ == "__main__":
    main()
