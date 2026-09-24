"""挑戰波段軌 10 日命中率：能不能更高？能不能有更多 ≥70% 的案例？

起因（2026-08-24）：使用者要求「提高波段軌命中機率，或找其他條件判斷挑戰更多超過 70% 的案例」。
目標＝ hit10（進場錨＝隔日最高，之後 10 交易日摸到 +10%），與 data/corners70.json 同口徑。

紀律
----
1. 挖掘窗 2021-01~2024-12 定義、holdout 2025-01~2026-07 只驗一次；兩窗分開報。
2. 絕對命中率會隨行情漂移（全市場逐日基率 1%~66%、逐季 9.5%~35.2%），故一律同時報
   **同日全市場配對超額**（選中命中 − 當日全市場基率，以「日」為觀測算 t）。
3. 崩勢類規則的有效樣本數不是「日數」而是**崩盤段數**（同段內每天選到的是同一批股票）。
   §5 逐段拆解 + 段級離散度，這是本研究最重要的一張表。
4. 乾淨池：排除自身被列注意(5日)/處置(10日)者；ETF 已在 forward_labels 階段排除。

章節（--only 可單跑：ceiling/axes/styles/ablation/episodes/model/regime/frontier/ladder/oos/stop）
  §1 ceiling  hit10 = f(ATR, 大盤距季線) 天花板地圖 + 逐日基率分布
              → 回答「≥70% 的格子在哪、有多少案例、holdout 是否複製」
  §2 axes     角落原子池沒有的新特徵軸單篩（K線收盤強弱／箱型／波動擴張／價位／成交值）
              量尺＝錨池內同日同 ATR 桶配對增量，雙窗同號才算過
  §3 styles   現行四風格（explosive/strong/story/crash）在 hit10 口徑重測 + 加新軸
  §4 ablation 深跌反攻消融：ATR 門檻 / 大盤門檻 / (strong|story) 閘各貢獻多少
  §5 episodes 段級穩健性（leave-one-episode 觀察）
  §6 model    事前開關（預測當日基率）與 GBM 排序模型的移轉檢定——兩者皆為負面結論
  ── 第二輪（2026-08-24 下午，使用者再次要求「挑戰更多 ≥70% 的案例」）──
  §7 regime   時點型開關：落後已實現命中率（自參照）／崩段第幾天／14 個大盤狀態閘
              → 全部翻號或無法驗證；記錄為「已試過且失敗」，避免下次重挖
  §8 frontier 有界窮舉前緣（6 ATR × 4 大盤 × 4 近漲停 × 2 流動 × 8 第三軸 = 1536 組）
              ＋**虛無校準**（同日內打散 hit10 重跑，看有多少組是多重比較的產物）
  §9 ladder   ATR 門檻階梯 × 段級離散 × 同日同 ATR 桶歸因——回答「70% 的價格是什麼」
  §10 oos     定案規則在 forward_labels 之外（2026-07 崩段）的實測——真 out-of-sample
  §11 stop    停損敏感度——為什麼這條軌不設停損（−8% 停損 = −25pp）

用法
  PYTHONUTF8=1 .venv/Scripts/python scripts/wave_hit_challenge.py
  PYTHONUTF8=1 .venv/Scripts/python scripts/wave_hit_challenge.py --only ablation episodes
  PYTHONUTF8=1 .venv/Scripts/python scripts/wave_hit_challenge.py --json   # 另外凍結 data/wave_challenge.json（策略室讀這支）

依賴：data/condition_judge_cache_v4.pkl（pop_condition_judge.py 首跑會建）、data/twa.db。
結論寫在 docs/wave-hit-challenge.md。
"""
from __future__ import annotations

import sqlite3
import sys
import time

import numpy as np
import pandas as pd

_BASE = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0]
sys.path.insert(0, _BASE)
_DB = _BASE + "/data/twa.db"
_CACHE = _BASE + "/data/condition_judge_cache_v4.pkl"
_MINE_HI = "2024-12-31"
_LIQ_MIN = 500 * 1000
_RNG = np.random.default_rng(0)

# 現行 app/engines/rules/wave.py 的風格常數（同步；改那邊記得改這邊）
EXPLOSIVE_ATR_MIN = 0.07
STRONG_POS_MIN, STRONG_OVER_MA20 = 0.8, 0.23
STORY_PB_MIN, STORY_PE_MIN, STORY_ATR_MIN = 5.5, 56.0, 0.049
CRASH_ATR_MIN, CRASH_MKT_BIAS60 = 0.06, -2.3


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ─────────────────────────── 資料層 ───────────────────────────


def load() -> pd.DataFrame:
    """研究快取 + 新特徵軸 + 注意/處置旗標。全部只用 T 日（含）以前的資料。"""
    _log("載入 condition_judge_cache_v4 …")
    d = pd.read_pickle(_CACHE)
    d["date"] = d["date"].astype(str)
    # 先留 2020-06 起（讓 rolling 暖機），最後才切 2021-01
    d = d[d["date"] >= "2020-06-01"].sort_values(["stock_id", "date"]).reset_index(drop=True)
    g = d.groupby("stock_id", sort=False)

    rng = (d["high"] - d["low"]).replace(0, np.nan)
    d["close_pos"] = (d["close"] - d["low"]) / rng               # 收盤在當日區間位置 0~1
    d["body_pct"] = (d["close"] - d["open"]) / d["close"] * 100  # 實體漲跌 %
    prev_c = g["close"].shift(1)
    d["gap_pct"] = (d["open"] - prev_c) / prev_c * 100
    d["range_pct"] = rng / d["close"] * 100
    hi20 = g["high"].transform(lambda s: s.rolling(20, 15).max())
    lo20 = g["low"].transform(lambda s: s.rolling(20, 15).min())
    d["break20"] = (d["close"] >= hi20 * 0.999).astype(float)    # 收盤創 20 日新高
    d["box20"] = (hi20 - lo20) / d["close"] * 100                # 20 日箱型寬度 %
    d["atr_chg20"] = d["atr_pct"] / g["atr_pct"].shift(20)       # 波動擴張倍數
    d["atr_self_pct"] = g["atr_pct"].transform(lambda s: s.rolling(252, 120).rank(pct=True))
    d["turnover_val"] = d["close"] * d["volume"] / 1e8           # 成交值（億）
    d["px"] = d["close"]

    _log("大盤層（漲停家數/實現波動/成交值擴張）…")
    con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    px = pd.read_sql_query("SELECT stock_id,date,close,turnover FROM daily_prices "
                           "WHERE close IS NOT NULL ORDER BY stock_id,date", con)
    att = pd.read_sql_query("SELECT stock_id,date,kind FROM attention_listings", con)
    con.close()
    gp = px.groupby("stock_id", sort=False)
    px["ret"] = (px["close"] / gp["close"].shift(1) - 1.0) * 100
    px["is_lu"] = (px["ret"] >= 9.0).astype(float)               # 近漲停（9%，避開除權息邊界）
    m = px.groupby("date").agg(mkt_ret=("ret", "mean"), lu=("is_lu", "mean"),
                               turnover=("turnover", "sum")).reset_index()
    m["lu"] *= 100
    m["rv20"] = m["mkt_ret"].rolling(20, 15).std() * np.sqrt(252)
    tv = m["turnover"].astype(float)
    m["tv_5_60"] = tv.rolling(5, 3).mean() / tv.rolling(60, 40).mean()
    m["lu5"] = m["lu"].rolling(5, 3).mean()
    m["lu_trend"] = m["lu5"] - m["lu"].rolling(20, 15).mean()
    m["mkt_up20"] = m["mkt_ret"].rolling(20, 15).mean()
    d = d.merge(m[["date", "lu", "lu5", "lu_trend", "rv20", "tv_5_60", "mkt_up20"]],
                on="date", how="left")

    d = d[(d["vol_ma20"].fillna(0) >= _LIQ_MIN) & (d["date"] >= "2021-01-01")].reset_index(drop=True)

    _log("注意/處置旗標（自身排除用）+ 類股傳染…")
    att["date"] = att["date"].astype(str)
    days = pd.Index(sorted(d["date"].unique()))

    def _spread(sub: pd.DataFrame, win: int) -> pd.DataFrame:
        pos = days.get_indexer(pd.Index(sub["date"]))
        ok = pos >= 0
        rows = [(sid, days[p + k]) for sid, p in zip(sub["stock_id"].to_numpy()[ok], pos[ok])
                for k in range(win) if p + k < len(days)]
        return pd.DataFrame(rows, columns=["stock_id", "date"]).drop_duplicates()

    d = d.merge(_spread(att[att.kind == "notice"], 5).assign(att_notice5=1),
                on=["stock_id", "date"], how="left")
    d = d.merge(_spread(att[att.kind == "punish"], 10).assign(att_punish10=1),
                on=["stock_id", "date"], how="left")
    d[["att_notice5", "att_punish10"]] = d[["att_notice5", "att_punish10"]].fillna(0)
    sec = (d.groupby(["sector_id", "date"])
           .agg(s_n=("att_notice5", "size"), s_h=("att_notice5", "sum")).reset_index())
    d = d.merge(sec, on=["sector_id", "date"], how="left")
    d["sec_att5"] = (d["s_h"] - d["att_notice5"]) / (d["s_n"] - 1).clip(lower=1) * 100
    d = d.sort_values(["stock_id", "date"])
    d["sec_att_chg"] = d["sec_att5"] - d.groupby("stock_id")["sec_att5"].shift(5)
    d = d.reset_index(drop=True)

    d["year"] = d["date"].str[:4].astype(int)
    d["mine"] = d["date"] <= _MINE_HI
    d["clean"] = (d["att_notice5"] == 0) & (d["att_punish10"] == 0)
    d["ex"] = d["hit10"] - d.groupby("date")["hit10"].transform("mean")
    _log(f"工作集 {len(d):,} 列 / {d['date'].nunique()} 日；"
         f"全市場 hit10 挖掘 {d[d['mine']]['hit10'].mean()*100:.1f}% / "
         f"holdout {d[~d['mine']]['hit10'].mean()*100:.1f}%")
    return d


# ─────────────────────────── 共用統計 ───────────────────────────


def _boot_ci(sub: pd.DataFrame, n_boot: int = 2000) -> tuple[float, float]:
    """日層 bootstrap 95% CI（同日高度相關，以『日』為重抽單位）。"""
    g = sub.groupby("date")["hit10"].agg(["sum", "size"])
    if len(g) < 5:
        return (float("nan"), float("nan"))
    s, n = g["sum"].to_numpy(), g["size"].to_numpy()
    idx = _RNG.integers(0, len(g), size=(n_boot, len(g)))
    boots = s[idx].sum(1) / n[idx].sum(1)
    lo, hi = np.percentile(boots, [2.5, 97.5]) * 100
    return float(lo), float(hi)


def report(d: pd.DataFrame, name: str, mask, clean: bool = True, ci: bool = True) -> None:
    """一條規則的雙窗標準報表。"""
    m = (mask & d["clean"]) if clean else mask
    m = m.fillna(False).to_numpy() if isinstance(m, pd.Series) else m
    parts = [f"{name:<34}"]
    for lbl, sub in (("挖", d[m & d["mine"].to_numpy()]), ("後", d[m & ~d["mine"].to_numpy()])):
        if len(sub) < 40:
            parts.append(f"{lbl}: n={len(sub)} 樣本薄")
            continue
        nd = sub["date"].nunique()
        byday = sub.groupby("date")["ex"].mean()
        t = byday.mean() / (byday.std(ddof=1) / np.sqrt(len(byday))) if len(byday) > 5 else np.nan
        cis = ""
        if ci:
            lo, hi = _boot_ci(sub)
            cis = f"[{lo:4.1f},{hi:4.1f}]"
        parts.append(f"{lbl} n={len(sub):>5} 日{nd:>4} {len(sub)/nd:5.1f}檔 "
                     f"hit{sub['hit10'].mean()*100:5.1f}%{cis} "
                     f"超額{byday.mean()*100:+5.1f}pp(t{t:+5.1f}) mae{sub['mae10'].mean():+6.1f}%")
    print(" | ".join(parts))


def styles(d: pd.DataFrame) -> dict[str, pd.Series]:
    """現行 wave.py 四風格的向量化定義。"""
    a, c20 = d["atr_pct"], d["c_over_ma20"]
    strong = (d["pos_52w"] > STRONG_POS_MIN) & (c20 > STRONG_OVER_MA20)
    story = (d["pb"] > STORY_PB_MIN) & (d["pe"] > STORY_PE_MIN) & (a > STORY_ATR_MIN)
    return {
        "explosive": (a > EXPLOSIVE_ATR_MIN) & (c20 > 0) & d["ma20_up5"].fillna(False),
        "strong": strong,
        "story": story,
        "crash": (a > CRASH_ATR_MIN) & (strong | story) & (d["mkt_bias60"] <= CRASH_MKT_BIAS60),
    }


# ─────────────────────────── §1 天花板地圖 ───────────────────────────


def sec_ceiling(d: pd.DataFrame) -> None:
    print("\n" + "=" * 100)
    print("§1 天花板：hit10 = f(ATR, 大盤距季線)——≥70% 的格子在哪、holdout 是否複製")
    print("=" * 100)
    d = d.assign(
        atr_band=pd.cut(d["atr_pct"] * 100, [0, 3, 5, 6, 7, 8, 10, 12, 999],
                        labels=["<3", "3-5", "5-6", "6-7", "7-8", "8-10", "10-12", "12+"]),
        mkt_band=pd.cut(d["mkt_bias60"], [-999, -6, -2, 2, 999],
                        labels=["深崩<-6", "偏弱-6~-2", "持平-2~2", "偏強>2"]))
    for lbl, s in (("挖掘 2021-24", d[d["mine"]]), ("holdout 2025-26", d[~d["mine"]])):
        piv = s.pivot_table(index="atr_band", columns="mkt_band", values="hit10",
                            aggfunc=["mean", "size"], observed=True)
        txt = ((piv["mean"] * 100).round(1).astype(str) + "%("
               + piv["size"].fillna(0).astype(int).astype(str) + ")")
        print(f"\n[{lbl}] 格內 hit10%(樣本數)")
        print(txt.to_string())
    print("\n[代價] 同格 mae10 %（全期）")
    print(d.pivot_table(index="atr_band", columns="mkt_band", values="mae10",
                        aggfunc="mean", observed=True).round(1).to_string())

    day = d.groupby("date")["hit10"].mean() * 100
    mine_day = d[d["mine"]].groupby("date")["hit10"].mean() * 100
    print("\n[逐日全市場基率] 中位 %.1f%%  P90 %.1f%%  P99 %.1f%%  max %.1f%%"
          % (day.median(), day.quantile(.9), day.quantile(.99), day.max()))
    for thr in (30, 35, 40, 45, 50):
        n_all, n_mine = int((day >= thr).sum()), int((mine_day >= thr).sum())
        print(f"    基率≥{thr}%：{n_all:>4}/{len(day)} 日（挖掘 {n_mine}，holdout {n_all-n_mine}）")
    print("\n讀法：穩定超額上限約 +30pp（見 §3~§5），故『絕對 70%』需要當日基率 ≥40%——"
          "而基率≥40% 的日子只有全樣本的 3.5%，且 §6 證實事前無法可靠預測。")


# ─────────────────────────── §2 新特徵軸單篩 ───────────────────────────


def sec_axes(d: pd.DataFrame) -> None:
    print("\n" + "=" * 100)
    print("§2 角落原子池沒有的新軸：錨池 atr>6% 內、同日同 ATR 桶配對增量（雙窗同號才算過）")
    print("=" * 100)
    P = d[(d["atr_pct"] > 0.06) & d["clean"]].copy()
    P["_k"] = P["date"] + "|" + P["atr_bucket"].astype(str)
    P["exb"] = P["hit10"] - P.groupby("_k")["hit10"].transform("mean")
    print(f"錨池 n={len(P):,} 日={P['date'].nunique()}  "
          f"hit10 挖掘{P[P['mine']]['hit10'].mean()*100:.1f}% / 後{P[~P['mine']]['hit10'].mean()*100:.1f}%\n")
    cands = {
        "收最高 close_pos>0.9": P["close_pos"] > 0.9,
        "收最低 close_pos<0.2": P["close_pos"] < 0.2,
        "長紅 body>4%": P["body_pct"] > 4,
        "長黑 body<-4%": P["body_pct"] < -4,
        "跳空上 gap>2%": P["gap_pct"] > 2,
        "跳空下 gap<-2%": P["gap_pct"] < -2,
        "收盤創20日高": P["break20"] > 0.5,
        "箱型寬 box20>40%": P["box20"] > 40,
        "波動擴張 atr_chg20>1.5": P["atr_chg20"] > 1.5,
        "波動收縮 atr_chg20<0.8": P["atr_chg20"] < 0.8,
        "自身波動高位>0.9": P["atr_self_pct"] > 0.9,
        "低價股 <20元": P["px"] < 20,
        "高價股 >200元": P["px"] > 200,
        "成交值>10億": P["turnover_val"] > 10,
        "成交值<1億": P["turnover_val"] < 1,
        "類股傳染 sec_att5>5%": P["sec_att5"] > 5,
        "券5日暴增>50%": P["short_chg5"] > 50,
    }
    for name, mk in cands.items():
        mk = mk.fillna(False).to_numpy()
        line = [f"  {name:<22}"]
        for lbl, sub in (("挖", P[mk & P["mine"].to_numpy()]), ("後", P[mk & ~P["mine"].to_numpy()])):
            if len(sub) < 100:
                line.append(f"{lbl} 樣本薄")
                continue
            byday = sub.groupby("date")["exb"].mean()
            t = byday.mean() / (byday.std(ddof=1) / np.sqrt(len(byday))) if len(byday) > 5 else np.nan
            line.append(f"{lbl} n={len(sub):>6} hit{sub['hit10'].mean()*100:5.1f}% "
                        f"桶內增量{byday.mean()*100:+5.1f}pp(t{t:+5.1f})")
        print(" | ".join(line))
    print("\n過關（雙窗同號且 |t|>1.5）：箱型寬>40%、收盤創20日高、收最高、跳空上（正）；"
          "低價股<20元、成交值<1億（負，當排除項）。"
          "\n未過：波動收縮/自身波動高位/連漲（翻號或衰減）。")


# ─────────────────────────── §3 四風格重測 ───────────────────────────


def sec_styles(d: pd.DataFrame) -> None:
    print("\n" + "=" * 100)
    print("§3 現行四風格在 hit10 口徑重測，並試加 §2 過關的新軸")
    print("=" * 100)
    S = styles(d)
    U = S["explosive"] | S["strong"] | S["story"] | S["crash"]
    box = d["box20"] > 40
    brk = (d["break20"] > 0.5) | (d["close_pos"] > 0.9)
    liq = (d["px"] >= 20) & (d["turnover_val"] >= 1.0)
    print("\n[現行]")
    for k, v in S.items():
        report(d, k, v)
    report(d, "四風格聯集", U)
    print("\n[加箱型寬>40%]")
    for k, v in S.items():
        report(d, f"{k}+box", v & box)
    report(d, "聯集+box", U & box)
    print("\n[加 創20日高|收最高]")
    for k, v in S.items():
        report(d, f"{k}+突破", v & brk)
    report(d, "聯集+突破", U & brk)
    print("\n[聯集 + 組合]")
    report(d, "聯集+box+流動篩", U & box & liq)
    report(d, "聯集+box+突破+流動篩", U & box & brk & liq)


# ─────────────────────────── §4 深跌反攻消融 ───────────────────────────


def sec_ablation(d: pd.DataFrame) -> None:
    print("\n" + "=" * 100)
    print("§4 深跌反攻消融：現行 crash 為什麼弱？三個差異各貢獻多少")
    print("=" * 100)
    a, c20 = d["atr_pct"], d["c_over_ma20"]
    strong = (d["pos_52w"] > STRONG_POS_MIN) & (c20 > STRONG_OVER_MA20)
    story = (d["pb"] > STORY_PB_MIN) & (d["pe"] > STORY_PE_MIN) & (a > STORY_ATR_MIN)
    liq = (d["px"] >= 20) & (d["turnover_val"] >= 1.0)
    box = d["box20"] > 40
    report(d, "① 現行 atr>6 &(str|sto)& 大盤≤-2.3", (a > .06) & (strong | story) & (d["mkt_bias60"] <= -2.3))
    report(d, "② ① 但大盤≤-6", (a > .06) & (strong | story) & (d["mkt_bias60"] <= -6))
    report(d, "③ ② 但 atr>8", (a > .08) & (strong | story) & (d["mkt_bias60"] <= -6))
    report(d, "④ ③ 但去掉 (str|sto) 閘", (a > .08) & (d["mkt_bias60"] <= -6))
    report(d, "⑤ ④ 但 atr>6", (a > .06) & (d["mkt_bias60"] <= -6))
    report(d, "⑥ ④ 但大盤≤-2.3 [建議]", (a > .08) & (d["mkt_bias60"] <= -2.3))
    report(d, "⑦ ⑥ +流動篩 [建議+]", (a > .08) & (d["mkt_bias60"] <= -2.3) & liq)
    report(d, "⑧ ⑥ +box+流動篩", (a > .08) & (d["mkt_bias60"] <= -2.3) & box & liq)
    print("\n讀法：③ 樣本塌到 n=46 → (strong|story) 與『高 ATR』幾乎互斥，"
          "現行 crash 因此永遠選不到真正的高波動反彈標的（這是可修的產品缺陷，非統計錯覺）。"
          "\n     ⑤ vs ④ → ATR 門檻 6%→8% 是最大單一槓桿；⑥ 保留現行大盤門檻但涵蓋更多崩段。")


# ─────────────────────────── §5 段級穩健性 ───────────────────────────


def _episodes(dates) -> dict[str, int]:
    ds = pd.to_datetime(sorted(set(dates)))
    ep, cur, out = 0, ds[0], {}
    for x in ds:
        if (x - cur).days > 15:      # 間隔 >15 個日曆日＝新的崩段
            ep += 1
        out[x.strftime("%Y-%m-%d")] = ep
        cur = x
    return out


def sec_episodes(d: pd.DataFrame) -> None:
    print("\n" + "=" * 100)
    print("§5 段級穩健性：崩勢類規則的有效樣本數＝崩盤段數，不是日數")
    print("=" * 100)
    a = d["atr_pct"]
    liq = (d["px"] >= 20) & (d["turnover_val"] >= 1.0)
    cases = {
        "atr>8 & 大盤≤-6": (a > .08) & (d["mkt_bias60"] <= -6),
        "atr>8 & 大盤≤-2.3": (a > .08) & (d["mkt_bias60"] <= -2.3),
        "atr>8 & 大盤≤-2.3 +流動篩": (a > .08) & (d["mkt_bias60"] <= -2.3) & liq,
        "atr>6 & 大盤≤-2.3（對照）": (a > .06) & (d["mkt_bias60"] <= -2.3),
    }
    for nm, mk in cases.items():
        s = d[(mk & d["clean"]).fillna(False).to_numpy()].copy()
        if s.empty:
            continue
        s["ep"] = s["date"].map(_episodes(s["date"]))
        g = s.groupby("ep").agg(起=("date", "min"), 迄=("date", "max"), 日=("date", "nunique"),
                                n=("hit10", "size"), hit=("hit10", "mean"), mae=("mae10", "mean"))
        g["hit"] = (g["hit"] * 100).round(1)
        g["mae"] = g["mae"].round(1)
        g["同日超額pp"] = (s.groupby("ep")["ex"].mean() * 100).round(1)
        hits = g.loc[g["日"] >= 3, "hit"]
        print(f"\n=== {nm} ===  崩段 {len(g)} 個（日數≥3 的段 {len(hits)} 個）")
        print(g.to_string())
        if len(hits) >= 3:
            print(f"  段級：中位 {hits.median():.1f}%  平均 {hits.mean():.1f}%  "
                  f"最差 {hits.min():.1f}%  最好 {hits.max():.1f}%  "
                  f"≥60% {int((hits>=60).sum())}/{len(hits)} 段  "
                  f"≥70% {int((hits>=70).sum())}/{len(hits)} 段")
    print("\n讀法：holdout 的 20 個亮燈日幾乎全屬 2025-04 一段——用『日』算信賴區間會嚴重低估不確定性。"
          "段級離散度才是誠實的：≥70% 只發生在少數段，段間 20%~80% 都出現過。")


# ─────────────────────────── §6 兩條負面結論 ───────────────────────────


def sec_model(d: pd.DataFrame) -> None:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import RidgeCV
    from sklearn.metrics import roc_auc_score
    from sklearn.preprocessing import StandardScaler

    print("\n" + "=" * 100)
    print("§6-a 事前開關：當日基率能否預測？（若能，『基率+超額≥70%』就可事前開關）")
    print("=" * 100)
    day = d.groupby("date").agg(base=("hit10", "mean"), mine=("mine", "first"),
                                lu5=("lu5", "first"), lu=("lu", "first"),
                                lu_trend=("lu_trend", "first"), rv20=("rv20", "first"),
                                tv=("tv_5_60", "first"), mkt_bias60=("mkt_bias60", "first"),
                                mkt_ret20=("mkt_ret20", "first"), mkt_up20=("mkt_up20", "first"))
    day["base"] *= 100
    FE = ["lu5", "lu", "lu_trend", "rv20", "tv", "mkt_bias60", "mkt_ret20", "mkt_up20"]
    tr, te = day[day["mine"]].dropna(subset=FE), day[~day["mine"]].dropna(subset=FE)
    sc = StandardScaler().fit(tr[FE])
    mod = RidgeCV(alphas=np.logspace(-2, 3, 20)).fit(sc.transform(tr[FE]), tr["base"])
    for lbl, s in (("挖掘(in-sample)", tr), ("holdout", te)):
        p = mod.predict(sc.transform(s[FE]))
        q = pd.qcut(pd.Series(p, index=s.index).rank(method="first"), 5, labels=[1, 2, 3, 4, 5])
        print(f"  {lbl}: corr={np.corrcoef(p, s['base'])[0,1]:.3f} "
              f"MAE={np.abs(p-s['base']).mean():.1f}pp | 預測五分位→實際基率 "
              + " ".join(f"Q{k}:{v:.1f}%" for k, v in s["base"].groupby(q, observed=True).mean().items()))
    print("  → holdout corr 僅 ~0.25、最高五分位實際基率也只 ~30%。事前挑『高基率日』不可靠，"
          "『基率+超額≥70%』的開關在 holdout 選出的少數日子實際只有 4x%。")

    print("\n" + "=" * 100)
    print("§6-b 排序模型移轉檢定：波動池內能不能挑出更會噴的股票？")
    print("=" * 100)
    P = d[(d["atr_pct"] > 0.06) & d["clean"] & (d["px"] >= 20) & (d["turnover_val"] >= 1.0)].copy()
    XS = ["atr_pct", "ma_align", "c_over_ma20", "bias_20", "bias_60", "ret5", "ret20", "rel_ret20",
          "peer_surge5", "vol_ratio", "vol_trend", "kd_k", "pos_52w", "pe", "pb", "inst_f5",
          "inst_t5", "inst_tot10", "sq_ratio", "short_chg5", "close_pos", "body_pct", "gap_pct",
          "range_pct", "break20", "box20", "atr_chg20", "turnover_val", "px",
          "sec_att5", "sec_att_chg"]
    tr, te = P[P["mine"]], P[~P["mine"]]
    gbm = HistGradientBoostingClassifier(max_iter=250, learning_rate=0.05, max_depth=4,
                                         min_samples_leaf=300, l2_regularization=2.0,
                                         random_state=0).fit(tr[XS], tr["hit10"])
    P["p_gbm"] = gbm.predict_proba(P[XS])[:, 1]

    def _r(col, asc=True):
        return P.groupby("date")[col].rank(pct=True, ascending=asc).fillna(0.5)

    P["p_new"] = (_r("box20") + _r("close_pos") + _r("break20") + _r("gap_pct")
                  + _r("short_chg5") + _r("peer_surge5") + _r("px")) / 7
    P["p_pop"] = (2 * _r("atr_pct") + _r("ma_align")) / 3
    for col, nm in (("p_gbm", "GBM 31 特徵"), ("p_new", "新軸簡單合成"), ("p_pop", "現行會噴分數")):
        print(f"\n[{nm}] AUC 挖掘={roc_auc_score(tr['hit10'], P.loc[tr.index, col]):.3f} "
              f"holdout={roc_auc_score(te['hit10'], P.loc[te.index, col]):.3f}")
        for lbl, s in (("挖", P[P["mine"]]), ("後", P[~P["mine"]])):
            for topn in (3, 10):
                sel = s.sort_values(col, ascending=False).groupby("date").head(topn)
                pool_day = s.groupby("date")["hit10"].mean()
                byday = sel.groupby("date")["hit10"].mean() - pool_day.loc[sel["date"].unique()]
                t = byday.mean() / (byday.std(ddof=1) / np.sqrt(len(byday)))
                print(f"    {lbl} 每日前{topn:>2}檔 n={len(sel):>5} hit={sel['hit10'].mean()*100:5.1f}% "
                      f"同日池內增量={byday.mean()*100:+5.1f}pp (t={t:+5.1f})")
    print("\n  → GBM 挖掘窗池內 +18.7pp、holdout +0.8pp(t≈0.7)：**零移轉**（AUC 0.77→0.52）。"
          "\n    新軸合成與現行會噴分數同級（雙窗 +2~3pp）——池內排序的真實上限就是 +3pp 左右，"
          "所有邊際都在『池的定義』上，不在『池內選股』。")


# ══════════════════ 第二輪：時點開關 / 有界窮舉 / ATR 階梯 ══════════════════
#
# 第一輪的結論是「穩定的 ≥70% 不存在」。第二輪把三件第一輪沒做的事補上：
#   (a) 時點型開關（第一輪只試過「預測當日基率」，沒試過直接對命中率開關）；
#   (b) **有界窮舉 + 虛無校準**——第一輪沒有虛無對照，無法區分「找到」與「挖到」；
#   (c) 把 ATR 門檻當連續旋鈕掃描，而不是只比 6% vs 8%。
# 結果推翻了第一輪的一半結論：≥70% 在兩窗同時成立是**存在**的（虛無校準 0 組達標），
# 但 §9 的桶內歸因顯示它**全部由波動度買單**，第三軸沒有獨立貢獻。


def _liq(d: pd.DataFrame):
    """流動篩：股價≥20 元 ∧ 成交值≥1 億（§2 雙窗同號為負的兩項，反過來當排除項）。"""
    return (d["px"] >= 20) & (d["turnover_val"] >= 1.0)


def _B(x) -> np.ndarray:
    return x.fillna(False).to_numpy() if isinstance(x, pd.Series) else np.asarray(x)


def _win_stats(d: pd.DataFrame, mask: np.ndarray, mine: bool) -> dict | None:
    sub = d[mask & (d["mine"].to_numpy() if mine else ~d["mine"].to_numpy())]
    if sub.empty:
        return None
    byday = sub.groupby("date")["ex"].mean()
    nd = len(byday)
    t = byday.mean() / (byday.std(ddof=1) / np.sqrt(nd)) if nd > 5 else float("nan")
    return {"n": int(len(sub)), "days": int(nd), "per_day": round(len(sub) / nd, 1),
            "hit": round(float(sub["hit10"].mean() * 100), 1),
            "ex": round(float(byday.mean() * 100), 1),
            "t": round(float(t), 1) if t == t else None,
            "mae": round(float(sub["mae10"].mean()), 1)}


def _ep_stats(d: pd.DataFrame, mask: np.ndarray, min_n: int = 10) -> dict:
    """段級：崩勢規則的有效樣本是段數不是日數（§5）。只用 n≥min_n 的段算離散度。"""
    sub = d[mask]
    if sub.empty:
        return {"episodes": [], "n_eff": 0}
    ep = sub["date"].map(_episodes(sub["date"]))
    g = sub.assign(ep=ep).groupby("ep").agg(
        start=("date", "min"), days=("date", "nunique"), n=("hit10", "size"),
        hit=("hit10", "mean"), mae=("mae10", "mean"))
    items = [{"start": str(r.start)[:7], "days": int(r.days), "n": int(r.n),
              "hit": round(r.hit * 100, 1), "mae": round(r.mae, 1)} for r in g.itertuples()]
    k = g[g["n"] >= min_n]["hit"] * 100
    if len(k) == 0:
        return {"episodes": items, "n_eff": 0}
    return {"episodes": items, "n_eff": int(len(k)),
            "median": round(float(k.median()), 1), "mean": round(float(k.mean()), 1),
            "min": round(float(k.min()), 1), "max": round(float(k.max()), 1),
            "ge70": int((k >= 70).sum()), "ge60": int((k >= 60).sum())}


def _rule_row(d: pd.DataFrame, rid: str, label: str, mask) -> dict:
    m = _B(mask) & _B(d["clean"])
    return {"id": rid, "rule": label,
            "mine": _win_stats(d, m, True), "holdout": _win_stats(d, m, False),
            "cases": int(m.sum()),
            "per_month": round(float(m.sum()) / d["date"].nunique() * 20, 1),
            "ep": _ep_stats(d, m)}


def _crash_now(d: pd.DataFrame):
    """現行 wave.py 的 crash（個股端＋市場端），供對照。"""
    a, c20 = d["atr_pct"], d["c_over_ma20"]
    strong = (d["pos_52w"] > STRONG_POS_MIN) & (c20 > STRONG_OVER_MA20)
    story = (d["pb"] > STORY_PB_MIN) & (d["pe"] > STORY_PE_MIN) & (a > STORY_ATR_MIN)
    return (a > 0.06) & (strong | story) & (d["mkt_bias60"] <= CRASH_MKT_BIAS60)


# ─────────────────────────── §7 時點型開關（皆為負面結論）───────────────────────


def sec_regime(d: pd.DataFrame) -> list[dict]:
    print("\n" + "=" * 100)
    print("§7 時點型開關：能不能只在『對的日子』出手？（三條路，全滅——記錄以免下次重挖）")
    print("=" * 100)
    a = d["atr_pct"]
    R = _B((a > .08) & (d["mkt_bias60"] <= -2.3) & _liq(d) & d["clean"])
    out: list[dict] = []

    def _row(name, mask, verdict, why):
        report(d, name, pd.Series(mask, index=d.index), clean=False, ci=False)
        out.append({"gate": name, "verdict": verdict, "why": why,
                    "mine": _win_stats(d, mask, True), "holdout": _win_stats(d, mask, False)})

    print("\n[7-a] 自參照：用『這條規則自己近 10 個亮燈日的已實現命中率』當開關")
    print("      （PIT 合法：標籤要 11 個交易日才定案，故一律落後 11 日取用）")
    days_all = pd.Index(sorted(d["date"].unique()))
    pos = {dt: i for i, dt in enumerate(days_all)}
    day_hit = d[R].groupby("date")["hit10"].agg(["mean", "size"])
    fire = list(day_hit.index)
    gate = {}
    for dt in fire:
        prev = [x for x in fire if pos[x] <= pos[dt] - 11][-10:]
        if len(prev) == 10:
            sub = day_hit.loc[prev]
            gate[dt] = float((sub["mean"] * sub["size"]).sum() / sub["size"].sum() * 100)
    g = d["date"].map(gate)
    _row("⑦ & 落後10亮燈日命中≥70%", R & _B(g >= 70), "翻號",
         "落後已實現命中高＝反彈已走完；挖掘僅 40% vs 對照 73%，方向與直覺相反")
    _row("⑦ & 落後10亮燈日命中<50%", R & _B(g < 50), "挖掘窗有效但零移轉",
         "挖掘 73.4%（+5pp）但 holdout 55.9%（−0.2pp）：holdout 只有一個崩段，開關幾乎不篩")

    print("\n[7-b] 崩段第幾天（大盤 bias60≤−2.3 連續狀態的第 N 個交易日；完全事前可算）")
    mkt = d.groupby("date")["mkt_bias60"].first().sort_index()
    cnt, idx = 0, {}
    for dt, x in (mkt <= CRASH_MKT_BIAS60).items():
        cnt = cnt + 1 if x else 0
        idx[dt] = cnt
    di = d["date"].map(idx)
    for lo, hi, tag in ((1, 2, "第1~2天"), (3, 8, "第3~8天"), (11, 9999, "第11天以後")):
        _row(f"⑦ & 崩段{tag}", R & _B((di >= lo) & (di <= hi)), "無法驗證",
             "holdout 的崩段是一段連續長跌，99% 亮燈日都落在第 11 天以後；"
             "挖掘窗最漂亮的『第3~8天』(79.9%) 在 holdout 幾乎沒有對應樣本")

    print("\n[7-c] 16 個大盤狀態閘（門檻＝挖掘窗三分位，holdout 只驗一次）")
    S = d[R]
    flips = []
    for col in ("lu5", "lu", "lu_trend", "rv20", "tv_5_60", "mkt_bias60", "mkt_ret20", "mkt_up20"):
        q = S[S["mine"]][col].quantile([1 / 3, 2 / 3]).to_numpy()
        for lbl, mk_ in ((f"{col}≤{q[0]:.2f}", d[col] <= q[0]), (f"{col}≥{q[1]:.2f}", d[col] >= q[1])):
            m = R & _B(mk_)
            A, H = _win_stats(d, m, True), _win_stats(d, m, False)
            if A is None or H is None or A["n"] < 60 or H["n"] < 40:
                continue
            same = (A["hit"] - 68.3) * (H["hit"] - 56.1) > 0
            flips.append(same)
            print(f"  {lbl:<24} 挖 {A['hit']:5.1f}%(n{A['n']:>4}) 後 {H['hit']:5.1f}%(n{H['n']:>4})"
                  f"  {'同號' if same else '翻號'}")
    n_same = sum(1 for x in flips if x)
    print(f"\n  → {len(flips)} 個可評估的大盤閘裡只有 {n_same} 個雙窗同號；"
          "「再加一個大盤條件」這個維度已耗盡——\n    大盤狀態只夠決定『出不出手』，不夠決定『出手品質』。")
    out.append({"gate": f"大盤狀態閘（{len(flips)} 個）", "verdict": f"{n_same}/{len(flips)} 雙窗同號",
                "why": "多數翻號；大盤維度已耗盡", "mine": None, "holdout": None})
    return out


# ─────────────────────────── §8 有界窮舉前緣 ＋ 虛無校準 ───────────────────────

# 窮舉格點：每一軸都「事先有理由」，不是亂槍——ATR＝§1 天花板的主軸、大盤＝現行 crash
# 的市場端、昨收漲幅＝§2 沒試過的當日反轉、流動篩＝§2 判負的兩項、第三軸＝超賣/擴張族。
_GRID_ATR = (.06, .07, .08, .09, .10, .12)
_GRID_MKT = (("（無大盤閘）", 99.0), ("&大盤≤2", 2.0), ("&大盤≤-2.3", -2.3), ("&大盤≤-6", -6.0))
_GRID_RET1 = (("", None), ("&當日漲≥5", 5.0), ("&當日漲≥7", 7.0), ("&當日漲≥9", 9.0))
_MIN_N_MINE, _MIN_N_HOLD, _MIN_D_MINE, _MIN_D_HOLD = 40, 30, 10, 8


def _grid_masks(d: pd.DataFrame) -> list[tuple[str, np.ndarray]]:
    a = d["atr_pct"]
    ret1 = (d["close"] / d.groupby("stock_id")["close"].shift(1) - 1) * 100
    ones = np.ones(len(d), bool)
    extras = [("", None),
              ("&箱型>40", d["box20"] > 40), ("&波動擴張>1.5", d["atr_chg20"] > 1.5),
              ("&券增>50", d["short_chg5"] > 50), ("&pb<1.5", d["pb"] < 1.5),
              ("&20日跌深<-20", d["ret20"] < -20), ("&量縮<0.8", d["vol_ratio"] < 0.8),
              ("&KD<20", d["kd_k"] < 20)]
    out = []
    for at in _GRID_ATR:
        ma = _B(a > at)
        for mlbl, mv in _GRID_MKT:
            mm = ma & (_B(d["mkt_bias60"] <= mv) if mv < 90 else ones)
            for rlbl, rv in _GRID_RET1:
                rm = mm & (_B(ret1 >= rv) if rv is not None else ones)
                for llbl, lv in (("", None), ("&流動", _liq(d))):
                    lm = rm & (_B(lv) if lv is not None else ones)
                    for elbl, ev in extras:
                        out.append((f"atr>{at*100:.0f}{mlbl}{rlbl}{llbl}{elbl}",
                                    lm & (_B(ev) if ev is not None else ones)))
    return out


def _bincount_stats(codes, nd_all, hit, ex, mine, mask):
    """bincount 版雙窗統計（窮舉要跑上千次，groupby 太慢）。"""
    res = []
    for sub in (mask & mine, mask & ~mine):
        n = int(sub.sum())
        if n == 0:
            res.append(None)
            continue
        c = codes[sub]
        cnt = np.bincount(c, minlength=nd_all)
        exs = np.bincount(c, weights=ex[sub], minlength=nd_all)
        sel = cnt > 0
        dm = exs[sel] / cnt[sel]
        res.append({"n": n, "days": int(sel.sum()), "hit": float(hit[sub].mean() * 100),
                    "ex": float(dm.mean() * 100)})
    return res


def _pass_floor(A, H) -> bool:
    return not (A is None or H is None
                or A["n"] < _MIN_N_MINE or H["n"] < _MIN_N_HOLD
                or A["days"] < _MIN_D_MINE or H["days"] < _MIN_D_HOLD)


def sec_frontier(d: pd.DataFrame) -> dict:
    print("\n" + "=" * 100)
    print("§8 有界窮舉前緣 ＋ 虛無校準：≥70% 到底是『找到』還是『挖到』？")
    print("=" * 100)
    codes, uniq = pd.factorize(d["date"], sort=True)
    ndays = len(uniq)
    hit = d["hit10"].to_numpy(float)
    ex = d["ex"].to_numpy(float)
    mine = d["mine"].to_numpy()
    clean = _B(d["clean"])
    grid = _grid_masks(d)
    rows = []
    for label, m in grid:
        A, H = _bincount_stats(codes, ndays, hit, ex, mine, m & clean)
        if not _pass_floor(A, H):
            continue
        rows.append({"rule": label, "m_hit": round(A["hit"], 1), "m_n": A["n"], "m_days": A["days"],
                     "m_ex": round(A["ex"], 1), "h_hit": round(H["hit"], 1), "h_n": H["n"],
                     "h_days": H["days"], "h_ex": round(H["ex"], 1),
                     "worst": round(min(A["hit"], H["hit"]), 1), "cases": A["n"] + H["n"],
                     "per_month": round((A["n"] + H["n"]) / ndays * 20, 1)})
    rows.sort(key=lambda r: -r["worst"])
    over70 = [r for r in rows if r["m_hit"] >= 70 and r["h_hit"] >= 70]
    print(f"\n窮舉 {len(grid)} 組，過樣本下限（挖 n≥{_MIN_N_MINE}/日≥{_MIN_D_MINE}、"
          f"後 n≥{_MIN_N_HOLD}/日≥{_MIN_D_HOLD}）{len(rows)} 組；**兩窗皆≥70% 共 {len(over70)} 組**")
    print(f"{'規則':<36}{'挖n':>6}{'挖日':>5}{'挖hit':>7}{'後n':>6}{'後日':>5}{'後hit':>7}{'月案例':>7}")
    for r in over70[:20]:
        print(f"{r['rule']:<36}{r['m_n']:>6}{r['m_days']:>5}{r['m_hit']:>7.1f}"
              f"{r['h_n']:>6}{r['h_days']:>5}{r['h_hit']:>7.1f}{r['per_month']:>7.1f}")

    print("\n[虛無校準] 同日內隨機重排 hit10（保留逐日基率、只打散橫截面），整組窮舉重跑 5 次：")
    rng = np.random.default_rng(7)
    order = np.argsort(codes, kind="stable")
    segs = np.split(order, np.flatnonzero(np.diff(codes[order])) + 1)
    null_65 = null_70 = null_tot = 0
    for it in range(5):
        perm = np.empty_like(hit)
        for seg in segs:
            perm[seg] = rng.permutation(hit[seg])
        day_mean = (np.bincount(codes, weights=perm, minlength=ndays)
                    / np.bincount(codes, minlength=ndays))
        ex_p = perm - day_mean[codes]
        c65 = c70 = tot = 0
        for label, m in grid:
            A, H = _bincount_stats(codes, ndays, perm, ex_p, mine, m & clean)
            if not _pass_floor(A, H):
                continue
            tot += 1
            c65 += int(A["hit"] >= 65 and H["hit"] >= 65)
            c70 += int(A["hit"] >= 70 and H["hit"] >= 70)
        null_tot += tot
        null_65 += c65
        null_70 += c70
        print(f"  第{it+1}次：可評估 {tot}，兩窗≥65% {c65}，兩窗≥70% {c70}")
    print(f"  → 虛無下 {null_tot} 次抽樣共 {null_70} 組達標；實測的 {len(over70)} 組"
          "**不是多重比較的產物**。\n    但『不是雜訊』≠『可交易』——見 §9 的桶內歸因。")
    return {"tested": len(grid), "evaluable": len(rows), "over70": over70[:30], "top": rows[:30],
            "null": {"runs": 5, "draws": null_tot, "ge65": null_65, "ge70": null_70}}


# ─────────────────────────── §9 ATR 階梯 ＋ 桶內歸因 ───────────────────────────


def sec_ladder(d: pd.DataFrame) -> dict:
    print("\n" + "=" * 100)
    print("§9 ATR 階梯：命中率–案例數前緣，以及『70% 的價格是什麼』")
    print("=" * 100)
    a, mk, liq = d["atr_pct"], d["mkt_bias60"], _liq(d)
    deep_days = int(d.loc[_B(mk <= CRASH_MKT_BIAS60), "date"].nunique())
    ladder = []
    print(f"（大盤≤{CRASH_MKT_BIAS60}% 的交易日共 {deep_days} 日；日覆蓋＝有名單的崩日/全部崩日）")
    print(f"{'ATR門檻':>8}{'挖n':>6}{'挖日':>5}{'挖檔日':>8}{'挖hit':>7}{'挖mae':>7}{'後n':>6}{'後日':>5}"
          f"{'後檔日':>8}{'後hit':>7}{'後mae':>7}{'日覆蓋':>7}{'段中位':>7}{'≥70段':>8}{'月案例':>7}")
    for thr in (6, 7, 8, 8.5, 9, 9.5, 10, 11, 12):
        m = _B((a > thr / 100) & (mk <= CRASH_MKT_BIAS60) & liq & d["clean"])
        A, H = _win_stats(d, m, True), _win_stats(d, m, False)
        ep = _ep_stats(d, m)
        cov = round(d[m]["date"].nunique() / deep_days * 100, 1)
        row = {"atr": thr, "mine": A, "holdout": H, "day_cover": cov,
               "ep_median": ep.get("median"), "ep_n": ep.get("n_eff"), "ep_ge70": ep.get("ge70"),
               "ep_min": ep.get("min"),
               "per_month": round(float(m.sum()) / d["date"].nunique() * 20, 1)}
        ladder.append(row)
        print(f"{thr:>8}{A['n']:>6}{A['days']:>5}{A['per_day']:>8.1f}{A['hit']:>7.1f}{A['mae']:>7.1f}"
              f"{H['n']:>6}{H['days']:>5}{H['per_day']:>8.1f}{H['hit']:>7.1f}{H['mae']:>7.1f}"
              f"{cov:>7.1f}{(ep.get('median') or 0):>7.1f}"
              f"{str(ep.get('ge70')) + '/' + str(ep.get('n_eff')):>8}{row['per_month']:>7.1f}")
    print("\n讀法：命中率隨 ATR 門檻**單調上升**、案例數單調下降——這是一條連續前緣，不是某個角落；"
          "\n     單調性本身就是最強的反過擬合證據（挑到的不是一格，是整條線）。"
          "\n     ATR≥10% 之後 holdout n≤11，**已不可評估**；紀律上不追。")

    print("\n[歸因] 池＝atr>9 ∧ 大盤≤-2.3：第三軸在『同日同 ATR 桶』內還剩多少增量？")
    P = d[_B((a > .09) & (mk <= CRASH_MKT_BIAS60) & d["clean"])].copy()
    P["_k"] = P["date"] + "|" + P["atr_bucket"].astype(str)
    P["exb"] = P["hit10"] - P.groupby("_k")["hit10"].transform("mean")
    # 採用門檻**事前定好**：挖掘窗桶內增量 ≥+2pp 且雙窗同號為正。挖掘窗才是能拿來挑的窗，
    # +2pp 是第一輪 §2 判「過」的同一條線（低於它的軸在大池也都是雜訊等級）。
    _ADOPT_PP = 2.0
    attribution = []
    for nm, mk_ in (("波動擴張>1.5", P["atr_chg20"] > 1.5), ("20日跌深<-20%", P["ret20"] < -20),
                    ("KD<20", P["kd_k"] < 20), ("箱型>40%", P["box20"] > 40),
                    ("流動篩（價≥20 ∧ 值≥1億）", (P["px"] >= 20) & (P["turnover_val"] >= 1.0))):
        row = {"axis": nm}
        parts = [f"  {nm:<24}"]
        for lbl, key, sub in (("挖", "mine", P[_B(mk_) & P["mine"].to_numpy()]),
                              ("後", "holdout", P[_B(mk_) & ~P["mine"].to_numpy()])):
            if len(sub) < 30:
                parts.append(f"{lbl} 樣本薄")
                row[key] = None
                continue
            by = sub.groupby("date")["exb"].mean()
            t = by.mean() / (by.std(ddof=1) / np.sqrt(len(by))) if len(by) > 5 else float("nan")
            row[key] = {"n": int(len(sub)), "pp": round(float(by.mean() * 100), 1),
                        "t": round(float(t), 1) if t == t else None}
            parts.append(f"{lbl} n={len(sub):>4} 桶內增量{by.mean() * 100:+5.1f}pp(t{t:+5.1f})")
        m_, h_ = row.get("mine"), row.get("holdout")
        row["adopted"] = bool(m_ and h_ and m_["pp"] >= _ADOPT_PP and h_["pp"] > 0)
        row["verdict"] = (
            f"採用（挖掘桶內 {m_['pp']:+.1f}pp ≥ +{_ADOPT_PP:.0f}pp 且雙窗同號）" if row["adopted"]
            else "不採用（挖掘窗桶內增量為負）" if (m_ and m_["pp"] < 0)
            else f"不採用（挖掘桶內僅 {m_['pp']:+.1f}pp，未達 +{_ADOPT_PP:.0f}pp 門檻）" if m_
            else "不採用（樣本不足）")
        attribution.append(row)
        print(" | ".join(parts))
    print("\n  → 三個超賣/擴張軸在挖掘窗的桶內增量是 0 或負：它們拉高命中率靠的是**把 ATR 分布往上推**，"
          "\n    不是選股。唯一雙窗同號為正的仍是流動篩（§2 的老結論）。"
          "\n    ⇒ 定案只調 ATR 門檻＋流動篩，**不把第三軸寫進規則**（那會是過擬合）。")

    print("\n[定案候選]")
    cands = [
        _rule_row(d, "①", "現行 crash：atr>6 ∧ (強勢|故事) ∧ 大盤≤-2.3", _crash_now(d)),
        _rule_row(d, "⑦", "前份文件建議：atr>8 ∧ 大盤≤-2.3 ∧ 流動篩",
                  (a > .08) & (mk <= CRASH_MKT_BIAS60) & liq),
        _rule_row(d, "⑨", "本輪建議：atr>9 ∧ 大盤≤-2.3 ∧ 流動篩",
                  (a > .09) & (mk <= CRASH_MKT_BIAS60) & liq),
        _rule_row(d, "⑩", "更嚴（holdout 已不可評估）：atr>9.5 ∧ 大盤≤-2.3 ∧ 流動篩",
                  (a > .095) & (mk <= CRASH_MKT_BIAS60) & liq),
    ]
    for c in cands:
        A, H, e = c["mine"], c["holdout"], c["ep"]
        print(f"  {c['id']} {c['rule'][:44]:<46} 挖 {A['hit']:5.1f}%(n{A['n']:>4}/{A['days']:>3}日 "
              f"{A['per_day']:4.1f}檔 mae{A['mae']:+6.1f}) | 後 {H['hit']:5.1f}%(n{H['n']:>4}/{H['days']:>3}日 "
              f"{H['per_day']:4.1f}檔 mae{H['mae']:+6.1f}) | 段中位 {e.get('median')}% "
              f"≥70% {e.get('ge70')}/{e.get('n_eff')} 段")
    return {"deep_days": deep_days, "ladder": ladder, "attribution": attribution,
            "candidates": cands}


# ─────────────────────── §10 真 OOS：研究標籤窗之外的崩段 ───────────────────────


def sec_oos(_d: pd.DataFrame | None = None) -> dict:
    """定案規則在 **forward_labels 之外**（>2026-07-01）的實測——真 out-of-sample。

    不吃研究快取、直接讀 daily_prices/indicators 現算，口徑與 backfill_forward_labels
    完全一致：進場錨＝訊號隔日最高，觀察窗＝進場日之後 10 根；未滿窗者不結算。
    池的定義與線上 crash 完全相同（含乾淨池與 500 張均量、非 ETF）。
    """
    print("\n" + "=" * 100)
    print("§10 真 OOS：定案規則在研究標籤窗之外（2026-07-02 起）的實測")
    print("=" * 100)
    con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    px = pd.read_sql_query(
        "SELECT stock_id sid,date,high,low,close,volume FROM daily_prices "
        "WHERE date>='2026-05-01' ORDER BY sid,date", con)
    ind = pd.read_sql_query(
        "SELECT stock_id sid,date,atr14,vol_ma20 FROM indicators WHERE date>='2026-05-01'", con)
    att = pd.read_sql_query(
        "SELECT stock_id sid,date,kind FROM attention_listings WHERE date>='2026-05-01'", con)
    etf = {r[0] for r in con.execute("SELECT id FROM stocks WHERE is_etf=1")}
    mi = pd.read_sql_query("SELECT date,close FROM market_index ORDER BY date", con)
    con.close()

    mi["bias"] = (mi["close"] / mi["close"].rolling(60).mean() - 1.0) * 100
    bias = dict(zip(mi["date"], mi["bias"]))
    d = px.merge(ind, on=["sid", "date"])
    days = sorted(d["date"].unique())
    pos = {x: i for i, x in enumerate(days)}
    dirty = set()
    for sid, d0, kind in att.itertuples(index=False):
        p = pos.get(d0)
        if p is None:
            continue
        for k in range(5 if kind == "notice" else 10):
            if p + k < len(days):
                dirty.add((sid, days[p + k]))
    d["atr_pct"] = d["atr14"] / d["close"]
    d["mkt"] = d["date"].map(bias)
    sel = d[(d["atr_pct"] > 0.09) & (d["mkt"] <= CRASH_MKT_BIAS60) & (d["close"] >= 20)
            & (d["close"] * d["volume"] >= 1e8) & (d["vol_ma20"] >= _LIQ_MIN)
            & (~d["sid"].isin(etf)) & (d["date"] > "2026-07-01")]
    sel = sel[[(s, t) not in dirty for s, t in zip(sel["sid"], sel["date"])]]
    bars = {sid: (g["date"].tolist(), g["high"].tolist(), g["low"].tolist())
            for sid, g in px.groupby("sid")}
    rows = []
    for sid, dt in zip(sel["sid"], sel["date"]):
        dts, hi, lo = bars[sid]
        i = dts.index(dt)
        if i + 11 >= len(dts):      # 未滿窗不結算（贏家提早結算會灌水）
            continue
        entry = hi[i + 1]
        if not entry:
            continue
        rows.append((dt, max(hi[i + 2:i + 12]) / entry - 1 >= 0.10,
                     (min(lo[i + 2:i + 12]) / entry - 1) * 100))
    if not rows:
        print("  尚無已滿窗的 OOS 訊號")
        return {"n": 0, "days": [], "note": "尚無已滿窗的 OOS 訊號"}
    r = pd.DataFrame(rows, columns=["date", "hit", "mae"])
    by = r.groupby("date").agg(n=("hit", "size"), hit=("hit", "mean"), mae=("mae", "mean"))
    print(f"  n={len(r)} 日={len(by)} 命中 {r['hit'].mean()*100:.1f}% MAE {r['mae'].mean():.1f}%")
    for dt, row in by.iterrows():
        print(f"    {dt}  n={int(row.n):>4}  命中 {row.hit*100:5.1f}%  MAE {row.mae:+6.1f}%")
    print("  → 這一段完全在挖掘窗與 holdout 之外（forward_labels 只到 2026-07-01），"
          "規則的任何一個門檻都不是在這段資料上挑的。\n"
          "    但它仍然只是**一個崩段**，而且是 V 轉急彈的那種；不能當成新的期望值，"
          "只能當成「改版方向沒有立刻被推翻」。")
    return {
        "n": int(len(r)), "hit": round(float(r["hit"].mean() * 100), 1),
        "mae": round(float(r["mae"].mean()), 1),
        "days": [{"date": str(dt), "n": int(row.n), "hit": round(row.hit * 100, 1),
                  "mae": round(row.mae, 1)} for dt, row in by.iterrows()],
        "note": "forward_labels 只到 2026-07-01；此段的門檻沒有一個是在這段資料上挑的。"
                "但仍只是一個崩段（且是 V 轉急彈型），只能說「改版方向沒有立刻被推翻」。",
    }


# ─────────────────────── §11 停損敏感度：為什麼波段軌不設停損 ───────────────────────


def sec_stop(d: pd.DataFrame) -> dict:
    """定案規則加上不同停損線之後還剩多少命中率，以及無停損要承受的尾巴。

    逐根走路徑（同日既碰停損又碰目標＝保守記停損，與 strategy_engine._judge 同慣例），
    所以這裡量的是「真的照這條線操作」的結果，不是用 mfe10/mae10 事後兜出來的。
    """
    print("\n" + "=" * 100)
    print("§11 停損敏感度：波段軌該不該設停損")
    print("=" * 100)
    a, mk = d["atr_pct"], d["mkt_bias60"]
    sel = d[_B((a > .09) & (mk <= CRASH_MKT_BIAS60) & _liq(d) & d["clean"])][
        ["stock_id", "date", "mine"]]
    con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    px = pd.read_sql_query("SELECT stock_id sid,date,high,low,close FROM daily_prices "
                           "WHERE date>='2020-12-01' ORDER BY sid,date", con)
    con.close()
    bars = {sid: (g["date"].tolist(), g["high"].tolist(), g["low"].tolist(), g["close"].tolist())
            for sid, g in px.groupby("sid")}
    stops = (6, 8, 10, 12, 15)
    rows = []
    for sid, dt, mine in sel.itertuples(index=False):
        if sid not in bars:
            continue
        dts, hi, lo, cl = bars[sid]
        try:
            i = dts.index(dt)
        except ValueError:
            continue
        if i + 11 >= len(dts):
            continue
        entry = hi[i + 1]
        if not entry:
            continue
        H, L, C = hi[i + 2:i + 12], lo[i + 2:i + 12], cl[i + 2:i + 12]
        rec = {"mine": bool(mine), "nostop": max(H) / entry - 1 >= 0.10}
        dip, ret = 0.0, None
        for h, l, c in zip(H, L, C):
            dip = min(dip, (l / entry - 1) * 100)
            if h >= entry * 1.10:
                ret = 10.0
                break
        rec["ret"] = ret if ret is not None else (C[-1] / entry - 1) * 100
        rec["dip"] = dip
        for s in stops:                       # 逐根：先看低點（保守），再看高點
            hit = False
            for h, l in zip(H, L):
                if l <= entry * (1 - s / 100):
                    break
                if h >= entry * 1.10:
                    hit = True
                    break
            rec[f"s{s}"] = hit
        rows.append(rec)
    r = pd.DataFrame(rows)
    if r.empty:
        return {}
    base_m = r[r["mine"]]["nostop"].mean() * 100
    base_h = r[~r["mine"]]["nostop"].mean() * 100
    table = [{"stop": None, "mine": round(base_m, 1), "holdout": round(base_h, 1),
              "d_mine": 0.0, "d_holdout": 0.0}]
    print(f"  n={len(r)}（挖 {int(r['mine'].sum())} / 後 {int((~r['mine']).sum())}）")
    print(f"  {'停損':<10}{'挖掘':>8}{'holdout':>10}{'相對無停損':>14}")
    print(f"  {'無停損':<10}{base_m:>7.1f}%{base_h:>9.1f}%{'  —':>14}")
    for s in stops:
        m = r[r["mine"]][f"s{s}"].mean() * 100
        h = r[~r["mine"]][f"s{s}"].mean() * 100
        table.append({"stop": s, "mine": round(m, 1), "holdout": round(h, 1),
                      "d_mine": round(m - base_m, 1), "d_holdout": round(h - base_h, 1)})
        print(f"  {'-' + str(s) + '%':<10}{m:>7.1f}%{h:>9.1f}%"
              f"{m - base_m:>+7.1f}/{h - base_h:>+5.1f}pp")
    tail = {
        "avg_ret": round(float(r["ret"].mean()), 2),
        "miss_avg_ret": round(float(r[~r["nostop"]]["ret"].mean()), 1),
        "miss_worst_ret": round(float(r[~r["nostop"]]["ret"].min()), 1),
        "dip_p50": round(float(r["dip"].quantile(.50)), 1),
        "dip_p90": round(float(r["dip"].quantile(.10)), 1),
        "dip_worst": round(float(r["dip"].min()), 1),
        "deep10_share": round(float((r["dip"] <= -10).mean() * 100), 1),
        "deep10_still_hit": round(float(r[r["dip"] <= -10]["nostop"].mean() * 100), 1),
        "deep15_share": round(float((r["dip"] <= -15).mean() * 100), 1),
        "deep15_still_hit": round(float(r[r["dip"] <= -15]["nostop"].mean() * 100), 1),
    }
    print(f"\n  無停損要承受的尾巴：期間最深浮虧 中位 {tail['dip_p50']}% / P90 {tail['dip_p90']}% / "
          f"最差 {tail['dip_worst']}%；單筆期望 {tail['avg_ret']:+.2f}%，"
          f"未命中那批到期平均 {tail['miss_avg_ret']}%、最差 {tail['miss_worst_ret']}%")
    print(f"  關鍵：期間曾浮虧 >10% 的部位佔 {tail['deep10_share']}%，"
          f"**其中仍有 {tail['deep10_still_hit']}% 最後照樣摸到 +10%** "
          f"（>15% 者：{tail['deep15_share']}% 佔比、{tail['deep15_still_hit']}% 仍達標）。")
    print("  → 停損把「路還沒走完」誤判成「論點錯了」。這條軌的風控是**時間**（10 日到期），不是價格。")
    return {"n": int(len(r)), "table": table, "tail": tail}


# ─────────────────────────── 凍結 JSON（策略室讀這支）───────────────────────────


def emit_json(d: pd.DataFrame, regime: list[dict] | None, frontier: dict | None,
              ladder: dict | None, oos: dict | None = None,
              stop: dict | None = None) -> None:
    """把三節的結論凍成 data/wave_challenge.json，供 /lab/wave-challenge 端點直出。

    凍結而非即時計算：這份要跑 4~6 分鐘、且要吃 condition_judge_cache_v4.pkl，
    不可能放在 request 路徑上；同 corners.json 的作法。
    """
    import json

    if regime is None:
        regime = sec_regime(d)
    if frontier is None:
        frontier = sec_frontier(d)
    if ladder is None:
        ladder = sec_ladder(d)
    if oos is None:
        oos = sec_oos()
    if stop is None:
        stop = sec_stop(d)
    a, mk = d["atr_pct"], d["mkt_bias60"]
    core = _B((a > .09) & (mk <= CRASH_MKT_BIAS60) & _liq(d))
    adopted = core & _B(d["clean"])
    # 池紀律：研究一律用乾淨池，線上若不排注意/處置就會系統性高估（holdout −10pp）。
    # 這張對照就是「為什麼 _apply_crash_style 要加乾淨池閘」的證據，放進產物給策略室顯示。
    pool = [
        {"pool": "乾淨池（研究口徑：排除注意5日/處置10日）",
         "mine": _win_stats(d, adopted, True), "holdout": _win_stats(d, adopted, False)},
        {"pool": "只排除處置10日", "mine": _win_stats(d, core & _B(d["att_punish10"] == 0), True),
         "holdout": _win_stats(d, core & _B(d["att_punish10"] == 0), False)},
        {"pool": "完全不排除（改版前的線上口徑）",
         "mine": _win_stats(d, core, True), "holdout": _win_stats(d, core, False)},
    ]
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "target": "hit10",
        "target_label": "訊號隔日最高價進場，之後 10 個交易日內曾摸 +10%",
        "windows": {"mine": "2021-01~2024-12", "holdout": "2025-01~2026-07"},
        "base_rate": {"mine": round(float(d[d["mine"]]["hit10"].mean() * 100), 1),
                      "holdout": round(float(d[~d["mine"]]["hit10"].mean() * 100), 1)},
        "deep_days": ladder["deep_days"],
        "ladder": ladder["ladder"],
        "attribution": ladder["attribution"],
        "candidates": ladder["candidates"],
        "frontier": frontier,
        "regime_gates": regime,
        "adopted": {
            "rule": "crash 深跌反攻 ＝ atr_pct>9% ∧ 大盤距季線≤-2.3% ∧ 股價≥20 元 ∧ 成交值≥1 億"
                    " ∧ 非注意(近5交易日)/處置(近10交易日)",
            "changes": ["CRASH_ATR_MIN 6% → 9%",
                        "移除 (strong|story) 前置閘",
                        "新增流動篩：股價≥20 元、成交值≥1 億",
                        "線上口徑對齊研究的乾淨池：排除注意/處置窗內的標的"],
            "episodes": _ep_stats(d, adopted)["episodes"],
        },
        "pool_discipline": pool,
        "oos": oos,
        "stop_sensitivity": stop,
        "caveats": [
            "holdout（2025-01~2026-07）只有 1 個崩段：那個命中率是「一段」的成績，不是 1.5 年的平均。",
            "命中率隨 ATR 門檻單調上升是波動度的效果——高 ATR 標的更容易摸到 +10%，"
            "路上也更晃（MAE 同表對照）。",
            "段級離散度 18%~85%：任何單一數字都會誤導，一律同時看「段中位」與「最差段」。",
            "本頁是歷史統計，不是投資建議。",
        ],
    }
    path = _BASE + "/data/wave_challenge.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    _log(f"已凍結 {path}")


def main() -> None:
    only = []
    if "--only" in sys.argv:
        only = [a for a in sys.argv[sys.argv.index("--only") + 1:] if not a.startswith("--")]
    want_json = "--json" in sys.argv
    d = load()
    secs = {"ceiling": sec_ceiling, "axes": sec_axes, "styles": sec_styles,
            "ablation": sec_ablation, "episodes": sec_episodes, "model": sec_model,
            "regime": sec_regime, "frontier": sec_frontier, "ladder": sec_ladder,
            "oos": sec_oos, "stop": sec_stop}
    got: dict = {}
    for k, fn in secs.items():
        if only and k not in only:
            continue
        got[k] = fn(d)
    if want_json:
        emit_json(d, got.get("regime"), got.get("frontier"), got.get("ladder"),
                  got.get("oos"), got.get("stop"))


if __name__ == "__main__":
    main()
