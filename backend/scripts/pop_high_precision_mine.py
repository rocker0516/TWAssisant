"""高精度角落挖掘：目標「一推薦就 ≥70%」、允許空手。

使用者拍板：唯一目標=30日內摸+10%（隔日高錨），不管漏掉多少、大盤差可空手。
紀律（同判官）：只在 2021~2024 挖掘窗上選條件，**評選標準=分年地板命中**（不是
全期平均——全期會被 2026 大多頭灌水）；2025~2026 僅事後檢查且已非乾淨 holdout
（2026-07-28 燒過一次），結果只能當軟證據。

做法：以已過 holdout 的標籤合取為底座（strong∩story / expl∩story / story∩crash
/ 聯集U），逐一疊單一 refinement 特徵，列出挖掘窗分年命中、地板、檔/日，
再列 2025/2026。人工判讀，不自動選最優（避免多重比較撿僥倖）。

用法：.venv/bin/python scripts/pop_high_precision_mine.py
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from pop_condition_judge import _CACHE, _build_cache  # noqa: E402
from app.engines.rules.wave import (  # noqa: E402
    CRASH_ATR_MIN, CRASH_MKT_BIAS60, EXPLOSIVE_ATR_MIN,
    STORY_ATR_MIN, STORY_PB_MIN, STORY_PE_MIN, STRONG_OVER_MA20, STRONG_POS_MIN,
)

MINE_YEARS = ["2021", "2022", "2023", "2024"]
CHECK_YEARS = ["2025", "2026"]
MIN_N_MINE = 150  # 挖掘窗四年合計最少樣本，太薄不看


def main() -> None:
    df = pd.read_pickle(_CACHE) if os.path.exists(_CACHE) else _build_cache()
    m = df[df["date"] >= "2021-01-01"].copy()
    m["year"] = m["date"].astype(str).str[:4]
    ndays = m.groupby("year")["date"].nunique()

    liq = m["vol_ma20"].notna() & (m["vol_ma20"] >= 500 * 1000)
    above_rising = (m["c_over_ma20"] > 0) & m["ma20_up5"].fillna(False)
    E = (liq & (m["atr_pct"] > EXPLOSIVE_ATR_MIN) & above_rising).fillna(False)
    S = (liq & (m["pos_52w"] > STRONG_POS_MIN)
         & (m["c_over_ma20"] > STRONG_OVER_MA20)).fillna(False)
    T = (liq & (m["pb"] > STORY_PB_MIN) & (m["pe"] > STORY_PE_MIN)
         & (m["atr_pct"] > STORY_ATR_MIN)).fillna(False)
    C = ((S | T) & (m["atr_pct"] > CRASH_ATR_MIN)
         & (m["mkt_bias60"] <= CRASH_MKT_BIAS60)).fillna(False)

    bases = {
        "strong∩story": S & T,
        "expl∩story": E & T,
        "story∩crash": T & C,
        "聯集U": (S & T) | (E & T) | (T & C),
    }
    refs = {
        "(無疊加)": pd.Series(True, index=m.index),
        "sq_ratio>5": m["sq_ratio"] > 5,
        "sq_ratio>10": m["sq_ratio"] > 10,
        "inst_f5>0": m["inst_f5"] > 0,
        "inst_streak≥3": m["inst_streak"] >= 3,
        "atr>8": m["atr_pct"] > 8,
        "atr>10": m["atr_pct"] > 10,
        "vol_ratio>2": m["vol_ratio"] > 2,
        "peer_surge5>0.1": m["peer_surge5"] > 0.1,
        "pos52w>0.9": m["pos_52w"] > 0.9,
        "近60高<-15": m["dist_60d_high"] < -15,
        "rel_ret20>0": m["rel_ret20"] > 0,
        "深崩mkt≤-6": m["mkt_bias60"] <= -6,
    }

    for bname, bmask in bases.items():
        print(f"\n=== 底座 {bname} ===")
        for rname, rmask in refs.items():
            sel = m[bmask & rmask.fillna(False)]
            mine = sel[sel["year"].isin(MINE_YEARS)]
            if len(mine) < MIN_N_MINE:
                continue
            ys = []
            floor = 100.0
            for y in MINE_YEARS:
                g = sel[sel["year"] == y]
                if len(g) < 15:
                    ys.append(f"{y[2:]}: --")
                    continue
                h = g["hit"].mean() * 100
                floor = min(floor, h)
                ys.append(f"{y[2:]}:{h:3.0f}%({len(g)/ndays[y]:.1f})")
            chk = []
            for y in CHECK_YEARS:
                g = sel[sel["year"] == y]
                chk.append(f"{y[2:]}:{g['hit'].mean()*100:3.0f}%({len(g)/ndays[y]:.1f})"
                           if len(g) >= 15 else f"{y[2:]}: --")
            mark = " ★" if floor >= 70 else ""
            print(f"  {rname:>15} 地板{floor:3.0f}%{mark}  挖掘窗 " + " ".join(ys)
                  + f"  n={len(mine)}  | 檢查 " + " ".join(chk))


if __name__ == "__main__":
    main()
