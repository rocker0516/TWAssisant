"""會噴四因子權重重驗（目標函數換 10 日碰到率後）。

線上式：comp = (2*rank(atr_pct) + rank(ma_align) + rank(pos_52w) + rank(pb)) / 5
       pop = 硬篩(站上月線且月線上揚、|60日乖離|<15) & 流動性 & 當日排名前20%
本腳本：
  1. 各因子 rank 對 hit10 的日層級 IC（單獨資訊量）
  2. 權重網格 {0,1,2,3}^4（256 組）全搜：同硬篩同前20%口徑，
     日層級 hit10 命中/lift/ATR桶控波動；3 fold 全正才計
  3. 依挖掘窗控波動 t 排名；前 5 名＋現行權重(2,1,1,1) 看 holdout 一次

用法：PYTHONIOENCODING=utf-8 python scripts/wave_weight_revalidate.py
"""
from __future__ import annotations

import sys
import time

import numpy as np
import pandas as pd

_BASE = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0]
sys.path.insert(0, _BASE)
sys.path.insert(0, _BASE + "/scripts")

import pop_condition_judge as judge  # noqa: E402
import mega_mine as mm  # noqa: E402

_MIN_VOL = 500 * 1000
_TOP_PCT = 0.80  # 前 20%


def _log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    liq = df["vol_ma20"].notna() & (df["vol_ma20"] >= _MIN_VOL)
    above_rising = (df["c_over_ma20"] > 0) & df["ma20_up5"].fillna(False)
    df["_hard"] = (above_rising & (df["bias_60"].abs() < 15) & liq).fillna(False)
    g = df.groupby("date")
    df["_ra"] = g["atr_pct"].rank(pct=True)
    df["_rl"] = g["ma_align"].rank(pct=True)
    df["_rp"] = g["pos_52w"].rank(pct=True).fillna(0.5)
    df["_rb"] = g["pb"].rank(pct=True).fillna(0.5)
    # 評估用 hit 換成 hit10（Evaluator 讀 df["hit"]）
    df["hit30_bak"] = df["hit"]
    df["hit"] = df["hit10"]
    return df[df["hit"].notna()].reset_index(drop=True)


def factor_ic(df: pd.DataFrame) -> None:
    print("\n=== 各因子 rank 對 hit10 的日層級 IC（皮爾森，日均±t）===")
    for name, col in (("波動 ATR", "_ra"), ("均線排列", "_rl"), ("52週位置", "_rp"), ("PB", "_rb")):
        ics = []
        for _, gg in df.groupby("date"):
            if len(gg) < 100:
                continue
            v = gg[[col, "hit"]].dropna()
            if v[col].std() > 0 and v["hit"].std() > 0:
                ics.append(float(np.corrcoef(v[col], v["hit"])[0, 1]))
        a = np.array(ics)
        t = a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))
        print(f"  {name:<8} IC={a.mean():+.4f}  t={t:+.1f}  （{len(a)} 日）")


def eval_weights(df: pd.DataFrame, ev: mm.Evaluator, w: tuple) -> np.ndarray:
    comp = (w[0]*df["_ra"] + w[1]*df["_rl"] + w[2]*df["_rp"] + w[3]*df["_rb"]) / max(sum(w), 1e-9)
    score = comp.groupby(df["date"]).rank(pct=True)
    return (df["_hard"] & (score >= _TOP_PCT)).to_numpy()


def main() -> None:
    feat = pd.read_pickle(_BASE + "/data/condition_judge_cache_v4.pkl")
    mine = prepare(feat[(feat["date"] >= judge._MINE_LO) & (feat["date"] <= judge._MINE_HI)])
    hold = prepare(feat[feat["date"] >= judge._HOLD_LO])
    _log(f"挖掘窗 {len(mine):,} 列（hit10 基率 {mine['hit'].mean()*100:.1f}%）/ holdout {len(hold):,}")

    factor_ic(mine)

    ev = mm.Evaluator(mine)
    dts = np.sort(mine["date"].unique())
    fold_evs = []
    for fi in range(3):
        fd = set(dts[fi * len(dts) // 3:(fi + 1) * len(dts) // 3])
        fold_evs.append((mm.Evaluator(mine[mine["date"].isin(fd)]), mine["date"].isin(fd).to_numpy()))

    grid = [(a, b, c, d)
            for a in (0, 1, 2, 3) for b in (0, 1, 2, 3)
            for c in (0, 1, 2, 3) for d in (0, 1, 2, 3)
            if (a, b, c, d) != (0, 0, 0, 0)]
    _log(f"網格 {len(grid)} 組開始…")
    rows = []
    for k, w in enumerate(grid):
        if k % 32 == 0:
            _log(f"  {k}/{len(grid)}")
        mask = eval_weights(mine, ev, w)
        r = ev.run(mask)
        if r is None or r["ctrl"] <= 0:
            continue
        fold_ok = True
        fmin = 99.0
        for fev, fmask in fold_evs:
            fr = fev.run(mask[fmask])
            if fr is None or fr["ctrl"] <= 0:
                fold_ok = False
                break
            fmin = min(fmin, fr["ctrl"])
        if not fold_ok:
            continue
        rows.append({"w": w, **r, "fold_min": fmin})

    rows.sort(key=lambda r: -r["t_ctrl"])
    cur = next((r for r in rows if r["w"] == (2, 1, 1, 1)), None)
    print(f"\n=== 挖掘窗 Top 10（{len(rows)} 組過 3fold）＋現行權重 ===")
    print(f"{'權重(ATR,均線,位置,PB)':<24}{'命中':>7}{'lift':>8}{'ctrl':>8}{'t':>6}{'fold最弱':>9}")
    top = rows[:10]
    if cur and cur not in top:
        top = top + [cur]
    for r in top:
        tag = " ←現行" if r["w"] == (2, 1, 1, 1) else ""
        print(f"{str(r['w']):<24}{r['hit']:>6}%{r['lift']:>7}pp{r['ctrl']:>7}pp{r['t_ctrl']:>6}{r['fold_min']:>8}pp{tag}")

    # holdout：前 5 ＋ 現行
    ev_h = mm.Evaluator(hold)
    finalists = rows[:5] + ([cur] if cur and cur not in rows[:5] else [])
    print("\n=== HOLDOUT 2025+（只此一次）===")
    for r in finalists:
        mask = eval_weights(hold, ev_h, r["w"])
        hr = ev_h.run(mask)
        tag = " ←現行" if r["w"] == (2, 1, 1, 1) else ""
        if hr:
            print(f"{str(r['w']):<24}命中 {hr['hit']}%  lift {hr['lift']}pp  ctrl {hr['ctrl']}pp (t={hr['t_ctrl']}){tag}")
        else:
            print(f"{str(r['w']):<24}樣本不足{tag}")


if __name__ == "__main__":
    main()
