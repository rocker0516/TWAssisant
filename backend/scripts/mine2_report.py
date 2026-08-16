"""mega_mine2 結果彙整：依「觸發家族」分組，看 ≥70% 候選是否只集中在單一家族。

家族 = 條件裡出現的離散事件觸發器（處置/注意/ETF/營收/季報）；純技術籌碼條件歸「技術面」。
用法：PYTHONIOENCODING=utf-8 python scripts/mine2_report.py [最低holdout命中，預設70]
"""
from __future__ import annotations

import json
import sys

_BASE = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0]
_IN = _BASE + "/data/mega_mine2_results.json"

_FAMILY = [
    ("處置", ["處置10日"]),
    ("注意", ["注意5日", "注意次數"]),
    ("ETF成分", ["ETF納入", "ETF剔除", "etf_add_cnt20"]),
    ("月營收", ["營收創12月新高", "營收公布", "rev_yoy", "rev_mom"]),
    ("季報", ["季報公布", "eps_", "gm_chg"]),
]


def family(cond: str) -> str:
    hit = [name for name, keys in _FAMILY if any(k in cond for k in keys)]
    return "＋".join(hit) if hit else "技術面"


def main() -> None:
    floor = float(sys.argv[1]) if len(sys.argv) > 1 else 70.0
    d = json.load(open(_IN, encoding="utf-8"))
    rs = [r for r in d["results"] if r.get("holdout")]

    print(f"目標 {d['target']}｜挖掘窗基率 {d['base_mine']}%｜holdout 基率 {d['base_hold']}%")
    print(f"條件 {d['n_conditions']:,} 條｜Bonferroni t 門檻 {d['t_gate']}｜"
          f"pass {d['n_pass']} / watch {d['n_watch']}｜每日最少選 {d['min_picks']} 檔\n")

    hi = [r for r in rs if r["holdout"]["hit"] >= floor]
    print(f"=== holdout 10 日碰到率 ≥{floor:.0f}%：{len(hi)} 條 ===")
    if hi:
        fam: dict[str, int] = {}
        for r in hi:
            fam[family(r["cond"])] = fam.get(family(r["cond"]), 0) + 1
        print("家族分布：" + "、".join(f"{k} {v}條" for k, v in
                                   sorted(fam.items(), key=lambda x: -x[1])) + "\n")
        print(f"{'條件':<64}{'家族':<12}{'層級':>5}{'命中':>7}{'Wilson':>8}"
              f"{'增量':>8}{'日均':>6}{'MAE':>7}")
        for r in sorted(hi, key=lambda x: -x["holdout"]["wilson"]):
            h = r["holdout"]
            print(f"{r['cond']:<64}{family(r['cond']):<12}{r['tier']:>5}"
                  f"{h['hit']:>6}%{h['wilson']:>7}%{h['ctrl']:>7}pp"
                  f"{h['avg_picks']:>6}{h['mae_cost']:>7}")

    print(f"\n=== 各家族 holdout 最佳（不限 {floor:.0f}%）===")
    best: dict[str, dict] = {}
    for r in rs:
        f = family(r["cond"])
        if f not in best or r["holdout"]["hit"] > best[f]["holdout"]["hit"]:
            best[f] = r
    print(f"{'家族':<18}{'最佳條件':<58}{'命中':>7}{'Wilson':>8}{'增量':>8}{'日均':>6}")
    for f, r in sorted(best.items(), key=lambda x: -x[1]["holdout"]["hit"]):
        h = r["holdout"]
        print(f"{f:<18}{r['cond']:<58}{h['hit']:>6}%{h['wilson']:>7}%"
              f"{h['ctrl']:>7}pp{h['avg_picks']:>6}")


if __name__ == "__main__":
    main()
