"""標籤組合 × 五年實證命中統計 → data/tag_combo_stats.json（推薦頁卡片顯示用）。

對 forward_labels 全期（2021-01~有標籤的最新日）重建每檔每日的標籤集合
（與線上同定義：pop=硬篩+四因子rank≥80、explosive/strong/story/crash），
統計每種「精確組合」與每個標籤的「邊際」(any:) 摸+10%命中率、平均MAE。
說明：pop 用預設前 20% 口徑、硬篩不含遲滯寬限；描述性統計非新回測。
用法：python scripts/build_tag_combo_stats.py
"""
from __future__ import annotations

import json
import sys
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])
from pop_condition_judge import _CACHE, _build_cache  # noqa: E402
from app.engines.rules.wave import (  # noqa: E402
    CRASH_ATR_MIN, CRASH_MKT_BIAS60, EXPLOSIVE_ATR_MIN,
    STORY_ATR_MIN, STORY_PB_MIN, STORY_PE_MIN, STRONG_OVER_MA20, STRONG_POS_MIN,
)

_OUT = __file__.rsplit("/scripts/", 1)[0] + "/data/tag_combo_stats.json"
_MIN_VOL = 500 * 1000


def main() -> None:
    import os
    df = pd.read_pickle(_CACHE) if os.path.exists(_CACHE) else _build_cache()
    m = df[df["date"] >= "2021-01-01"].copy()

    liq = m["vol_ma20"].notna() & (m["vol_ma20"] >= _MIN_VOL)
    above_rising = (m["c_over_ma20"] > 0) & m["ma20_up5"].fillna(False)
    hard = above_rising & (m["bias_60"].abs() < 15)

    # 四因子 rank（與 finalize_wave_pop 同式，逐日全市場）
    g = m.groupby("date")
    ra = g["atr_pct"].rank(pct=True)
    rl = g["ma_align"].rank(pct=True)
    rp = g["pos_52w"].rank(pct=True).fillna(0.5)
    rb = g["pb"].rank(pct=True).fillna(0.5)
    comp = (2 * ra + rl + rp + rb) / 5
    pop_score = comp.groupby(m["date"]).rank(pct=True) * 100  # 合成再重排名（與線上同式）
    m["t_pop"] = (hard & liq & (pop_score >= 80)).fillna(False)
    m["t_explosive"] = (liq & (m["atr_pct"] > EXPLOSIVE_ATR_MIN) & above_rising).fillna(False)
    m["t_strong"] = (liq & (m["pos_52w"] > STRONG_POS_MIN)
                     & (m["c_over_ma20"] > STRONG_OVER_MA20)).fillna(False)
    m["t_story"] = (liq & (m["pb"] > STORY_PB_MIN) & (m["pe"] > STORY_PE_MIN)
                    & (m["atr_pct"] > STORY_ATR_MIN)).fillna(False)
    m["t_crash"] = ((m["t_strong"] | m["t_story"]) & (m["atr_pct"] > CRASH_ATR_MIN)
                    & (m["mkt_bias60"] <= CRASH_MKT_BIAS60)).fillna(False)

    tags = ["pop", "explosive", "strong", "story", "crash"]
    cols = [f"t_{t}" for t in tags]
    any_tag = m[cols].any(axis=1)
    sub = m[any_tag]
    print(f"全期 {m['date'].min()} ~ {m['date'].max()}，有標籤列 {len(sub):,}")

    stats: dict[str, dict] = {}

    def _put(key: str, gsel: pd.DataFrame) -> None:
        n = len(gsel)
        if n < 30:
            return
        stats[key] = {
            "n": int(n),
            "hit": round(float(gsel["hit"].mean()) * 100, 1),
            "avg_mae": round(float(gsel["mae30"].mean()), 1),
        }

    # 精確組合
    combo = sub[cols].apply(lambda r: "+".join(t for t, c in zip(tags, cols) if r[c]), axis=1)
    for key, gsel in sub.groupby(combo):
        _put(key, gsel)
    # 邊際（含該標籤的全部列）
    for t, c in zip(tags, cols):
        _put(f"any:{t}", sub[sub[c]])

    out = {
        "generated_at": date.today().isoformat(),
        "window": {"from": str(m["date"].min()), "to": str(m["date"].max())},
        "note": "五年實證：同標籤組合隔日高錨、30交易日內摸+10%比率；pop=預設前20%口徑(無遲滯)",
        "stats": stats,
    }
    with open(_OUT, "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(f"寫出 {len(stats)} 組 → {_OUT}")
    for k in sorted(stats, key=lambda k_: -stats[k_]["n"])[:12]:
        v = stats[k]
        print(f"  {k:<28} n={v['n']:>7,}  命中{v['hit']:5.1f}%  MAE{v['avg_mae']:+6.1f}%")


if __name__ == "__main__":
    main()
