"""10 日碰到率專用挖掘 v2：事件家族擴充 × 三元交叉 × 放寬選中數護欄。

與 mega_mine.py 的四點差異
--------------------------
1) 目標函數改為定版口徑
   mega_mine 用的是 hit（mfe30≥10，30 日碰到）；本腳本一律用 hit10（mfe10≥10）
   與 mae10 代價。基率差很大（挖掘窗 30 日約 32%、10 日約 16%），兩者結果不可互比。

2) 新增三個事件家族（皆為嚴格 point-in-time）
   ETF/指數成分（index_constituent_events）
     etf_add_eff  納入生效後 1~10 交易日；etf_add_sweet 生效後 6~10 日（既有研究甜蜜點）
     etf_del_eff / etf_del_sweet 剔除對應窗；etf_add_ann 公告後 1~5 日
     etf_add_cnt20 過去 20 交易日內納入事件數（多指數同時納入＝強度）
   月營收（revenue_monthly；M 月營收自 M+1 月 10 日起可用）
     rev_yoy rev_mom rev_yoy_chg（YoY 加速）rev_yoy3（3 月均 YoY）
     rev_hi12（營收創 12 月新高）rev_pub5（可用後 1~5 交易日）
   季報（financials_quarterly；Q1 5/15、Q2 8/14、Q3 11/14、年報次年 3/31）
     eps_yoy eps_qoq gm_chg（毛利率 QoQ 變化）roe_v fin_pub10（可用後 1~10 交易日）

3) 條件生成加入三元 AND
   單變量（數值 × 6 分位）＋ 兩兩 AND ＋ 三元 AND（以支持度剪枝）。

4) 護欄：每日最少選中 5→2 檔
   讓「每天只選 1~2 檔但很準」的稀有設定有機會浮出（原護欄會直接 return None）。
   代價是小樣本假象變多，因此額外報 Wilson 95% 下界，並把 t 門檻改為依實際條件數
   動態 Bonferroni 校正（條件開越多、門檻越高），而非固定 4.0。

輸出分兩層：pass（過 Bonferroni，正式候選）與 watch（t≥4.0 但未過校正，觀察名單）。

用法：PYTHONIOENCODING=utf-8 python scripts/mega_mine2.py
輸出：data/mega_mine2_results.json + stdout 摘要
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

import numpy as np
import pandas as pd
from scipy.stats import norm

_BASE = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0]
sys.path.insert(0, _BASE)
sys.path.insert(0, _BASE + "/scripts")

import pop_condition_judge as judge  # noqa: E402

_DB = judge._DB
_V1_CACHE = _BASE + "/data/mega_mine_features.pkl"
_V2_CACHE = _BASE + "/data/mega_mine2_features.pkl"
_OUT = _BASE + "/data/mega_mine2_results.json"

_MINE_LO, _MINE_HI = judge._MINE_LO, judge._MINE_HI
_HOLD_LO = judge._HOLD_LO

_MIN_DAYS = 60      # 有效日下限（沿用）
_MIN_PICKS = 2      # 每日最少選中檔數（v1 為 5；放寬讓稀有高精度浮出）
_WATCH_T = 4.0      # 觀察名單 t 門檻（＝v1 的固定門檻）

_TARGET = "hit10"   # 定版目標：10 日內碰到 +10%
_COST = "mae10"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ─────────────────────────── 事件特徵（PIT） ───────────────────────────


def _tindex(dates_all: np.ndarray, when: pd.Series) -> np.ndarray:
    """把任意日期對齊到「>= 該日的第一個交易日」的序號；超出資料尾端記 -1。"""
    ti = np.searchsorted(dates_all, when.to_numpy(dtype=object), side="left")
    return np.where(ti >= len(dates_all), -1, ti)


def _add_etf(df: pd.DataFrame, con: sqlite3.Connection,
             dates_all: np.ndarray, row_ti: np.ndarray) -> pd.DataFrame:
    """ETF/指數成分事件窗。事件在公告日即公開，但一律以 offset≥1 起算避免當日前視。"""
    ev = pd.read_sql_query(
        "SELECT stock_id, action, announce_date, effective_date "
        "FROM index_constituent_events", con)
    _log(f"  ETF 成分事件 {len(ev):,} 筆")

    key = pd.Series(list(zip(df["stock_id"], row_ti)))

    def _window(sub: pd.DataFrame, col: str, lo: int, hi: int) -> np.ndarray:
        """事件基準日序號 + offset∈[lo,hi] 展開成 (stock, tindex) 集合，回傳旗標。"""
        s = sub.dropna(subset=[col])
        if s.empty:
            return np.zeros(len(df), dtype=bool)
        base = _tindex(dates_all, s[col])
        ok = base >= 0
        sid = s["stock_id"].to_numpy()[ok]
        base = base[ok]
        pairs = {(a, int(b + off)) for a, b in zip(sid, base)
                 for off in range(lo, hi + 1)}
        return key.isin(pairs).to_numpy()

    add = ev[ev["action"] == "add"]
    dele = ev[ev["action"] == "remove"]
    df["etf_add_eff"] = _window(add, "effective_date", 1, 10)
    df["etf_add_sweet"] = _window(add, "effective_date", 6, 10)
    df["etf_add_ann"] = _window(add, "announce_date", 1, 5)
    df["etf_del_eff"] = _window(dele, "effective_date", 1, 10)
    df["etf_del_sweet"] = _window(dele, "effective_date", 6, 10)

    # 強度：過去 20 交易日內納入生效事件數（多指數同時納入代表被動買盤更重）
    s = add.dropna(subset=["effective_date"])
    base = _tindex(dates_all, s["effective_date"])
    ok = base >= 0
    cnt: dict[tuple, int] = {}
    for a, b in zip(s["stock_id"].to_numpy()[ok], base[ok]):
        for off in range(1, 21):
            k = (a, int(b + off))
            cnt[k] = cnt.get(k, 0) + 1
    df["etf_add_cnt20"] = key.map(cnt).fillna(0.0).to_numpy(dtype=float)
    return df


def _asof(df: pd.DataFrame, right: pd.DataFrame, cols: list[str],
          dates_all: np.ndarray, row_ti: np.ndarray) -> np.ndarray:
    """point-in-time backward merge_asof：每個交易日往回找該檔最近一筆「已可用」財報。

    合併鍵用交易日序號（merge_asof 不接受字串日期），順帶回傳資料可用日的序號，
    讓「公布後第幾個交易日」可直接相減得出。
    """
    right = right.copy()
    right["ati"] = _tindex(dates_all, right["avail"])
    right = right[right["ati"] >= 0].drop(columns=["avail"])
    right = right.sort_values("ati", kind="mergesort")

    left = pd.DataFrame({"_i": np.arange(len(df)), "stock_id": df["stock_id"].to_numpy(),
                         "ti": row_ti}).sort_values("ti", kind="mergesort")
    m = pd.merge_asof(left, right, left_on="ti", right_on="ati",
                      by="stock_id", direction="backward")
    m = m.set_index("_i").sort_index()
    for c in cols:
        df[c] = m[c].to_numpy()
    return m["ati"].to_numpy(dtype=float)


def _add_revenue(df: pd.DataFrame, con: sqlite3.Connection,
                 dates_all: np.ndarray, row_ti: np.ndarray) -> pd.DataFrame:
    """月營收：M 月數字自 M+1 月 10 日（法定公布截止）起可用。"""
    r = pd.read_sql_query(
        "SELECT stock_id, year, month, revenue, yoy, mom FROM revenue_monthly", con)
    r = r.dropna(subset=["year", "month"]).sort_values(["stock_id", "year", "month"])
    _log(f"  月營收 {len(r):,} 筆")

    ym = r["year"].astype(int) * 12 + (r["month"].astype(int) - 1) + 1  # 次月
    r["avail"] = (ym // 12).astype(str).str.zfill(4) + "-" + \
                 (ym % 12 + 1).astype(str).str.zfill(2) + "-10"

    g = r.groupby("stock_id", sort=False)
    r["rev_yoy_chg"] = r["yoy"] - g["yoy"].shift(1)
    r["rev_yoy3"] = g["yoy"].transform(lambda s: s.rolling(3, 2).mean())
    r["rev_hi12"] = (r["revenue"] >= g["revenue"].transform(
        lambda s: s.rolling(12, 6).max())).astype(float)
    r = r.rename(columns={"yoy": "rev_yoy", "mom": "rev_mom"})

    cols = ["rev_yoy", "rev_mom", "rev_yoy_chg", "rev_yoy3", "rev_hi12"]
    ati = _asof(df, r[["stock_id", "avail"] + cols], cols, dates_all, row_ti)
    age = row_ti - ati
    df["rev_pub5"] = (age >= 1) & (age <= 5)
    return df


def _add_fin(df: pd.DataFrame, con: sqlite3.Connection,
             dates_all: np.ndarray, row_ti: np.ndarray) -> pd.DataFrame:
    """季報：Q1 5/15、Q2 8/14、Q3 11/14、Q4（年報）次年 3/31 為法定截止。"""
    q = pd.read_sql_query(
        "SELECT stock_id, year, quarter, eps, gross_margin, roe "
        "FROM financials_quarterly", con)
    q = q.dropna(subset=["year", "quarter"]).sort_values(["stock_id", "year", "quarter"])
    _log(f"  季報 {len(q):,} 筆")

    dl = {1: ("-05-15", 0), 2: ("-08-14", 0), 3: ("-11-14", 0), 4: ("-03-31", 1)}
    suf = q["quarter"].astype(int).map(lambda x: dl[x][0])
    yr = q["year"].astype(int) + q["quarter"].astype(int).map(lambda x: dl[x][1])
    q["avail"] = yr.astype(str) + suf

    g = q.groupby("stock_id", sort=False)
    q["eps_qoq"] = q["eps"] - g["eps"].shift(1)
    q["eps_yoy"] = q["eps"] - g["eps"].shift(4)
    q["gm_chg"] = q["gross_margin"] - g["gross_margin"].shift(1)
    q = q.rename(columns={"roe": "roe_v"})
    q["roe_v"] = pd.to_numeric(q["roe_v"], errors="coerce")

    cols = ["eps_qoq", "eps_yoy", "gm_chg", "roe_v"]
    ati = _asof(df, q[["stock_id", "avail"] + cols], cols, dates_all, row_ti)
    age = row_ti - ati
    df["fin_pub10"] = (age >= 1) & (age <= 10)
    return df


# add_market_layer / add_sector_neutral 已上移到 mega_mine.build_features()，
# 讓所有吃 mega_mine_features.pkl 的下游一次修好；此處直接沿用，不重複定義。

def build_features_v2() -> pd.DataFrame:
    if not os.path.exists(_V1_CACHE):
        raise SystemExit(f"缺少 v1 特徵快取 {_V1_CACHE}，請先跑 mega_mine.py")
    df = pd.read_pickle(_V1_CACHE)
    _log(f"載入 v1 特徵 {len(df):,} 列 / {df.shape[1]} 欄")

    # 只留挖掘用得到的欄，控制記憶體
    drop = ["open", "high", "low", "close", "volume", "ma5", "ma10", "ma60",
            "ma120", "ma240", "vol_ma5", "vol_ma20", "kd_d", "macd",
            "macd_signal", "atr14", "mfe30", "ret30", "mfe10", "ma_align"]
    df = df.drop(columns=[c for c in drop if c in df.columns])

    df = df.sort_values(["stock_id", "date"]).reset_index(drop=True)
    dates_all = np.sort(df["date"].unique())
    didx = {d: i for i, d in enumerate(dates_all)}
    row_ti = df["date"].map(didx).to_numpy(dtype=int)

    con = sqlite3.connect(_DB)
    _log("擴充事件特徵…")
    df = _add_etf(df, con, dates_all, row_ti)
    df = _add_revenue(df, con, dates_all, row_ti)
    df = _add_fin(df, con, dates_all, row_ti)
    con.close()

    need = ("sec_rel20", "sec_breadth_rel", "peer_surge5_rel")
    miss = [c for c in need if c not in df.columns]
    if miss:
        raise SystemExit(
            f"v1 快取缺少中性化類股特徵 {miss} —— 請先刪除 {_V1_CACHE} 並重跑 "
            "mega_mine.py 重建（修正已上移到 mega_mine.build_features）")

    _log(f"v2 特徵完成 {df.shape[1]} 欄")
    return df


# ─────────────────────────── 評估器 ───────────────────────────


def _wilson_lo(k: int, n: int, z: float = 1.96) -> float:
    """命中率的 Wilson 95% 下界；小樣本高命中會被自動打折。"""
    if n <= 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return float((c - m) / d)


class Evaluator:
    """向量化日層級評估：命中、ATR 桶控波動增量、MAE 代價、t 值、Wilson 下界。"""

    def __init__(self, df: pd.DataFrame):
        # 只留下評估需要的向量，不保留 DataFrame 複本（3 個 fold 各留一份會多吃數 GB）
        sub = df[["date", "atr_bucket", _TARGET, _COST]]
        self.date_codes, self.dates = pd.factorize(sub["date"], sort=True)
        self.n_dates = len(self.dates)
        self.day_base = sub.groupby("date")[_TARGET].mean().reindex(self.dates).to_numpy()
        bkt = sub.groupby(["date", "atr_bucket"])
        self.hit = sub[_TARGET].to_numpy(dtype=float)
        self.mae = sub[_COST].to_numpy(dtype=float)
        self.bexp = bkt[_TARGET].transform("mean").to_numpy(dtype=float)
        self.bmae = bkt[_COST].transform("mean").to_numpy(dtype=float)
        # 反向標籤：10 日內碰 −10%。高 hit10 不等於看漲 —— 「處置10日」單獨的
        # 碰−10% 機率（58.5%）比碰+10%（56.1%）還高，是波動訊號不是方向訊號。
        # 沒有這個對照，任何高命中率條件都可能只是挑出高波動股。
        self.down = (sub[_COST].to_numpy(dtype=float) <= -10.0).astype(float)

    def run(self, mask: np.ndarray) -> dict | None:
        idx = np.flatnonzero(mask)
        if len(idx) < _MIN_DAYS * _MIN_PICKS:
            return None
        dc = self.date_codes[idx]
        cnt = np.bincount(dc, minlength=self.n_dates)
        ok_days = cnt >= _MIN_PICKS
        n_days = int(ok_days.sum())
        if n_days < _MIN_DAYS:
            return None
        keep = ok_days[dc]
        idx, dc = idx[keep], dc[keep]
        cnt = np.bincount(dc, minlength=self.n_dates).astype(float)
        with np.errstate(invalid="ignore", divide="ignore"):
            d_hit = np.bincount(dc, weights=self.hit[idx], minlength=self.n_dates) / cnt
            d_ctrl = np.bincount(dc, weights=(self.hit[idx] - self.bexp[idx]),
                                 minlength=self.n_dates) / cnt
            d_mae = np.bincount(dc, weights=(self.mae[idx] - self.bmae[idx]),
                                minlength=self.n_dates) / cnt
        sel = ok_days
        lift = d_hit[sel] - self.day_base[sel]
        ctrl, maed = d_ctrl[sel], d_mae[sel]

        def _t(a: np.ndarray) -> float:
            a = a[~np.isnan(a)]
            if len(a) < 3:
                return float("nan")
            sd = a.std(ddof=1)
            return float(a.mean() / (sd / np.sqrt(len(a)))) if sd > 0 else float("nan")

        n = len(idx)
        k = int(self.hit[idx].sum())
        dn = float(self.down[idx].mean()) * 100
        return {
            "days": n_days, "n": n, "avg_picks": round(float(cnt[sel].mean()), 1),
            "hit": round(k / n * 100, 1),
            "down": round(dn, 1),
            "updown": round((k / n * 100) / dn, 2) if dn > 0 else None,
            "wilson": round(_wilson_lo(k, n) * 100, 1),
            "lift": round(float(np.nanmean(lift)) * 100, 2), "t_lift": round(_t(lift), 1),
            "ctrl": round(float(np.nanmean(ctrl)) * 100, 2), "t_ctrl": round(_t(ctrl), 1),
            "mae_cost": round(float(np.nanmean(maed)), 2),
        }


# ─────────────────────────── 條件生成 ───────────────────────────

# 依 feature_contamination_audit.py 的稽核結果整理，規則：
#   翻號 → 移出池；衰減>60% → 保留但標記；覆蓋不足 → 重建；市場成分過重且失效 → 改中性化版
# 已移除（挖掘窗與 holdout 分層價差翻號，方向不可信）：
#   vol_trend_chg, ret5_accel, dh_chg5, dist_60d_high,
#   etf_add_eff/sweet/ann, etf_del_eff/sweet, sec_ret20
# 已重建（原本覆蓋率 1.3~4.5%，或組件缺失）：mkt_bias60, mkt_ret20, fg, fg_chg5
# 已改中性化：sec_ret20→sec_rel5/10/20、sec_breadth→sec_breadth_rel
#   （peer_surge5 雖有市場成分 0.371，但 +14.4→+17.8pp 又穩又強，保留原版並另加相對版）
_FLAGGED = {"margin_chg5", "rev_yoy_chg"}   # 衰減>60% 但同號，保留並在輸出標記

_NUMERIC = [
    # v2 新特徵
    "etf_add_cnt20", "rev_yoy", "rev_mom", "rev_yoy_chg", "rev_yoy3",
    "eps_qoq", "eps_yoy", "gm_chg",   # roe_v 整欄 NULL，不納入
    # v1 網絡/事件/變化值
    "sec_att5", "sec_att_chg", "node_att5", "node_att_chg", "node_surge5", "att_times",
    "inst_f5_chg", "inst_t5_chg", "atr_pct_chg",
    "bias20_chg", "pos52_chg20", "pe_chg20",
    # v1 既有代表特徵
    "atr_pct", "vol_ratio", "vol_trend", "c_over_ma20", "pos_52w",
    "ret5", "ret20", "inst_f5", "inst_t5", "inst_tot10", "inst_streak",
    "sq_ratio", "short_chg5", "margin_chg5", "pe", "pb",
    "sec_rel5", "sec_rel10", "sec_rel20", "sec_breadth_rel",
    "peer_surge5", "peer_surge5_rel", "rel_ret20",
    "kd_k", "bias_20", "bias_60", "macd_hist",
]
# 純市場層特徵（fg / fg_chg5 / mkt_bias60 / mkt_ret20，ts_share=1.0）全部移出橫斷面池：
# 同一天對所有股票同值，在「日內同 ATR 桶」的控波動比較裡變異為零，唯一作用是製造
# 日期選擇效應 —— 正是讓 2026 主導結果的機制。重建覆蓋率到 100% 後仍全數翻號。
# 市場層 regime 研究改在 market_base_gate.py 獨立進行。

# 二元謂詞：(欄位, 取高端?)
_PRED_SPEC = [
    ("etf_add_cnt20", True), ("rev_yoy", True), ("rev_yoy_chg", True),
    ("rev_yoy3", True), ("rev_mom", True), ("eps_yoy", True), ("gm_chg", True),
    ("sec_att5", True), ("node_att5", True), ("node_surge5", True),
    ("inst_f5_chg", True), ("inst_t5_chg", True),
    ("pos52_chg20", True),
    ("atr_pct_chg", True), ("vol_ratio", True), ("vol_trend", True),
    ("inst_f5", True), ("inst_t5", True), ("inst_streak", True), ("sq_ratio", True),
    ("peer_surge5", True), ("peer_surge5_rel", True), ("sec_breadth_rel", True),
    ("rel_ret20", True),
    ("pos_52w", True), ("atr_pct", True), ("c_over_ma20", True),
    ("ret20", False), ("bias_60", False), ("margin_chg5", False), ("short_chg5", True),
    ("sec_rel5", True), ("sec_rel10", True), ("sec_rel20", True),
    ("kd_k", True), ("macd_hist", True), ("bias_20", False),
]

# 旗標型謂詞（布林欄，直接用）
# ETF 五個旗標全數移除：稽核顯示挖掘窗 −0.80~−2.01pp、holdout +0.50~+2.71pp 全部翻號，
# 且未控 ATR 時看到的 +2.0pp「甜蜜點」在控波動後消失 —— ETF 納入效應其實是波動度的代理。
_FLAGS = [
    ("營收公布1-5日", "rev_pub5"), ("營收創12月新高", "rev_hi12"),
    ("季報公布1-10日", "fin_pub10"),
    ("注意5日", "att_notice5"), ("處置10日", "att_punish10"),
]


def build_predicates(mine: pd.DataFrame,
                     target: pd.DataFrame) -> list[tuple[str, np.ndarray]]:
    """二元謂詞池；門檻一律由挖掘窗計算，遮罩套在 target 上。"""
    preds: list[tuple[str, np.ndarray]] = []

    def _p(name: str, mask_m: np.ndarray, mask_t: np.ndarray) -> None:
        share = float(np.nanmean(mask_m.astype(float)))
        if 0.002 <= share <= 0.6:      # v1 下限 0.005；放寬讓稀有事件（ETF/季報窗）進池
            preds.append((name, mask_t))

    for name, col in _FLAGS:
        if col not in mine.columns:
            continue
        _p(name, mine[col].fillna(False).to_numpy(bool),
           target[col].fillna(False).to_numpy(bool))

    _p("注意次數≥2", mine["att_times"].to_numpy(float) >= 2,
       target["att_times"].to_numpy(float) >= 2)

    for f, hi in _PRED_SPEC:
        if f not in mine.columns:
            continue
        vm, vt = mine[f].to_numpy(float), target[f].to_numpy(float)
        if hi:
            q = np.nanquantile(vm, 0.80)
            _p(f"{f}>q80", vm > q, vt > q)
        else:
            q = np.nanquantile(vm, 0.20)
            _p(f"{f}<q20", vm < q, vt < q)

    _p("上升結構",
       (mine["c_over_ma20"].to_numpy(float) > 0) & mine["ma20_up5"].fillna(False).to_numpy(bool),
       (target["c_over_ma20"].to_numpy(float) > 0) & target["ma20_up5"].fillna(False).to_numpy(bool))

    seen, uniq = set(), []
    for name, m in preds:
        if name not in seen:
            seen.add(name)
            uniq.append((name, m))
    return uniq


def _uni_defs(mine: pd.DataFrame) -> list[tuple[str, str, str, float]]:
    """單變量條件定義（名稱, 欄位, 方向, 門檻）；門檻取自挖掘窗分位數。"""
    out = []
    for f in _NUMERIC:
        if f not in mine.columns:
            continue
        v = mine[f].to_numpy(dtype=float)
        if np.all(np.isnan(v)):
            continue
        qs = np.nanquantile(v, [0.10, 0.20, 0.30, 0.70, 0.80, 0.90])
        for q, p in zip(qs[:3], ("q10", "q20", "q30")):
            out.append((f"{f}<{p}({q:.3g})", f, "<", float(q)))
        for q, p in zip(qs[3:], ("q70", "q80", "q90")):
            out.append((f"{f}>{p}({q:.3g})", f, ">", float(q)))
    return out


def _uni_mask(target: pd.DataFrame, f: str, op: str, q: float) -> np.ndarray:
    v = target[f].to_numpy(dtype=float)
    return (v < q) if op == "<" else (v > q)


# ─────────────────────────── 主流程 ───────────────────────────


def main() -> None:
    t0 = time.time()
    if os.path.exists(_V2_CACHE):
        df = pd.read_pickle(_V2_CACHE)
        _log(f"載入 v2 特徵快取 {len(df):,} 列 / {df.shape[1]} 欄")
    else:
        df = build_features_v2()
        df.to_pickle(_V2_CACHE)
        _log(f"已寫入 {_V2_CACHE}")

    d = df["date"].astype(str)
    mine = df[(d >= _MINE_LO) & (d <= _MINE_HI)].reset_index(drop=True)
    hold = df[d >= _HOLD_LO].reset_index(drop=True)
    del df
    _log(f"挖掘窗 {len(mine):,} 列（基率 {mine[_TARGET].mean()*100:.1f}%）／"
         f"holdout {len(hold):,} 列（基率 {hold[_TARGET].mean()*100:.1f}%）")

    valid_m = ~np.isnan(mine[_TARGET].to_numpy(dtype=float))
    valid_h = ~np.isnan(hold[_TARGET].to_numpy(dtype=float))

    pm = build_predicates(mine, mine)
    ph = dict(build_predicates(mine, hold))
    names = [n for n, _ in pm]
    _log(f"謂詞池 {len(names)} 個：{', '.join(names[:12])} …")
    Pm = np.vstack([m & valid_m for _, m in pm])
    Ph = np.vstack([ph[n] & valid_h for n in names])

    unis = _uni_defs(mine)
    n_pairs = len(names) * (len(names) - 1) // 2
    n_tri = len(names) * (len(names) - 1) * (len(names) - 2) // 6
    n_total = len(unis) + n_pairs + n_tri
    t_gate = float(norm.isf(0.025 / n_total))
    _log(f"條件總數 {n_total:,}（單變量 {len(unis)} / 兩兩 {n_pairs:,} / 三元 {n_tri:,}）"
         f"；Bonferroni t 門檻 {t_gate:.2f}")

    ev_all = Evaluator(mine)
    dts = np.sort(mine["date"].unique())
    fold_evs = []
    for fi in range(3):
        fd = set(dts[fi * len(dts) // 3:(fi + 1) * len(dts) // 3])
        sel = mine["date"].isin(fd).to_numpy()
        fold_evs.append((Evaluator(mine[sel]), sel))

    min_support = _MIN_DAYS * _MIN_PICKS
    results: list[dict] = []
    seen_names: set[str] = set()

    def _consider(name: str, mask_m: np.ndarray, ref: dict) -> None:
        if name in seen_names:
            return
        r = ev_all.run(mask_m)
        if r is None or r["ctrl"] <= 0 or r["t_ctrl"] < _WATCH_T:
            return
        folds = []
        for fev, fsel in fold_evs:
            fr = fev.run(mask_m[fsel])
            if fr is None or fr["ctrl"] <= 0:
                return
            folds.append(fr["ctrl"])
        seen_names.add(name)
        results.append({"cond": name, **r, "fold_ctrl": folds,
                        "tier": "pass" if r["t_ctrl"] >= t_gate else "watch",
                        # 稽核標記：用到 holdout 衰減>60% 的特徵，結論要打折看
                        "flagged": sorted(f for f in _FLAGGED if f in name), **ref})

    # 單變量
    _log("評估單變量…")
    for nm, f, op, q in unis:
        _consider(nm, _uni_mask(mine, f, op, q) & valid_m, {"ref": ["uni", f, op, q]})

    # 兩兩
    _log("評估兩兩交叉…")
    P = len(names)
    pair_ok = np.zeros((P, P), dtype=bool)
    for i in range(P):
        for j in range(i + 1, P):
            m = Pm[i] & Pm[j]
            if int(m.sum()) < min_support:
                continue
            pair_ok[i, j] = pair_ok[j, i] = True
            _consider(f"{names[i]} & {names[j]}", m, {"ref": ["and", [i, j]]})

    # 三元（三個配對都要有支持度才試）
    _log("評估三元交叉…")
    done = 0
    for i in range(P):
        for j in range(i + 1, P):
            if not pair_ok[i, j]:
                continue
            mij = Pm[i] & Pm[j]
            for k in range(j + 1, P):
                if not (pair_ok[i, k] and pair_ok[j, k]):
                    continue
                m = mij & Pm[k]
                done += 1
                if done % 2000 == 0:
                    _log(f"  三元 {done:,} 組…（已收 {len(results)} 條）")
                if int(m.sum()) < min_support:
                    continue
                _consider(f"{names[i]} & {names[j]} & {names[k]}", m,
                          {"ref": ["and", [i, j, k]]})

    n_pass = sum(1 for r in results if r["tier"] == "pass")
    _log(f"挖掘窗存活 {len(results)} 條（pass {n_pass} / watch {len(results)-n_pass}）")

    # holdout（存活者一次）
    _log("holdout 驗證…")
    ev_h = Evaluator(hold)
    for r in results:
        ref = r.pop("ref")
        if ref[0] == "uni":
            _, f, op, q = ref
            hm = _uni_mask(hold, f, op, q) & valid_h
        else:
            hm = Ph[ref[1][0]].copy()
            for t in ref[1][1:]:
                hm &= Ph[t]
        r["holdout"] = ev_h.run(hm)

    results.sort(key=lambda x: -((x["holdout"] or {}).get("wilson", -99)))
    with open(_OUT, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M"),
                   "target": _TARGET, "min_picks": _MIN_PICKS,
                   "n_conditions": n_total, "t_gate": round(t_gate, 2),
                   "n_pass": n_pass, "n_watch": len(results) - n_pass,
                   "base_mine": round(float(mine[_TARGET].mean()) * 100, 1),
                   "base_hold": round(float(hold[_TARGET].mean()) * 100, 1),
                   "results": results}, fh, ensure_ascii=False, indent=1)
    _log(f"完成（{time.time()-t0:.0f}s）→ {_OUT}")

    hi = [r for r in results if (r["holdout"] or {}).get("hit", 0) >= 70]
    print(f"\n=== holdout 10 日碰到率 ≥70%：{len(hi)} 條 ===")
    print(f"{'條件':<58}{'層級':>6}{'h命中':>7}{'碰-10%':>8}{'上/下':>7}"
          f"{'Wilson':>8}{'h增量':>8}{'日均檔':>7}")
    for r in (hi or results[:30]):
        h = r["holdout"] or {}
        print(f"{r['cond']:<58}{r['tier']:>6}{h.get('hit','—'):>6}%{h.get('down','—'):>7}%"
              f"{h.get('updown','—'):>7}{h.get('wilson','—'):>7}%"
              f"{h.get('ctrl','—'):>7}pp{h.get('avg_picks','—'):>7}")
    print("  上/下 = 碰+10% ÷ 碰-10%；<1 代表只是高波動、沒有方向性（全市場基準約 0.81）")


if __name__ == "__main__":
    main()
