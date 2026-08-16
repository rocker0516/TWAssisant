"""條件機率查表 → data/prob_table.json（推薦頁每檔顯示「同條件歷史命中率」用）。

格子 = 波動帶(絕對 ATR%) × 會噴分數帶(全市場百分位) × 大盤狀態(乖離季線)。
量尺依挖掘定論：ATR/大盤=絕對（機制軸）、分數本身已是相對排名。
對 forward_labels 全期（2021-01~最新）統計每格「隔日高錨 10 交易日內碰到 +10%」
命中率；serve 時 n<150 的格子逐層回退（去大盤 → 去波動 → 全域）。
描述性統計非新回測；歷史條件機率≠保證。
用法：.venv/bin/python scripts/build_prob_table.py
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, __file__.replace("\\", "/").rsplit("/scripts/", 1)[0])
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from pop_condition_judge import _CACHE, _build_cache  # noqa: E402

_OUT = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0] + "/data/prob_table.json"
_MIN_VOL = 500 * 1000

# 帶界定義（serve 端 routes.py 用同一份，存進 json）
SCORE_BINS = [0, 60, 70, 80, 90, 95, 101]
SCORE_LABELS = ["<60", "60-70", "70-80", "80-90", "90-95", "95+"]
ATR_BINS = [0, 3, 5, 8, 999]          # %
ATR_LABELS = ["<3", "3-5", "5-8", "8+"]
MKT_BINS = [-999, -6, 0, 999]         # 大盤乖離季線 %
MKT_LABELS = ["深崩", "偏弱", "正常"]


def main() -> None:
    import os
    m = pd.read_pickle(_CACHE) if os.path.exists(_CACHE) else _build_cache()
    m = m[m["date"] >= "2021-01-01"].copy()
    m = m[m["vol_ma20"].notna() & (m["vol_ma20"] >= _MIN_VOL)]

    # 會噴分數：四因子合成 → 全市場百分位重排名（與 finalize_wave_pop 同式）
    g = m.groupby("date")
    ra = g["atr_pct"].rank(pct=True)
    rl = g["ma_align"].rank(pct=True)
    rp = g["pos_52w"].rank(pct=True).fillna(0.5)
    rb = g["pb"].rank(pct=True).fillna(0.5)
    comp = (2 * ra + rl + rp + rb) / 5
    m["score"] = comp.groupby(m["date"]).rank(pct=True) * 100

    m["s_bin"] = pd.cut(m["score"], SCORE_BINS, labels=SCORE_LABELS, right=False)
    m["a_bin"] = pd.cut(m["atr_pct"] * 100, ATR_BINS, labels=ATR_LABELS, right=False)
    m["m_bin"] = pd.cut(m["mkt_bias60"], MKT_BINS, labels=MKT_LABELS, right=False)
    # 2026-08 定版：推薦目標改為「10 日內碰到 +10%」（資金周轉導向；30日窗見 hit 欄）
    m["hit"] = m["hit10"]
    m["mae30"] = m["mae10"]  # 卡片顯示的同條件回撤也對齊 10 日窗
    m = m.dropna(subset=["s_bin", "a_bin", "m_bin", "hit"])

    def cells(keys: list[str]) -> dict:
        out = {}
        for k, gsel in m.groupby(keys, observed=True):
            key = "|".join(k) if isinstance(k, tuple) else str(k)
            out[key] = {"hit": round(float(gsel["hit"].mean()) * 100, 1), "n": int(len(gsel)),
                        "mae": round(float(gsel["mae30"].mean()), 1)}
        return out

    table = {
        "window": {"from": str(m["date"].min())[:10], "to": str(m["date"].max())[:10]},
        "note": "同條件歷史命中率（隔日高錨、30交易日摸+10%）；描述統計非保證。",
        "bins": {"score": SCORE_BINS, "score_labels": SCORE_LABELS,
                 "atr": ATR_BINS, "atr_labels": ATR_LABELS,
                 "mkt": MKT_BINS, "mkt_labels": MKT_LABELS},
        "full": cells(["s_bin", "a_bin", "m_bin"]),   # 分數×波動×大盤
        "sa": cells(["s_bin", "a_bin"]),              # 回退1：去大盤
        "s": cells(["s_bin"]),                        # 回退2：只看分數
        "global": {"hit": round(float(m["hit"].mean()) * 100, 1), "n": int(len(m)),
                   "mae": round(float(m["mae30"].mean()), 1)},
    }
    with open(_OUT, "w", encoding="utf-8") as fh:
        json.dump(table, fh, ensure_ascii=False, indent=1)
    print(f"格子 full={len(table['full'])} sa={len(table['sa'])} → {_OUT}")
    # 摘要：正常市況下各分數帶×波動帶
    for s in SCORE_LABELS:
        row = []
        for a in ATR_LABELS:
            c = table["full"].get(f"{s}|{a}|正常")
            row.append(f"{a}:{c['hit']:.0f}%({c['n']})" if c else f"{a}: --")
        print(f"  分數{s:>6} | " + "  ".join(row))


if __name__ == "__main__":
    main()
