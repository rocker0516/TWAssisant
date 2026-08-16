"""乾淨股（非注意非處置）條件探索：補 mega_mine 缺口。

核心問題：**自身未被列管**的股票，能不能靠「鄰居的事件」（網絡傳染）或
變化值特徵取得前瞻優勢？這才是真正可提前卡位的超前訊號——
mega_mine 的純網絡條件混入了自身列管股，此處全部加上乾淨閘門重驗。

條件集：
  乾淨（單獨基準）
  乾淨 & 每個謂詞（~45）
  乾淨 & 謂詞兩兩交叉（全對）
護欄與 mega_mine 相同（t≥4、3fold 全正、holdout 一次）。

用法：PYTHONIOENCODING=utf-8 python scripts/clean_mine.py
輸出：data/clean_mine_results.json + stdout 摘要
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

import mega_mine as mm  # noqa: E402

_OUT = _BASE + "/data/clean_mine_results.json"


def _clean_mask(df: pd.DataFrame) -> np.ndarray:
    return ~(df["att_notice5"].to_numpy(bool) | df["att_punish10"].to_numpy(bool))


def gen(mine: pd.DataFrame, target: pd.DataFrame) -> list[tuple[str, np.ndarray]]:
    clean = _clean_mask(target)
    preds = mm.build_predicates(mine, target)
    # 乾淨閘門下，事件謂詞（注意/處置）恆假 → 排除
    preds = [(n, m) for n, m in preds if n not in ("注意5日", "處置10日", "注意次數≥2")]
    conds: list[tuple[str, np.ndarray]] = [("乾淨(基準)", clean)]
    for n, m in preds:
        conds.append((f"乾淨 & {n}", clean & m))
    for i in range(len(preds)):
        for j in range(i + 1, len(preds)):
            conds.append((f"乾淨 & {preds[i][0]} & {preds[j][0]}",
                          clean & preds[i][1] & preds[j][1]))
    return conds


def main() -> None:
    t0 = time.time()
    df = pd.read_pickle(_BASE + "/data/mega_mine_features.pkl")
    mine = df[(df["date"].astype(str) >= mm._MINE_LO) & (df["date"].astype(str) <= mm._MINE_HI)].reset_index(drop=True)
    hold = df[df["date"].astype(str) >= mm._HOLD_LO].reset_index(drop=True)
    print(f"挖掘窗 {len(mine):,} / holdout {len(hold):,}；"
          f"乾淨占比 {float(_clean_mask(mine).mean())*100:.1f}%（基率 {mine['hit'].mean()*100:.1f}%）", flush=True)

    conds = gen(mine, mine)
    print(f"總條件數 {len(conds)}", flush=True)

    ev_all = mm.Evaluator(mine)
    dts = np.sort(mine["date"].unique())
    fold_evs = []
    for fi in range(3):
        fd = set(dts[fi * len(dts) // 3:(fi + 1) * len(dts) // 3])
        sub = mine[mine["date"].isin(fd)]
        fold_evs.append((mm.Evaluator(sub), mine["date"].isin(fd).to_numpy()))

    results = []
    for k, (name, mask) in enumerate(conds):
        if k % 200 == 0:
            print(f"  評估 {k}/{len(conds)}…", flush=True)
        mask = np.asarray(mask, dtype=bool)
        r = ev_all.run(mask)
        if r is None or r["t_ctrl"] < mm._T_GATE or r["ctrl"] <= 0:
            continue
        folds = []
        ok = True
        for fev, fmask in fold_evs:
            fr = fev.run(mask[fmask])
            if fr is None or fr["ctrl"] <= 0:
                ok = False
                break
            folds.append(fr["ctrl"])
        if not ok:
            continue
        results.append({"cond": name, **r, "fold_ctrl": folds})
    print(f"挖掘窗晉級 {len(results)} 條", flush=True)

    hold_conds = dict(gen(mine, hold))
    ev_h = mm.Evaluator(hold)
    out = []
    for r in results:
        hm = hold_conds.get(r["cond"])
        hr = ev_h.run(np.asarray(hm, dtype=bool)) if hm is not None else None
        out.append({**r, "holdout": hr})
    out.sort(key=lambda x: -(x["holdout"]["ctrl"] if x["holdout"] else -99))

    with open(_OUT, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M"),
                   "n_conditions": len(conds), "n_survivors": len(results),
                   "results": out}, fh, ensure_ascii=False, indent=1)
    print(f"完成（{time.time()-t0:.0f}s）→ {_OUT}\n", flush=True)

    print(f"=== 乾淨股 Top 30（依 holdout 控波動增量）；共 {len(out)} 條雙段存活 ===")
    print(f"{'條件':<64}{'挖掘ctrl':>9}{'t':>6}{'hold ctrl':>10}{'hold命中':>9}{'MAE代價':>8}")
    for r in out[:30]:
        h = r["holdout"] or {}
        print(f"{r['cond']:<64}{r['ctrl']:>8}pp{r['t_ctrl']:>6}"
              f"{(str(h.get('ctrl'))+'pp') if h else '—':>10}{h.get('hit','—'):>8}%"
              f"{h.get('mae_cost','—'):>8}")


if __name__ == "__main__":
    main()
