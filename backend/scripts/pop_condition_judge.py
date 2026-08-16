"""候選條件標準判官：從答案反推的每個條件都用同一把尺判生死。

吃 forward_labels（backfill_forward_labels.py 先跑）＋ daily_prices/indicators，
對任意 PIT 條件式固定輸出：
  1. 逐日 lift vs 當天全市場基率（日層級 t）      ← 護欄2：不吃分年基率紅利
  2. ATR 五分桶控波動後的增量（日×桶配對，日層級 t）← 護欄3：先扣掉「高波動」假答案
  3. MAE 代價（選中 vs 同日同桶對照）              ← 護欄4：MFE/MAE 一起看
  4. 挖掘窗 2021-01-01 ~ 2024-12-31 內 3 段 fold    ← 護欄1：2025+ 是 holdout，
     預設鎖住；--holdout 才跑（整個挖掘過程只准最後看一次）

用法：
  python scripts/pop_condition_judge.py "<條件式>" [--holdout]
條件式=向量化布林運算，可用欄位（皆 T 日收盤 PIT）：
  open high low close volume ma5 ma10 ma20 ma60 ma120 ma240 vol_ma5 vol_ma20
  kd_k kd_d macd macd_signal macd_hist atr14 bias_20 bias_60
  atr_pct(=atr14/close) ma_align(0~3) ret5 ret20(%) vol_ratio(=volume/vol_ma20)
  vol_trend(=vol_ma5/vol_ma20) ma20_up5(月線比5日前高) c_over_ma20(=close/ma20-1)
  pos_52w(52週位置0~1) dist_60d_high(距60日高%)
  籌碼族: inst_f5/inst_t5(外資/投信5日買超佔量) inst_tot10(法人10日) inst_streak(連買日)
         sq_ratio(券資比%) short_chg5/margin_chg5(券/資5日增%) pe pb
  市場情境: mkt_bias60(大盤距季線%) mkt_ret20(大盤20日%)
  關聯性族: sec_ret20(類股20日動能中位%) sec_breadth(類股站上月線比%)
           peer_surge5(同類近5日噴>10%比例%) rel_ret20(個股-類股20日%)
  注意: 大戶(shareholding)僅2025-06+，不在挖掘窗，未納入。
例："(atr_pct > 0.05) & (c_over_ma20 > 0) & (ma20_up5)"
首跑會建快取 data/condition_judge_cache.pkl（~2 分鐘），之後秒載。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

_BASE = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0]
_DB = _BASE + "/data/twa.db"
_CACHE = _BASE + "/data/condition_judge_cache_v4.pkl"  # v4: +mfe10/mae10/hit10
_MINE_LO, _MINE_HI = "2021-01-01", "2024-12-31"
_HOLD_LO = "2025-01-01"
_MIN_PICKS_DAY = 5   # 當日選中 <5 檔的日子不進日層級統計（單檔噪音）


def _build_cache() -> pd.DataFrame:
    t0 = time.time()
    con = sqlite3.connect(_DB)
    print("建快取：join daily_prices + indicators + forward_labels …")
    df = pd.read_sql_query("""
        SELECT p.stock_id, p.date, p.open, p.high, p.low, p.close, p.volume,
               i.ma5, i.ma10, i.ma20, i.ma60, i.ma120, i.ma240,
               i.vol_ma5, i.vol_ma20, i.kd_k, i.kd_d,
               i.macd, i.macd_signal, i.macd_hist, i.atr14, i.bias_20, i.bias_60,
               f.mfe30, f.mae30, f.ret30, f.mfe10, f.mae10
        FROM forward_labels f
        JOIN daily_prices p ON p.stock_id = f.stock_id AND p.date = f.date
        LEFT JOIN indicators i ON i.stock_id = f.stock_id AND i.date = f.date
        ORDER BY p.stock_id, p.date""", con)
    con.close()
    df["atr_pct"] = df["atr14"] / df["close"]
    df["ma_align"] = ((df["ma5"] > df["ma10"]).astype(float)
                      + (df["ma10"] > df["ma20"]).astype(float)
                      + (df["ma20"] > df["ma60"]).astype(float))
    g = df.groupby("stock_id", sort=False)
    df["ret5"] = (df["close"] / g["close"].shift(5) - 1.0) * 100
    df["ret20"] = (df["close"] / g["close"].shift(20) - 1.0) * 100
    df["ma20_up5"] = df["ma20"] > g["ma20"].shift(5)
    df["vol_ratio"] = df["volume"] / df["vol_ma20"]
    df["vol_trend"] = df["vol_ma5"] / df["vol_ma20"]     # 量能趨勢（放量中/縮量中）
    df["c_over_ma20"] = df["close"] / df["ma20"] - 1.0
    df["hit"] = (df["mfe30"] >= 10.0).astype(float)
    df["hit10"] = (df["mfe10"] >= 10.0).astype(float)  # 10日碰到率（2026-08 主目標）
    # 位置結構
    df["pos_52w"] = ((df["close"] - g["low"].transform(lambda s: s.rolling(240, 60).min()))
                     / (g["high"].transform(lambda s: s.rolling(240, 60).max())
                        - g["low"].transform(lambda s: s.rolling(240, 60).min()) + 1e-9))
    df["dist_60d_high"] = (df["close"] / g["high"].transform(lambda s: s.rolling(60, 20).max()) - 1.0) * 100

    # ── 籌碼族 ──
    print("  籌碼族（法人/融資券）…")
    con = sqlite3.connect(_DB)
    inst = pd.read_sql_query(
        "SELECT stock_id, date, foreign_net, trust_net, total_net FROM institutional", con)
    marg = pd.read_sql_query(
        "SELECT stock_id, date, margin_balance, short_balance FROM margin", con)
    val = pd.read_sql_query("SELECT stock_id, date, pe, pb FROM valuation", con)
    con.close()
    df = df.merge(inst, on=["stock_id", "date"], how="left")
    df = df.merge(marg, on=["stock_id", "date"], how="left")
    df = df.merge(val, on=["stock_id", "date"], how="left")
    g = df.groupby("stock_id", sort=False)
    for c in ("foreign_net", "trust_net", "total_net"):
        df[c] = df[c].fillna(0.0)
    for c in ("margin_balance", "short_balance", "pe", "pb"):
        df[c] = g[c].ffill()
    g = df.groupby("stock_id", sort=False)
    volsum5 = g["volume"].transform(lambda s: s.rolling(5, 3).sum())
    volsum10 = g["volume"].transform(lambda s: s.rolling(10, 5).sum())
    # 法人買賣佔量比（5/10日累計；正=買超）
    df["inst_f5"] = g["foreign_net"].transform(lambda s: s.rolling(5, 3).sum()) / (volsum5 + 1e-9)
    df["inst_t5"] = g["trust_net"].transform(lambda s: s.rolling(5, 3).sum()) / (volsum5 + 1e-9)
    df["inst_tot10"] = g["total_net"].transform(lambda s: s.rolling(10, 5).sum()) / (volsum10 + 1e-9)
    # 法人連買天數
    pos = (df["total_net"] > 0)
    df["inst_streak"] = pos.groupby([df["stock_id"], (~pos).groupby(df["stock_id"]).cumsum()]).cumsum()
    # 軋空族：券資比、券增、資增（5日變化相對餘額）
    df["sq_ratio"] = (df["short_balance"] / (df["margin_balance"] + 1e-9)).clip(0, 5) * 100
    df["short_chg5"] = ((df["short_balance"] - g["short_balance"].shift(5))
                        / (g["short_balance"].shift(5) + 1e-9)).clip(-2, 5) * 100
    df["margin_chg5"] = ((df["margin_balance"] - g["margin_balance"].shift(5))
                         / (g["margin_balance"].shift(5) + 1e-9)).clip(-2, 5) * 100
    df = df.drop(columns=["foreign_net", "trust_net", "total_net",
                          "margin_balance", "short_balance"])

    # 市場情境（同日全股相同）
    con = sqlite3.connect(_DB)
    mkt = pd.read_sql_query("SELECT date, close AS mkt_close FROM market_index ORDER BY date", con)
    sec_map = pd.read_sql_query("SELECT id AS stock_id, sector_id FROM stocks", con)
    con.close()
    mkt["mkt_bias60"] = (mkt["mkt_close"] / mkt["mkt_close"].rolling(60).mean() - 1.0) * 100
    mkt["mkt_ret20"] = (mkt["mkt_close"] / mkt["mkt_close"].shift(20) - 1.0) * 100
    df = df.merge(mkt[["date", "mkt_bias60", "mkt_ret20"]], on="date", how="left")

    # ── 關聯性族：類股情境（由成分股當場重算，PIT；成分 <5 檔的類股不給值）──
    print("  關聯性族（類股動能/廣度/鄰居效應）…")
    df = df.merge(sec_map, on="stock_id", how="left")
    gs = df.groupby(["sector_id", "date"])
    sec = gs.agg(sec_ret20=("ret20", "median"),
                 sec_breadth=("c_over_ma20", lambda s: (s > 0).mean()),
                 peer_surge5=("ret5", lambda s: (s > 10).mean()),
                 sec_n=("ret20", "size")).reset_index()
    sec.loc[sec["sec_n"] < 5, ["sec_ret20", "sec_breadth", "peer_surge5"]] = np.nan
    df = df.merge(sec.drop(columns=["sec_n"]), on=["sector_id", "date"], how="left")
    df["rel_ret20"] = df["ret20"] - df["sec_ret20"]   # 個股相對類股強弱
    df["sec_breadth"] = df["sec_breadth"] * 100
    df["peer_surge5"] = df["peer_surge5"] * 100
    # 當日 ATR 五分桶（全市場，控波動用）
    df["atr_bucket"] = (df.groupby("date")["atr_pct"]
                        .transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False)))
    for c in df.columns:   # float32 省一半磁碟（研究精度足夠）
        if df[c].dtype == np.float64:
            df[c] = df[c].astype(np.float32)
    df.to_pickle(_CACHE)
    print(f"快取完成 {len(df):,} 列，{time.time()-t0:.0f}s → {_CACHE}")
    return df


def _daily_stats(df: pd.DataFrame, mask: pd.Series, label: str) -> None:
    """日層級：lift vs 當天基率、ATR 桶配對增量、MAE 代價。"""
    sel = df[mask]
    if sel.empty:
        print(f"  [{label}] 條件選不到任何列")
        return
    # 當天全市場基率 / 桶基率（含選中列本身；選中占比小時偏差可忽略且方向保守）
    day_base = df.groupby("date")["hit"].mean()
    bkt_base = df.groupby(["date", "atr_bucket"])["hit"].mean()
    bkt_mae = df.groupby(["date", "atr_bucket"])["mae30"].mean()

    by_day = sel.groupby("date")
    days = by_day.size()
    days = days[days >= _MIN_PICKS_DAY]
    if len(days) < 30:
        print(f"  [{label}] 有效日僅 {len(days)}（≥{_MIN_PICKS_DAY}檔/日），樣本太薄不評")
        return
    sel_ok = sel[sel["date"].isin(days.index)]
    d_hit = sel_ok.groupby("date")["hit"].mean()
    d_lift = d_hit - day_base.loc[d_hit.index]
    # 桶配對期望
    exp_hit = sel_ok.join(bkt_base.rename("bexp"), on=["date", "atr_bucket"])["bexp"]
    d_ctrl = (sel_ok.assign(delta=sel_ok["hit"] - exp_hit.values)
              .groupby("date")["delta"].mean())
    exp_mae = sel_ok.join(bkt_mae.rename("bmae"), on=["date", "atr_bucket"])["bmae"]
    d_mae = (sel_ok.assign(dm=sel_ok["mae30"] - exp_mae.values)
             .groupby("date")["dm"].mean())

    def _t(s: pd.Series) -> float:
        return float(s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))) if len(s) > 2 else float("nan")

    print(f"  [{label}] 日={len(days)}  日均選中{days.mean():.1f}檔  "
          f"選中命中={sel_ok['hit'].mean()*100:.1f}%")
    print(f"    vs當日基率 lift={d_lift.mean()*100:+5.1f}pp (t={_t(d_lift):+.1f})   "
          f"控波動增量={d_ctrl.mean()*100:+5.1f}pp (t={_t(d_ctrl):+.1f})   "
          f"MAE代價={d_mae.mean():+5.1f}pp (avgMAE={sel_ok['mae30'].mean():+.1f}%)")


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--holdout"]
    holdout = "--holdout" in sys.argv
    if not args:
        print(__doc__)
        sys.exit(1)
    expr = args[0]

    df = pd.read_pickle(_CACHE) if os.path.exists(_CACHE) else _build_cache()
    mine = df[(df["date"] >= _MINE_LO) & (df["date"] <= _MINE_HI)]
    print(f"條件式：{expr}")
    print(f"挖掘窗 {_MINE_LO}~{_MINE_HI}（{mine['date'].nunique()} 日，"
          f"全市場基率 {mine['hit'].mean()*100:.1f}%）\n")

    env = {c: mine[c] for c in mine.columns}
    try:
        mask = pd.Series(eval(expr, {"np": np, "__builtins__": {}}, env), index=mine.index).fillna(False).astype(bool)
    except Exception as e:  # noqa: BLE001
        print(f"條件式解析失敗：{e}")
        sys.exit(1)

    print("=== 挖掘窗全期 ===")
    _daily_stats(mine, mask, "2021~2024")

    print("\n=== 3 段 fold（挖掘窗內）===")
    dts = sorted(mine["date"].unique())
    for fi in range(3):
        fd = set(dts[fi * len(dts) // 3:(fi + 1) * len(dts) // 3])
        fm = mine["date"].isin(fd)
        _daily_stats(mine[fm], mask[fm], f"fold{fi+1} {min(fd)}~{max(fd)}")

    if holdout:
        print("\n=== ⚠️ HOLDOUT 2025+（整個挖掘過程只准看這一次）===")
        hold = df[df["date"] >= _HOLD_LO]
        envh = {c: hold[c] for c in hold.columns}
        maskh = pd.Series(eval(expr, {"np": np, "__builtins__": {}}, envh), index=hold.index).fillna(False).astype(bool)
        _daily_stats(hold, maskh, f"{_HOLD_LO}~{hold['date'].max()}")
    else:
        print("\n(holdout 2025+ 已鎖，定稿後加 --holdout 看最後一眼)")


if __name__ == "__main__":
    main()
