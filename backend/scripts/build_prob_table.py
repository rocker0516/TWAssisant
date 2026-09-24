"""條件機率查表 → data/prob_table.json（推薦頁每檔顯示「同條件歷史命中率」用）。

格子 = 波動帶(絕對 ATR%) × 會噴分數帶(全市場百分位) × 大盤狀態(乖離季線)。
量尺依挖掘定論：ATR/大盤=絕對（機制軸）、分數本身已是相對排名。
對 forward_labels 全期（2021-01~最新）統計每格「隔日高錨 10 交易日內碰到 +10%」
命中率（**無停損**，與波段軌定版口徑一致）；serve 時 n<150 的格子逐層回退
（去分數 → 去大盤 → 只看分數 → 全域）。
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
from build_tag_combo_stats import style_masks  # noqa: E402  （風格定義只有那一份）

_OUT = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0] + "/data/prob_table.json"
_MIN_VOL = 500 * 1000

# 帶界定義（serve 端 routes.py 用同一份，存進 json）
SCORE_BINS = [0, 60, 70, 80, 90, 95, 101]
SCORE_LABELS = ["<60", "60-70", "70-80", "80-90", "90-95", "95+"]
# 2026-08-24：8+ 再切一刀。crash 風格改版後門檻是 ATR>9%，100% 的 crash 推薦都擠在舊的
# 「8+」一格裡（實際中位 10.2%、59% 在 10% 以上），而「深崩×8-10」與「深崩×10-12」的
# 歷史命中差 19pp（60.8% vs 79.7%）——不切開就等於把兩件事平均掉。
ATR_BINS = [0, 3, 5, 8, 10, 999]      # %
ATR_LABELS = ["<3", "3-5", "5-8", "8-10", "10+"]
MKT_BINS = [-999, -6, 0, 999]         # 大盤乖離季線 %
MKT_LABELS = ["深崩", "偏弱", "正常"]


def _wilson_lb(p: float, n: int, z: float = 1.96) -> float:
    """Wilson 95% 下界。薄格子自動被壓低，厚格子幾乎不動。

    walk-forward 實測（15 季樣本外）：裸命中率在高機率端系統性高估 +5.9pp，改用下界後
    收斂到 −1.5pp，而 Brier(0.1461→0.1464)/AUC(0.666→0.664) 幾乎不變 —— 純賺。
    對照組 isotonic 重校準與「只用近 2 年」都反而更差，shrink-to-base 則過度收縮成 −10.4pp。
    """
    if n <= 0:
        return 0.0
    d = 1.0 + z * z / n
    c = (p + z * z / (2 * n)) / d
    hw = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return max(0.0, c - hw)


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
            p, n = float(gsel["hit"].mean()), int(len(gsel))
            out[key] = {"hit": round(p * 100, 1), "hit_lb": round(_wilson_lb(p, n) * 100, 1),
                        "n": n, "mae": round(float(gsel["mae30"].mean()), 1)}
        return out

    # 風格分層格子（2026-08-24）：卡片有風格標籤時，用「該風格 × 波動 × 大盤」的歷史，
    # 而不是全市場同格。實測（70,288 筆清單列回算）加權|誤差| 4.3pp→2.9pp，四個風格全改善：
    # explosive +9.3→+5.3、strong +14.0→+4.4、story +9.7→+7.0、crash +18.0→+13.9pp。
    # 對照組「用風格的歷史命中當常數」反而更差（4.9pp）——因為丟掉了每檔不同的 ATR/大盤條件。
    style_cells: dict = {}
    style_all: dict = {}
    for _tag, _mask in style_masks(m).items():
        sm = m[_mask].dropna(subset=["a_bin", "m_bin"])
        if sm.empty:
            continue
        for k, gsel in sm.groupby(["a_bin", "m_bin"], observed=True):
            p_, n_ = float(gsel["hit"].mean()), int(len(gsel))
            style_cells[f"{_tag}|{k[0]}|{k[1]}"] = {
                "hit": round(p_ * 100, 1), "hit_lb": round(_wilson_lb(p_, n_) * 100, 1),
                "n": n_, "mae": round(float(gsel["mae30"].mean()), 1)}
        p_, n_ = float(sm["hit"].mean()), int(len(sm))
        style_all[_tag] = {"hit": round(p_ * 100, 1), "hit_lb": round(_wilson_lb(p_, n_) * 100, 1),
                           "n": n_, "mae": round(float(sm["mae30"].mean()), 1)}

    table = {
        "window": {"from": str(m["date"].min())[:10], "to": str(m["date"].max())[:10]},
        "note": ("同條件歷史命中率（隔日高錨、10交易日摸+10%）；serve 用 hit_lb"
                 "（Wilson 95% 下界，修高機率端的系統性高估）；描述統計非保證。"),
        "bins": {"score": SCORE_BINS, "score_labels": SCORE_LABELS,
                 "atr": ATR_BINS, "atr_labels": ATR_LABELS,
                 "mkt": MKT_BINS, "mkt_labels": MKT_LABELS},
        "full": cells(["s_bin", "a_bin", "m_bin"]),   # 分數×波動×大盤
        # 回退1（2026-08-24 新增）：**先丟分數、保留大盤**。分數＝池內排序，研究實測增量
        # 上限僅 +3pp（docs/wave-hit-challenge.md §6-b GBM 零移轉）；ATR 與大盤才是機制軸。
        # 舊版第一步就丟大盤，高波動格子一薄就被「正常盤」稀釋 → crash 卡片系統性低估。
        "am": cells(["a_bin", "m_bin"]),
        "sa": cells(["s_bin", "a_bin"]),              # 回退2：去大盤
        "s": cells(["s_bin"]),                        # 回退3：只看分數
        "style": style_cells,                         # 風格×波動×大盤（有標籤時優先）
        "style_all": style_all,                       # 風格整體（風格格子太薄時回退）
        "global": {"hit": round(float(m["hit"].mean()) * 100, 1),
                   "hit_lb": round(_wilson_lb(float(m["hit"].mean()), int(len(m))) * 100, 1),
                   "n": int(len(m)), "mae": round(float(m["mae30"].mean()), 1)},
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
