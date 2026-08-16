"""大盤「高基率 regime」閘門研究：能不能事前預測 10 日碰到率的基率會高？

動機
----
mega_mine2 挖到的 ≥70% 條件（處置家族）逐年拆開是 50/44/54/52/**76**%，那個 76% 出現在
2026 —— 而 2026 全市場 10 日基率 22.3%，其他年只有 11~17%。絕對命中率其實是
「條件超額 + 大盤基率」，超額逐年穩定（+31~40pp），會漂的是基率。
所以要拿到穩定的絕對 70%，該預測的不是個股，是**大盤基率**。

既有 fg 閘為何失效
------------------
mega_mine 的 fg 用 market_index 算動能/波動，但該表只有 2026-03 之後 106 列 ——
2021~2025 那兩個組件全是 NaN，fg 實際只由廣度/漲跌家數/融資三項構成。
本腳本改用 daily_prices（2020-01 起、1607 個交易日、含 turnover）自建大盤層。

候選閘門（皆 T 日收盤可知，無前視）
----------------------------------
  實現波動度  rv20（等權大盤日報酬 20 日標準差年化）、rv20_pct（252 日百分位）、rv20_chg
  成交值擴張  tv_5_60（總成交值 5 日均 / 60 日均）、tv_20_60、tv_pct（20 日均的 252 日百分位）
  漲停家數    lu（當日漲幅≥9% 家數佔比）、lu5（5 日均）、lu_trend（5 日均 − 20 日均）、lu_pct

驗證三關（沿用 pop_regime_gate_validate.py 的紀律）
--------------------------------------------------
  1. 參數敏感度：門檻分位數掃 60/70/80，鄰格須同號，只有單格有效＝過擬合
  2. 日層級顯著性：以「每個交易日的清單命中率」為一個觀測做 Welch t（個股同日高度相關）
  3. circular-shift 安慰劑：閘門狀態序列整段平移，保留自相關但打斷與市場對齊

用法：PYTHONIOENCODING=utf-8 python scripts/market_base_gate.py
輸出：data/market_base_gate.json + stdout
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time

import numpy as np
import pandas as pd
from scipy import stats

_BASE = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0]
sys.path.insert(0, _BASE)
sys.path.insert(0, _BASE + "/scripts")

_DB = _BASE + "/data/twa.db"
_FEAT = _BASE + "/data/mega_mine2_features.pkl"
_OUT = _BASE + "/data/market_base_gate.json"

_YEARS = ["2021", "2022", "2023", "2024", "2025", "2026"]
_LIMIT_UP = 9.0     # 台股漲跌幅 10%；用 9% 抓「接近漲停」避免除權息/四捨五入邊界
_MIN_DAYS_YR = 40   # 逐年統計的最少交易日


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ─────────────────────────── 大盤層特徵（PIT） ───────────────────────────


def build_market() -> pd.DataFrame:
    """由 daily_prices 自建大盤日層特徵。全部只用到 T 日（含）以前的資料。"""
    con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    px = pd.read_sql_query(
        "SELECT stock_id, date, close, turnover FROM daily_prices "
        "WHERE close IS NOT NULL ORDER BY stock_id, date", con)
    con.close()
    _log(f"daily_prices {len(px):,} 列 / {px['date'].nunique():,} 個交易日")

    g = px.groupby("stock_id", sort=False)
    px["ret"] = (px["close"] / g["close"].shift(1) - 1.0) * 100
    px["is_lu"] = (px["ret"] >= _LIMIT_UP).astype(float)

    m = px.groupby("date").agg(
        mkt_ret=("ret", "mean"),            # 等權大盤日報酬
        lu=("is_lu", "mean"),               # 漲停家數佔比
        turnover=("turnover", "sum"),
        n=("ret", "size"),
    ).reset_index()
    m["lu"] *= 100

    # 實現波動度
    m["rv20"] = m["mkt_ret"].rolling(20, 15).std() * np.sqrt(252)
    m["rv20_chg"] = m["rv20"] - m["rv20"].shift(20)
    m["rv20_pct"] = m["rv20"].rolling(252, 120).rank(pct=True) * 100

    # 成交值擴張
    tv = m["turnover"].astype(float)
    m["tv_5_60"] = tv.rolling(5, 3).mean() / tv.rolling(60, 40).mean()
    m["tv_20_60"] = tv.rolling(20, 15).mean() / tv.rolling(60, 40).mean()
    m["tv_pct"] = tv.rolling(20, 15).mean().rolling(252, 120).rank(pct=True) * 100

    # 漲停家數趨勢
    m["lu5"] = m["lu"].rolling(5, 3).mean()
    m["lu_trend"] = m["lu"].rolling(5, 3).mean() - m["lu"].rolling(20, 15).mean()
    m["lu_pct"] = m["lu"].rolling(20, 15).mean().rolling(252, 120).rank(pct=True) * 100

    return m


_GATES = ["rv20", "rv20_chg", "rv20_pct", "tv_5_60", "tv_20_60", "tv_pct",
          "lu", "lu5", "lu_trend", "lu_pct"]


# ─────────────────────────── 分析 ───────────────────────────


def _welch_t(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    if len(a) < 5 or len(b) < 5:
        return float("nan")
    return float(stats.ttest_ind(a, b, equal_var=False).statistic)


def _placebo(day_hit: np.ndarray, gate: np.ndarray, n_shift: int = 200) -> dict:
    """circular-shift 安慰劑：把閘門狀態整段平移，保留自相關但打斷與市場的對齊。"""
    real = np.nanmean(day_hit[gate]) - np.nanmean(day_hit[~gate])
    n = len(gate)
    diffs = []
    for s in np.linspace(n // 20, n - n // 20, n_shift).astype(int):
        gs = np.roll(gate, s)
        if gs.sum() < 5 or (~gs).sum() < 5:
            continue
        diffs.append(np.nanmean(day_hit[gs]) - np.nanmean(day_hit[~gs]))
    diffs = np.array(diffs)
    return {"real": round(float(real) * 100, 2),
            "placebo_mean": round(float(np.nanmean(diffs)) * 100, 2),
            "placebo_p95": round(float(np.nanpercentile(diffs, 95)) * 100, 2),
            "pctile": round(float((diffs < real).mean()) * 100, 1)}


def main() -> None:
    t0 = time.time()
    mkt = build_market()

    _log("載入個股特徵，組日層目標…")
    df = pd.read_pickle(_FEAT)
    d = df["date"].astype(str)
    mine = df[(d >= "2021-01-01") & (d <= "2024-12-31")]
    qa = float(np.nanquantile(mine["atr_pct_chg"], 0.80))
    qm = float(np.nanquantile(mine["margin_chg5"], 0.20))

    # 目標 A：全市場當日 10 日基率；目標 B：處置家族清單當日命中率
    cond = (df["att_punish10"].fillna(False).to_numpy(bool)
            & (df["atr_pct_chg"].to_numpy(float) > qa)
            & (df["margin_chg5"].to_numpy(float) < qm))
    base_day = df.groupby("date")["hit10"].mean()
    sel = df[cond]
    g_sel = sel.groupby("date")
    agg = g_sel["hit10"].agg(["mean", "size"])
    ok = agg["size"] >= 2
    cond_day = agg[ok]["mean"]
    cond_mae = g_sel["mae10"].mean()[ok]
    del df
    _log(f"處置家族有效日 {len(cond_day):,} 天（每日≥2 檔）")

    m = mkt.set_index("date")
    m["base10"] = base_day
    m["cond_hit"] = cond_day
    m["cond_mae"] = cond_mae
    m["yr"] = m.index.astype(str).str[:4]
    m = m[m["yr"].isin(_YEARS)]

    # ── 第一關：IC（閘門 vs 未來 10 日基率）──
    print(f"\n=== 閘門 vs 全市場 10 日基率：Spearman IC ===")
    print(f"{'閘門':<12}{'全期IC':>8}" + "".join(f"{y:>8}" for y in _YEARS))
    ics = {}
    for gname in _GATES:
        v = m[gname]
        ok = v.notna() & m["base10"].notna()
        ic = stats.spearmanr(v[ok], m["base10"][ok]).statistic if ok.sum() > 60 else np.nan
        ics[gname] = round(float(ic), 3)
        row = ""
        for y in _YEARS:
            s = m[m["yr"] == y]
            o = s[gname].notna() & s["base10"].notna()
            r = stats.spearmanr(s[gname][o], s["base10"][o]).statistic if o.sum() > _MIN_DAYS_YR else np.nan
            row += f"{r:>8.2f}" if not np.isnan(r) else f"{'—':>8}"
        print(f"{gname:<12}{ic:>8.3f}{row}")

    # ── 第二關：門檻掃描（參數敏感度）× 處置家族逐年命中 ──
    print(f"\n=== 閘門開啟時，處置&ATRΔ&融資減 的逐年 10 日命中 ===")
    print(f"{'閘門':<12}{'門檻':>6}{'覆蓋':>7}{'基率':>7}{'命中':>7}{'Welch t':>9}"
          f"  " + "".join(f"{y:>7}" for y in _YEARS))
    rows = []
    for gname in _GATES:
        for qp in (60, 70, 80):
            v = m[gname].to_numpy(dtype=float)
            if np.all(np.isnan(v)):
                continue
            thr = float(np.nanquantile(v, qp / 100))
            gate = (v > thr) & ~np.isnan(v)
            if gate.sum() < 60:
                continue
            ch = m["cond_hit"].to_numpy(dtype=float)
            have = ~np.isnan(ch)
            g_on, g_off = gate & have, (~gate) & have
            if g_on.sum() < 30 or g_off.sum() < 30:
                continue
            hit_on = float(np.nanmean(ch[g_on])) * 100
            base_on = float(np.nanmean(m["base10"].to_numpy(float)[gate])) * 100
            t = _welch_t(ch[g_on], ch[g_off])
            yrs = []
            for y in _YEARS:
                ym = (m["yr"] == y).to_numpy() & g_on
                yrs.append(float(np.nanmean(ch[ym])) * 100 if ym.sum() >= 10 else np.nan)
            rows.append({"gate": gname, "q": qp, "thr": round(thr, 4),
                         "cover": round(float(gate.mean()) * 100, 1),
                         "base_on": round(base_on, 1), "hit_on": round(hit_on, 1),
                         "welch_t": round(t, 2),
                         "yearly": [round(x, 1) if not np.isnan(x) else None for x in yrs]})
            ys = "".join(f"{x:>7.1f}" if not np.isnan(x) else f"{'—':>7}" for x in yrs)
            print(f"{gname:<12}{'q'+str(qp):>6}{gate.mean()*100:>6.1f}%"
                  f"{base_on:>6.1f}%{hit_on:>6.1f}%{t:>9.2f}  {ys}")

    # ── 第三關：安慰劑（取逐年最穩的前 3 名）──
    def _stability(r: dict) -> float:
        ys = [x for x in r["yearly"] if x is not None]
        return min(ys) if len(ys) >= 4 else -99

    rows.sort(key=lambda r: -_stability(r))
    print(f"\n=== circular-shift 安慰劑（依「逐年最低命中」排序取前 3）===")
    ch = m["cond_hit"].to_numpy(dtype=float)
    for r in rows[:3]:
        v = m[r["gate"]].to_numpy(dtype=float)
        gate = (v > r["thr"]) & ~np.isnan(v)
        p = _placebo(ch, gate)
        r["placebo"] = p
        print(f"{r['gate']}>q{r['q']}：真實差 {p['real']:+.2f}pp／"
              f"平移分布均值 {p['placebo_mean']:+.2f}pp、95分位 {p['placebo_p95']:+.2f}pp"
              f"／真實落在平移分布 {p['pctile']:.1f} 分位"
              f"  → {'通過' if p['pctile'] >= 95 else '未通過'}")

    # ── 第四關：絕對門檻轉移測試 ──
    # 分位數門檻會被各年自身分布拉平，看不出「2026 是不是只是波動高」。
    # 改用絕對門檻：若同樣的 rv20 水準在 2026 前後命中一致，波動就是機制；不一致就不是。
    transfer = _transfer(m)

    with open(_OUT, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M"),
                   "ic": ics, "scan": rows, "transfer": transfer},
                  fh, ensure_ascii=False, indent=1)
    _log(f"完成（{time.time()-t0:.0f}s）→ {_OUT}")


def _transfer(m: pd.DataFrame) -> list[dict]:
    """絕對 rv20 門檻下，2026 前 vs 2026 的基率與條件命中是否一致（機制檢定）。

    另診斷高門檻日是否叢集於崩盤：若大盤同期在跌，該閘門實為「崩盤反彈閘」，
    命中高但進場時點最難執行，需另計 MAE 代價。
    """
    ch = m["cond_hit"].to_numpy(dtype=float)
    b10 = m["base10"].to_numpy(dtype=float)
    pre, is26 = (m["yr"] != "2026").to_numpy(), (m["yr"] == "2026").to_numpy()
    have = ~np.isnan(ch)

    print("\n=== 絕對門檻 rv20≥X：同樣波動水準，2026 前後是否一致？ ===")
    print(f"{'rv20≥':>6} | {'2021-25 日/基率/命中':>28} | {'2026 日/基率/命中':>26}")
    out = []
    for x in (10, 12, 14, 16, 17.5, 20, 24):
        g = (m["rv20"] >= x).to_numpy()
        a, b = g & pre & have, g & is26 & have
        rec = {"thr": x,
               "pre_days": int(a.sum()),
               "pre_base": round(float(np.nanmean(b10[g & pre])) * 100, 1) if (g & pre).any() else None,
               "pre_hit": round(float(np.nanmean(ch[a])) * 100, 1) if a.sum() >= 10 else None,
               "y26_days": int(b.sum()),
               "y26_base": round(float(np.nanmean(b10[g & is26])) * 100, 1) if (g & is26).any() else None,
               "y26_hit": round(float(np.nanmean(ch[b])) * 100, 1) if b.sum() >= 10 else None}
        out.append(rec)
        f = lambda n, bs, h: f"{n:>5} {bs if bs is not None else '—':>6}% {(str(h)+'%') if h else 'n/a':>7}"
        print(f"{x:>6} | {f(rec['pre_days'], rec['pre_base'], rec['pre_hit']):>28} | "
              f"{f(rec['y26_days'], rec['y26_base'], rec['y26_hit']):>26}")

    hi = m[(m["rv20"] >= 24) & (m["yr"] != "2026") & m["cond_hit"].notna()]
    if len(hi):
        print(f"\n高波動日（rv20≥24、2026 前 {len(hi)} 日）是否叢集於崩盤：")
        print(f"  平均大盤日報酬 {hi['mkt_ret'].mean():+.3f}%／條件命中 "
              f"{hi['cond_hit'].mean()*100:.1f}%／MAE {hi['cond_mae'].mean():.2f}")
        base_days = m[(m["rv20"] < 24) & (m["yr"] != "2026")]
        print(f"  對照（同期 rv20<24）：命中 {base_days['cond_hit'].mean()*100:.1f}%／"
              f"MAE {base_days['cond_mae'].mean():.2f}")
    return out


if __name__ == "__main__":
    main()
