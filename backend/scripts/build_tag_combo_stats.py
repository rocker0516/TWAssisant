"""標籤組合 × 五年實證命中統計 → data/tag_combo_stats.json（推薦頁卡片顯示用）。

對 forward_labels 全期（2021-01~有標籤的最新日）重建每檔每日的標籤集合
（與線上同定義：pop=硬篩+四因子rank≥80、explosive/strong/story/crash），
統計每種「精確組合」與每個標籤的「邊際」(any:) 10 日內摸+10%命中率、平均MAE。
推薦頁標籤開關列直接顯示 any:<tag>，避免手抄挖掘數字放到過期。
說明：pop 用預設前 20% 口徑、硬篩不含遲滯寬限；描述性統計非新回測。
用法：python scripts/build_tag_combo_stats.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, __file__.replace("\\", "/").rsplit("/scripts/", 1)[0])
from pop_condition_judge import _CACHE, _build_cache  # noqa: E402
from app.engines.rules.wave import (  # noqa: E402
    CRASH_ATR_MIN, CRASH_MKT_BIAS60, CRASH_PX_MIN, CRASH_TURNOVER_MIN,
    EXPLOSIVE_ATR_MIN,
    STORY_ATR_MIN, STORY_PB_MIN, STORY_PE_MIN, STRONG_OVER_MA20, STRONG_POS_MIN,
)

_BASE = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0]
_OUT = _BASE + "/data/tag_combo_stats.json"
_DB = _BASE + "/data/twa.db"
_MIN_VOL = 500 * 1000
_HOLDOUT_FROM = "2025-07-01"  # 雙段檢定切點：加成條件要挖掘窗＋holdout 都贏基線才算數


def _clean_pool(m: pd.DataFrame) -> pd.Series:
    """乾淨池：近 5 交易日被列注意、近 10 交易日被列處置者不算（同線上 crash 口徑）。

    命中率是在這個池上量的，統計端不排就會與線上掛出來的名單口徑不一致。
    """
    con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    att = pd.read_sql_query("SELECT stock_id sid, date, kind FROM attention_listings", con)
    con.close()
    days = sorted(m["date"].unique())
    pos = {d: i for i, d in enumerate(days)}
    dirty: set[tuple[str, str]] = set()
    for sid, d0, kind in att.itertuples(index=False):
        p0 = pos.get(d0)
        if p0 is None:
            continue
        for k in range(5 if kind == "notice" else 10):
            if p0 + k < len(days):
                dirty.add((sid, days[p0 + k]))
    return pd.Series([(s_, d_) not in dirty for s_, d_ in zip(m["stock_id"], m["date"])],
                     index=m.index)


def _episodes(dates, gap: int = 15) -> dict:
    """訊號日相隔 >15 個日曆日＝換一段行情（同 docs/wave-hit-challenge.md §5）。"""
    ds = pd.to_datetime(sorted(set(dates)))
    ep, cur, out = 0, ds[0], {}
    for x in ds:
        if (x - cur).days > gap:
            ep += 1
        out[x.strftime("%Y-%m-%d")] = ep
        cur = x
    return out


def style_masks(m: pd.DataFrame) -> dict[str, pd.Series]:
    """四個純門檻風格的向量化遮罩——**這是唯一一份定義**，build_prob_table.py 也 import 它。

    與 app/engines/rules/wave.py 同式（常數直接 import，不抄數字）。crash 另含乾淨池，
    因為線上 ScoringEngine._apply_crash_style 也排除注意/處置窗內的標的。
    """
    liq = m["vol_ma20"].notna() & (m["vol_ma20"] >= _MIN_VOL)
    above_rising = (m["c_over_ma20"] > 0) & m["ma20_up5"].fillna(False)
    return {
        "explosive": (liq & (m["atr_pct"] > EXPLOSIVE_ATR_MIN) & above_rising).fillna(False),
        "strong": (liq & (m["pos_52w"] > STRONG_POS_MIN)
                   & (m["c_over_ma20"] > STRONG_OVER_MA20)).fillna(False),
        "story": (liq & (m["pb"] > STORY_PB_MIN) & (m["pe"] > STORY_PE_MIN)
                  & (m["atr_pct"] > STORY_ATR_MIN)).fillna(False),
        # crash 2026-08-24 改版（同 wave.crash_cand_ok + scoring._apply_crash_style）
        "crash": (liq & (m["atr_pct"] > CRASH_ATR_MIN)
                  & (m["mkt_bias60"] <= CRASH_MKT_BIAS60)
                  & (m["close"] >= CRASH_PX_MIN)
                  & (m["close"] * m["volume"] >= CRASH_TURNOVER_MIN)
                  & _clean_pool(m)).fillna(False),
    }


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
    for _t, _mask in style_masks(m).items():
        m[f"t_{_t}"] = _mask

    # 2026-08 定版：目標＝「10 日內碰到 +10%」（與 build_prob_table.py／實驗室同口徑）。
    # 舊版誤用 hit/mae30（30 日窗）→ 標籤命中率全面虛高，深跌反攻尤甚。
    m["hit"] = m["hit10"]
    m["mae30"] = m["mae10"]

    tags = ["pop", "explosive", "strong", "story", "crash"]
    cols = [f"t_{t}" for t in tags]
    any_tag = m[cols].any(axis=1)
    sub = m[any_tag]
    print(f"全期 {m['date'].min()} ~ {m['date'].max()}，有標籤列 {len(sub):,}")

    stats: dict[str, dict] = {}
    is_ho = sub["date"] >= _HOLDOUT_FROM

    def _seg(gsel: pd.DataFrame) -> tuple[int, float | None]:
        n = len(gsel)
        return n, (round(float(gsel["hit"].mean()) * 100, 1) if n >= 30 else None)

    def _ep_spread(gsel: pd.DataFrame) -> dict:
        """段級中位／最差／≥70% 段數。日加權平均會被最大的一段綁架，單一數字必然誤導
        （docs/wave-hit-challenge.md §5）；只計 n≥10 的段。"""
        ep = gsel["date"].map(_episodes(gsel["date"]))
        g_ = gsel.assign(_ep=ep).groupby("_ep")["hit"].agg(["size", "mean"])
        k = g_[g_["size"] >= 10]["mean"] * 100
        if len(k) < 2:
            return {"ep_n": int(len(k))}
        return {"ep_n": int(len(k)), "ep_median": round(float(k.median()), 1),
                "ep_min": round(float(k.min()), 1), "ep_max": round(float(k.max()), 1),
                "ep_ge70": int((k >= 70).sum())}

    def _put(key: str, gsel: pd.DataFrame) -> None:
        n = len(gsel)
        if n < 30:
            return
        n_tr, hit_tr = _seg(gsel[~is_ho.loc[gsel.index]])
        n_ho, hit_ho = _seg(gsel[is_ho.loc[gsel.index]])
        stats[key] = {
            "n": int(n),
            "hit": round(float(gsel["hit"].mean()) * 100, 1),
            "avg_mae": round(float(gsel["mae30"].mean()), 1),
            "n_tr": int(n_tr), "hit_tr": hit_tr,   # 挖掘窗（< _HOLDOUT_FROM）
            "n_ho": int(n_ho), "hit_ho": hit_ho,   # holdout（>= _HOLDOUT_FROM）
            **_ep_spread(gsel),                    # 段級離散（崩勢型標籤的有效樣本是段數）
        }

    # 精確組合
    combo = sub[cols].apply(lambda r: "+".join(t for t, c in zip(tags, cols) if r[c]), axis=1)
    for key, gsel in sub.groupby(combo):
        _put(key, gsel)
    # 邊際（含該標籤的全部列）
    for t, c in zip(tags, cols):
        _put(f"any:{t}", sub[sub[c]])

    # 量能加成條件：與線上徽章同定義（vol_ma5/vol_ma20，非快取的 volume/vol_ma20）。
    # 推薦卡「⚡量增共振」「🤫量縮惜售」的數字就從這裡來，不再手抄。
    volr = sub["vol_ma5"] / sub["vol_ma20"]
    for t, c in zip(tags, cols):
        for cname, mask in (("volup", volr > 1.5), ("voldn", volr < 0.8)):
            _put(f"lift:{t}|{cname}", sub[sub[c] & mask.fillna(False)])

    out = {
        "generated_at": date.today().isoformat(),
        "window": {"from": str(m["date"].min()), "to": str(m["date"].max())},
        "holdout_from": _HOLDOUT_FROM,  # 前端標「單段實證」用，不要在 UI 端寫死
        "note": ("五年實證：同標籤組合隔日高錨、10 交易日內摸 +10% 比率（**無停損**，與波段軌"
                 "定版口徑一致）；pop=預設前20%口徑(無遲滯)；ep_* 為段級離散度——"
                 "崩勢型標籤的有效樣本數是段數不是筆數，單看 hit 會被最大的一段綁架。"),
        "stats": stats,
    }
    with open(_OUT, "w", encoding="utf-8") as fh:  # Windows 預設 cp950，端點以 utf-8 讀會 500
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(f"寫出 {len(stats)} 組 → {_OUT}")
    for k in sorted(stats, key=lambda k_: -stats[k_]["n"])[:12]:
        v = stats[k]
        print(f"  {k:<28} n={v['n']:>7,}  命中{v['hit']:5.1f}%  MAE{v['avg_mae']:+6.1f}%")

    # 量能加成雙段檢定：兩段都要 >4pp 才算穩健（沿用 2026-08-14 挖掘門檻）
    print(f"\n── 量能加成（挖掘窗 <{_HOLDOUT_FROM} / holdout ≥）──")
    for t in tags:
        base = stats.get(f"any:{t}")
        if not base:
            continue
        for cname, label in (("volup", "×量增>1.5"), ("voldn", "×量縮<0.8")):
            v = stats.get(f"lift:{t}|{cname}")
            if not v or v["hit_tr"] is None or v["hit_ho"] is None:
                continue
            d_tr, d_ho = v["hit_tr"] - (base["hit_tr"] or 0), v["hit_ho"] - (base["hit_ho"] or 0)
            ok = "✔穩健" if min(d_tr, d_ho) > 4 else ("✖" if min(d_tr, d_ho) <= 0 else "△邊緣")
            print(f"  {t:<10}{label:<10} {v['hit_tr']:5.1f}%/{v['hit_ho']:5.1f}%"
                  f"（基線 {base['hit_tr']:.1f}%/{base['hit_ho']:.1f}%，"
                  f"lift {d_tr:+.1f}/{d_ho:+.1f}pp，n={v['n']:,}）{ok}")


if __name__ == "__main__":
    main()
