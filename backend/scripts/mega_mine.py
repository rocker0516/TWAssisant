"""超大規模條件挖掘：網絡傳染 × 變化值 × 市場情緒 × 既有特徵，≥1000 條件一次驗。

特徵擴充（併入 pop_condition_judge 快取）：
  網絡層：sec_att5/node_att5（類股/產業鏈節點 5 日注意+處置事件數，不含自身）
          sec_att_chg/node_att_chg（其 5 日Δ）、node_surge5（節點鄰居 5 日噴>10% 比例，不含自身）
  自身事件：att_notice5/att_punish10（旗標）、att_times（5日內最近注意累計次數）
  變化值：inst_f5_chg inst_t5_chg vol_trend_chg atr_pct_chg ret5_accel bias20_chg
          pos52_chg20 pe_chg20 dh_chg5(距60日高變化)
  市場情緒（長史 5 組件：動能/廣度/漲跌家數/波動/融資；對過去252日百分位再平均）：
          fg（0~100）、fg_chg5

條件生成：
  1) 每個數值特徵 × {q10,q20,q30,q70,q80,q90} 門檻 × 方向 → 單變量
  2) ~46 個二元謂詞兩兩 AND → 交叉
  合計 >1000。

護欄（多重檢定紀律）：
  挖掘窗 2021~2024 日層級評估（≥5檔/日、≥60有效日）；ATR 五分桶控波動；
  晉級=控波動 t≥4（Bonferroni p≈.05/1000）且 3 fold 控波動增量全>0；
  晉級者才跑 holdout 2025+（一次），依 holdout 控波動增量排序輸出。

用法：PYTHONIOENCODING=utf-8 python scripts/mega_mine.py
輸出：data/mega_mine_results.json + stdout 摘要
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

_BASE = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0]
sys.path.insert(0, _BASE)
sys.path.insert(0, _BASE + "/scripts")

import pop_condition_judge as judge  # noqa: E402

_OUT = _BASE + "/data/mega_mine_results.json"
_MINE_LO, _MINE_HI = judge._MINE_LO, judge._MINE_HI
_HOLD_LO = judge._HOLD_LO
_MIN_DAYS = 60          # 有效日下限
_MIN_PICKS = 5          # 每日最少選中檔數（沿判官）
_T_GATE = 4.0           # 晉級 t 門檻（~p<.05/1000）


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ─────────────────────────── 特徵擴充 ───────────────────────────


def build_features() -> pd.DataFrame:
    df = pd.read_pickle(judge._CACHE) if os.path.exists(judge._CACHE) else judge._build_cache()
    _log(f"快取 {len(df):,} 列；擴充特徵…")
    con = sqlite3.connect(judge._DB)

    if "sector_id" not in df.columns:
        sec_map = pd.read_sql_query("SELECT id AS stock_id, sector_id FROM stocks", con)
        df = df.merge(sec_map, on="stock_id", how="left")

    # ── 自身注意/處置（公告次日起算窗）──
    ev = pd.read_sql_query(
        "SELECT stock_id, date, kind, times FROM attention_listings", con)
    df = df.sort_values(["stock_id", "date"]).reset_index(drop=True)
    for kind, col in (("notice", "ev_n"), ("punish", "ev_p")):
        sub = ev[ev["kind"] == kind][["stock_id", "date"]].drop_duplicates()
        sub[col] = 1.0
        df = df.merge(sub, on=["stock_id", "date"], how="left")
        df[col] = df[col].fillna(0.0)
    g = df.groupby("stock_id", sort=False)
    df["att_notice5"] = (g["ev_n"].transform(lambda s: s.shift(1).rolling(5, 1).max()).fillna(0) > 0)
    df["att_punish10"] = (g["ev_p"].transform(lambda s: s.shift(1).rolling(10, 1).max()).fillna(0) > 0)
    # 最近一次注意的累計次數（5 日內；無=0）
    tsub = ev[ev["kind"] == "notice"][["stock_id", "date", "times"]].drop_duplicates(["stock_id", "date"], keep="last")
    df = df.merge(tsub.rename(columns={"times": "ev_times"}), on=["stock_id", "date"], how="left")
    g = df.groupby("stock_id", sort=False)
    df["att_times"] = g["ev_times"].transform(
        lambda s: s.shift(1).rolling(5, 1).max()).fillna(0.0)

    # ── 網絡層：類股/節點事件密度（不含自身）與鄰居噴發 ──
    _log("  網絡層（類股/節點傳染）…")
    ev_any = ev[["stock_id", "date"]].drop_duplicates().assign(e=1.0)
    # 類股密度：sector×date 事件數 → 5 日 rolling
    smap = df[["stock_id", "sector_id"]].drop_duplicates()
    ev_s = ev_any.merge(smap, on="stock_id", how="left").dropna(subset=["sector_id"])
    dates_all = np.sort(df["date"].unique())
    didx = {d: i for i, d in enumerate(dates_all)}

    def _density(events: pd.DataFrame, key: str) -> pd.DataFrame:
        """events[key,date,e] → 每 key×交易日 5 日事件數與Δ（以全交易日格對齊）。"""
        cnt = events.groupby([key, "date"])["e"].sum().reset_index()
        out = []
        for k, gg in cnt.groupby(key):
            arr = np.zeros(len(dates_all))
            for d_, v in zip(gg["date"], gg["e"]):
                i = didx.get(d_)
                if i is not None:
                    arr[i] = v
            r5 = pd.Series(arr).rolling(5, 1).sum().to_numpy()
            chg = r5 - np.concatenate([np.zeros(5), r5[:-5]])
            out.append(pd.DataFrame({key: k, "date": dates_all, "d5": r5, "dchg": chg}))
        return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=[key, "date", "d5", "dchg"])

    sd = _density(ev_s, "sector_id")
    df = df.merge(sd.rename(columns={"d5": "sec_att5", "dchg": "sec_att_chg"}),
                  on=["sector_id", "date"], how="left")
    df["sec_att5"] = (df["sec_att5"].fillna(0) - df["ev_n"] - df["ev_p"]).clip(lower=0)
    df["sec_att_chg"] = df["sec_att_chg"].fillna(0)

    # 節點密度（產業鏈；一股取第一節點）
    chain = pd.read_sql_query(
        "SELECT stock_id, node_id FROM industry_chain_members", con)
    chain = chain.drop_duplicates("stock_id")
    ev_c = ev_any.merge(chain, on="stock_id", how="inner")
    nd = _density(ev_c, "node_id")
    df = df.merge(chain, on="stock_id", how="left")
    df = df.merge(nd.rename(columns={"d5": "node_att5", "dchg": "node_att_chg"}),
                  on=["node_id", "date"], how="left")
    df["node_att5"] = (df["node_att5"].fillna(0) - df["ev_n"] - df["ev_p"]).clip(lower=0)
    df["node_att_chg"] = df["node_att_chg"].fillna(0)

    # 節點鄰居噴發率（不含自身）：node×date 的 (ret5>10) 平均以 leave-one-out 校正
    node_g = df.groupby(["node_id", "date"])
    surge = node_g["ret5"].transform(lambda s: (s > 10).sum())
    size = node_g["ret5"].transform("size")
    self_surge = (df["ret5"] > 10).astype(float)
    df["node_surge5"] = ((surge - self_surge) / (size - 1).clip(lower=1) * 100).where(size > 1)

    # ── 變化值族 ──
    _log("  變化值族…")
    g = df.groupby("stock_id", sort=False)
    df["inst_f5_chg"] = df["inst_f5"] - g["inst_f5"].shift(5)
    df["inst_t5_chg"] = df["inst_t5"] - g["inst_t5"].shift(5)
    df["vol_trend_chg"] = df["vol_trend"] - g["vol_trend"].shift(5)
    df["atr_pct_chg"] = df["atr_pct"] - g["atr_pct"].shift(5)
    df["ret5_accel"] = df["ret5"] - g["ret5"].shift(5)
    df["bias20_chg"] = df["bias_20"] - g["bias_20"].shift(5)
    df["pos52_chg20"] = df["pos_52w"] - g["pos_52w"].shift(20)
    df["pe_chg20"] = (df["pe"] / g["pe"].shift(20) - 1.0) * 100
    df["dh_chg5"] = df["dist_60d_high"] - g["dist_60d_high"].shift(5)

    # ── 市場情緒（長史 5 組件；全市場同日同值）──
    _log("  市場情緒（長史 5 組件）…")
    mkt = pd.read_sql_query("SELECT date, close FROM market_index ORDER BY date", con)
    breadth = pd.read_sql_query("""
        SELECT i.date d, AVG(CASE WHEN p.close > i.ma20 THEN 1.0 ELSE 0.0 END)*100 v
        FROM indicators i JOIN daily_prices p ON p.stock_id=i.stock_id AND p.date=i.date
        WHERE i.ma20 IS NOT NULL AND p.close IS NOT NULL GROUP BY i.date""", con)
    adv = pd.read_sql_query("""
        SELECT date d, AVG(up)*100 v FROM (
          SELECT date, CASE WHEN close > LAG(close) OVER (PARTITION BY stock_id ORDER BY date)
                 THEN 1.0 ELSE 0.0 END up
          FROM daily_prices WHERE close IS NOT NULL) GROUP BY date""", con)
    marg = pd.read_sql_query("""
        SELECT date d, SUM(margin_balance) v FROM margin
        WHERE margin_balance IS NOT NULL GROUP BY date""", con)
    con.close()

    def _srs(q: pd.DataFrame) -> pd.Series:
        s = pd.Series(q["v"].to_numpy(), index=pd.to_datetime(q["d"]))
        return s.sort_index()

    comps: dict[str, pd.Series] = {}
    if len(mkt) > 70:
        idxs = pd.Series(mkt["close"].to_numpy(), index=pd.to_datetime(mkt["date"])).sort_index()
        comps["momentum"] = idxs / idxs.rolling(60, min_periods=30).mean() - 1
        comps["volatility"] = -(idxs.pct_change().rolling(20, 15).std())
    comps["breadth"] = _srs(breadth)
    comps["advancers"] = _srs(adv).rolling(5, 3).mean()
    mg = _srs(marg)
    comps["margin"] = mg.pct_change(20) * 100

    ranks = []
    for s in comps.values():
        r = s.rolling(252, min_periods=60).rank(pct=True) * 100
        ranks.append(r)
    fg = pd.concat(ranks, axis=1).mean(axis=1, skipna=True)
    fgd = pd.DataFrame({"date": fg.index.strftime("%Y-%m-%d"), "fg": fg.values})
    fgd["fg_chg5"] = fgd["fg"] - fgd["fg"].shift(5)
    df["date_str"] = df["date"].astype(str)
    df = df.merge(fgd.rename(columns={"date": "date_str"}), on="date_str", how="left")
    df = df.drop(columns=["date_str", "ev_n", "ev_p", "ev_times"])

    _log(f"特徵擴充完成，共 {df.shape[1]} 欄")
    return df


# ─────────────────────────── 批次評估器 ───────────────────────────


class Evaluator:
    """向量化日層級評估：lift、ATR 桶控波動增量、MAE 代價、t 值。"""

    def __init__(self, df: pd.DataFrame):
        self.df = df.reset_index(drop=True)
        self.date_codes, self.dates = pd.factorize(self.df["date"], sort=True)
        self.n_dates = len(self.dates)
        day_base = self.df.groupby("date")["hit"].mean()
        self.day_base = day_base.reindex(self.dates).to_numpy()
        bkt = self.df.groupby(["date", "atr_bucket"])
        self.df["_bexp"] = bkt["hit"].transform("mean")
        self.df["_bmae"] = bkt["mae30"].transform("mean")
        self.hit = self.df["hit"].to_numpy()
        self.mae = self.df["mae30"].to_numpy()
        self.bexp = self.df["_bexp"].to_numpy()
        self.bmae = self.df["_bmae"].to_numpy()

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
        ctrl = d_ctrl[sel]
        maed = d_mae[sel]

        def _t(a: np.ndarray) -> float:
            a = a[~np.isnan(a)]
            if len(a) < 3:
                return float("nan")
            sd = a.std(ddof=1)
            return float(a.mean() / (sd / np.sqrt(len(a)))) if sd > 0 else float("nan")

        return {
            "days": n_days, "avg_picks": round(float(cnt[sel].mean()), 1),
            "hit": round(float(self.hit[idx].mean()) * 100, 1),
            "lift": round(float(np.nanmean(lift)) * 100, 2), "t_lift": round(_t(lift), 1),
            "ctrl": round(float(np.nanmean(ctrl)) * 100, 2), "t_ctrl": round(_t(ctrl), 1),
            "mae_cost": round(float(np.nanmean(maed)), 2),
        }


# ─────────────────────────── 條件生成 ───────────────────────────

# 數值特徵（單變量掃門檻用）
_NUMERIC = [
    # 網絡/事件/變化值（本輪新特徵）
    "sec_att5", "sec_att_chg", "node_att5", "node_att_chg", "node_surge5", "att_times",
    "inst_f5_chg", "inst_t5_chg", "vol_trend_chg", "atr_pct_chg", "ret5_accel",
    "bias20_chg", "pos52_chg20", "pe_chg20", "dh_chg5", "fg", "fg_chg5",
    # 既有代表特徵
    "atr_pct", "vol_ratio", "vol_trend", "c_over_ma20", "pos_52w", "dist_60d_high",
    "ret5", "ret20", "inst_f5", "inst_t5", "inst_tot10", "inst_streak",
    "sq_ratio", "short_chg5", "margin_chg5", "pe", "pb",
    "sec_ret20", "sec_breadth", "peer_surge5", "rel_ret20",
    "mkt_bias60", "mkt_ret20", "kd_k", "bias_20", "bias_60", "macd_hist",
]


# ─────────────────────────── 主流程 ───────────────────────────


def main() -> None:
    t0 = time.time()
    feat_cache = _BASE + "/data/mega_mine_features.pkl"
    if os.path.exists(feat_cache):
        df = pd.read_pickle(feat_cache)
        _log(f"載入特徵快取 {len(df):,} 列")
    else:
        df = build_features()
        df.to_pickle(feat_cache)

    mine = df[(df["date"].astype(str) >= _MINE_LO) & (df["date"].astype(str) <= _MINE_HI)].reset_index(drop=True)
    hold = df[df["date"].astype(str) >= _HOLD_LO].reset_index(drop=True)
    _log(f"挖掘窗 {len(mine):,} 列 / holdout {len(hold):,} 列；基率 {mine['hit'].mean()*100:.1f}%")

    conds = gen_conditions_fixed(mine, mine)
    _log(f"總條件數 {len(conds)}")

    ev_all = Evaluator(mine)
    # 3 fold evaluator
    dts = np.sort(mine["date"].unique())
    fold_evs = []
    for fi in range(3):
        fd = set(dts[fi * len(dts) // 3:(fi + 1) * len(dts) // 3])
        sub = mine[mine["date"].isin(fd)]
        fold_evs.append((Evaluator(sub), mine["date"].isin(fd).to_numpy()))

    results = []
    for k, (name, mask) in enumerate(conds):
        if k % 200 == 0:
            _log(f"  評估 {k}/{len(conds)}…")
        mask = np.asarray(mask, dtype=bool) & ~pd.isna(mine["hit"].to_numpy())
        r = ev_all.run(mask)
        if r is None or r["t_ctrl"] < _T_GATE or r["ctrl"] <= 0:
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
    _log(f"挖掘窗晉級 {len(results)} 條（t≥{_T_GATE}、3fold 控波動全正）")

    # holdout（晉級者一次）
    ev_h = Evaluator(hold)
    # holdout 遮罩：同一組條件（門檻＝挖掘窗分位數常數）套在 holdout 資料上
    _log("重建 holdout 遮罩…")
    hold_conds = gen_conditions_fixed(mine, hold)
    hmap = dict(hold_conds)
    out = []
    for r in results:
        hm = hmap.get(r["cond"])
        if hm is None:
            continue
        hr = ev_h.run(np.asarray(hm, dtype=bool))
        out.append({**r, "holdout": hr})
    out.sort(key=lambda x: -(x["holdout"]["ctrl"] if x["holdout"] else -99))

    with open(_OUT, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M"),
                   "n_conditions": len(conds), "n_survivors": len(results),
                   "results": out}, fh, ensure_ascii=False, indent=1)
    _log(f"完成（{time.time()-t0:.0f}s）→ {_OUT}")

    print(f"\n=== Top 30（依 holdout 控波動增量）；共 {len(out)} 條雙段存活 ===")
    print(f"{'條件':<58}{'挖掘ctrl':>9}{'t':>6}{'hold ctrl':>10}{'hold命中':>9}{'MAE代價':>8}")
    for r in out[:30]:
        h = r["holdout"] or {}
        print(f"{r['cond']:<58}{r['ctrl']:>8}pp{r['t_ctrl']:>6}"
              f"{(str(h.get('ctrl'))+'pp') if h else '—':>10}{h.get('hit','—'):>8}%"
              f"{h.get('mae_cost','—'):>8}")


_PRED_SPEC = [
    ("sec_att5", True), ("sec_att_chg", True), ("node_att5", True), ("node_att_chg", True),
    ("node_surge5", True), ("inst_f5_chg", True), ("inst_t5_chg", True),
    ("vol_trend_chg", True), ("ret5_accel", True), ("pos52_chg20", True),
    ("fg", False), ("fg_chg5", True), ("dh_chg5", True), ("atr_pct_chg", True),
    ("vol_ratio", True), ("vol_trend", True), ("inst_f5", True), ("inst_t5", True),
    ("inst_streak", True), ("sq_ratio", True), ("peer_surge5", True), ("sec_breadth", True),
    ("rel_ret20", True), ("pos_52w", True), ("atr_pct", True), ("c_over_ma20", True),
    ("mkt_bias60", True), ("ret20", False), ("bias_60", False), ("pe_chg20", True),
    ("margin_chg5", False), ("short_chg5", True), ("sec_ret20", True), ("dist_60d_high", True),
    ("kd_k", True), ("macd_hist", True), ("bias_20", False), ("pos52_chg20", False),
    ("fg_chg5", False), ("inst_f5_chg", False), ("node_surge5", False), ("mkt_ret20", False),
]


def build_predicates(mine: pd.DataFrame, target: pd.DataFrame) -> list[tuple[str, np.ndarray]]:
    """二元謂詞池（門檻=挖掘窗分位數，遮罩套 target）。供交叉與乾淨股探索重用。"""
    env_m: dict[str, np.ndarray] = {}
    env_t: dict[str, np.ndarray] = {}
    for f in _NUMERIC:
        if f not in mine.columns:
            continue
        env_m[f] = mine[f].to_numpy(dtype=float)
        env_t[f] = target[f].to_numpy(dtype=float)

    preds: list[tuple[str, np.ndarray]] = []

    def _p(name: str, mask_m: np.ndarray, mask_t: np.ndarray) -> None:
        share = float(np.nanmean(mask_m.astype(float)))
        if 0.005 <= share <= 0.6:
            preds.append((name, mask_t))

    _p("注意5日", mine["att_notice5"].to_numpy(bool), target["att_notice5"].to_numpy(bool))
    _p("處置10日", mine["att_punish10"].to_numpy(bool), target["att_punish10"].to_numpy(bool))
    _p("注意次數≥2", env_m.get("att_times", np.zeros(len(mine))) >= 2,
       env_t.get("att_times", np.zeros(len(target))) >= 2)
    for f, hi in _PRED_SPEC:
        if f not in env_m:
            continue
        if hi:
            q = np.nanquantile(env_m[f], 0.80)
            _p(f"{f}>q80", env_m[f] > q, env_t[f] > q)
        else:
            q = np.nanquantile(env_m[f], 0.20)
            _p(f"{f}<q20", env_m[f] < q, env_t[f] < q)
    _p("上升結構",
       (mine["c_over_ma20"].to_numpy(float) > 0) & mine["ma20_up5"].fillna(False).to_numpy(bool),
       (target["c_over_ma20"].to_numpy(float) > 0) & target["ma20_up5"].fillna(False).to_numpy(bool))

    seen: set[str] = set()
    uniq: list[tuple[str, np.ndarray]] = []
    for name, m in preds:
        if name not in seen:
            seen.add(name)
            uniq.append((name, m))
    return uniq


def gen_conditions_fixed(mine: pd.DataFrame, target: pd.DataFrame) -> list[tuple[str, np.ndarray]]:
    """與 gen_conditions 同名同門檻（分位數以挖掘窗計），但遮罩套在 target 上。"""
    conds: list[tuple[str, np.ndarray]] = []
    env_m: dict[str, np.ndarray] = {}
    env_t: dict[str, np.ndarray] = {}
    for f in _NUMERIC:
        if f not in mine.columns:
            continue
        env_m[f] = mine[f].to_numpy(dtype=float)
        env_t[f] = target[f].to_numpy(dtype=float)
    for f, vm in env_m.items():
        vt = env_t[f]
        qs = np.nanquantile(vm, [0.10, 0.20, 0.30, 0.70, 0.80, 0.90])
        for q, p in zip(qs[:3], ("q10", "q20", "q30")):
            conds.append((f"{f}<{p}({q:.3g})", vt < q))
        for q, p in zip(qs[3:], ("q70", "q80", "q90")):
            conds.append((f"{f}>{p}({q:.3g})", vt > q))

    uniq = build_predicates(mine, target)
    for i in range(len(uniq)):
        for j in range(i + 1, len(uniq)):
            conds.append((f"{uniq[i][0]} & {uniq[j][0]}", uniq[i][1] & uniq[j][1]))
    return conds


if __name__ == "__main__":
    main()
