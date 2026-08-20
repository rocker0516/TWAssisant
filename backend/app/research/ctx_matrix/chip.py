"""籌碼家族特徵組裝（spec §5 A~F）。

資料表：Institutional / Margin / ShareholdingDistribution(shareholding) /
ShortLending(short_lending) / DayTrading(day_trading) / InsiderHolding。

防前視紀律：
  - 三大法人／融資融券／借券／當沖為日頻表，PK=(stock_id, date)，比照既有
    `flow_engine` 讀法視為當日即可用，不另位移。
  - 集保戶股權分散（週頻）與董監持股（月頻）表內只有「資料日期/年月」，
    沒有公布日欄位。防前視假設（已於 brief 明確要求並在此標明）：
      * 集保週頻：以「資料日 + 3 個日曆日」視為可用日（TDCC 週報慣例延遲）。
      * 董監月頻：以「資料月月底 + 10 個日曆日」視為可用日（依公開資訊觀測站
        月報慣例延遲）。
    forward-fill 一律用 `avail_date`（可用日）對齊到日頻，而非原始資料日，
    避免用「未來才公布的籌碼變化」推論「過去的訊號」。
  - ETF（股號 `00` 開頭 / `stocks.is_etf`）不入標的池。
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from .templates import Template

# 一年交易日視窗（自身時序分位數用，如 squeeze_flag 券資比 q80、insider_buyback 價格 q30）
_YEAR_WIN = 252
_YEAR_MIN = 60


def _streak(mask: pd.Series) -> pd.Series:
    """止於當列的連續 True 天數（同 templates._consec_pos 的手法，遇 False 重置）。"""
    m = mask.astype(int)
    grp = (m == 0).cumsum()
    return m.groupby(grp).cumsum()


def _sector_pct(df: pd.DataFrame, col: str) -> pd.Series:
    """類股內橫斷面百分位（同日、同 sector_id 分組排名 0~1）。"""
    return df.groupby(["date", "sector_id"], sort=False)[col].rank(pct=True)


def _read(con: sqlite3.Connection, sql: str, cols: list[str]) -> pd.DataFrame:
    df = pd.read_sql_query(sql, con)
    for c in cols:
        if c not in df.columns:
            df[c] = np.nan
    return df


def chip_feature_frame(db_path: str) -> pd.DataFrame:
    """從 SQLite 組出日頻籌碼家族特徵 frame。"""
    con = sqlite3.connect(db_path)
    try:
        stocks = pd.read_sql_query(
            "SELECT id AS stock_id, sector_id, is_etf FROM stocks", con
        )
        pool = stocks[
            (stocks["is_etf"] != 1) & (~stocks["stock_id"].astype(str).str.startswith("00"))
        ][["stock_id", "sector_id"]]

        prices = pd.read_sql_query(
            "SELECT stock_id, date, close, volume FROM daily_prices", con
        )
        inst = _read(
            con,
            "SELECT stock_id, date, foreign_net, trust_net, total_net FROM institutional",
            ["foreign_net", "trust_net", "total_net"],
        )
        margin = _read(
            con,
            "SELECT stock_id, date, margin_balance, margin_change, short_balance FROM margin",
            ["margin_balance", "margin_change", "short_balance"],
        )
        sbl = _read(
            con,
            "SELECT stock_id, date, sbl_balance FROM short_lending",
            ["sbl_balance"],
        )
        dtr = _read(
            con,
            "SELECT stock_id, date, dt_volume FROM day_trading",
            ["dt_volume"],
        )
        share = _read(
            con,
            "SELECT stock_id, date, big_pct, over1000_pct, small_pct, holders FROM shareholding",
            ["big_pct", "over1000_pct", "small_pct", "holders"],
        )
        insider = _read(
            con,
            "SELECT stock_id, year, month, director_shares FROM insider_holding",
            ["director_shares"],
        )
    finally:
        con.close()

    if prices.empty:
        return pd.DataFrame(columns=[
            "stock_id", "date", "foreign_net", "trust_net", "dual_net_flag",
            "netbuy_turnover_pct", "netbuy_accel", "trust_adopt_flag", "sell_exhaust_flag",
            "absorb_flag", "receive_flag", "margin_chg5", "margin_chg10", "margin_chg20",
            "margin_down_price_up", "short_margin_ratio_pct", "squeeze_flag", "washout_flag",
            "big_holder_wk_up", "retail_cnt_chg", "conc_diff_chg", "mid_holder_up",
            "big_absorb_flag", "sbl_chg5", "sbl_chg10", "sbl_chg20", "short_cover_flag",
            "daytrade_pct", "daytrade_drop_flag", "insider_chg", "insider_buyback_flag",
            "distribute_warn_flag",
        ])

    # ── 基底：股票池 × 日頻收盤/成交量 ──
    prices["date"] = pd.to_datetime(prices["date"])
    base = prices.merge(pool, on="stock_id", how="inner").sort_values(["stock_id", "date"])
    base = base.reset_index(drop=True)

    def _merge_daily(df: pd.DataFrame, cols: list[str]) -> None:
        nonlocal base
        if df.empty:
            for c in cols:
                base[c] = np.nan
            return
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        base = base.merge(df[["stock_id", "date", *cols]], on=["stock_id", "date"], how="left")

    _merge_daily(inst, ["foreign_net", "trust_net", "total_net"])
    _merge_daily(margin, ["margin_balance", "margin_change", "short_balance"])
    _merge_daily(sbl, ["sbl_balance"])
    _merge_daily(dtr, ["dt_volume"])

    base = base.sort_values(["stock_id", "date"]).reset_index(drop=True)
    g = base.groupby("stock_id", sort=False)

    # ── A. 三大法人（日頻） ──
    base["foreign_net"] = base["foreign_net"].astype(float)
    base["trust_net"] = base["trust_net"].astype(float)
    total_net = base["total_net"].astype(float)

    foreign_sum5 = g["foreign_net"].transform(lambda s: s.rolling(5, min_periods=5).sum())
    trust_sum5 = g["trust_net"].transform(lambda s: s.rolling(5, min_periods=5).sum())
    base["dual_net_flag"] = (foreign_sum5 > 0) & (trust_sum5 > 0)

    vol5 = g["volume"].transform(lambda s: s.rolling(5, min_periods=5).sum())
    total_sum5 = g["total_net"].transform(lambda s: s.rolling(5, min_periods=5).sum())
    turnover_raw = total_sum5 / vol5.replace(0, np.nan)
    base["_turnover_raw"] = turnover_raw
    base["netbuy_turnover_pct"] = _sector_pct(base, "_turnover_raw")

    total_sum5_prev = g["total_net"].transform(
        lambda s: s.rolling(5, min_periods=5).sum().shift(5)
    )
    base["netbuy_accel"] = total_sum5 - total_sum5_prev

    # A6 投信認養：過去 60 日（不含近 3 日）無投信淨買超 → 首度連買滿 3 日
    trust_pos_streak = g["trust_net"].transform(lambda s: _streak(s > 0))
    trust_had_buy_60 = g["trust_net"].transform(
        lambda s: (s > 0).rolling(60, min_periods=1).sum().shift(3)
    )
    base["trust_adopt_flag"] = (trust_pos_streak == 3) & (trust_had_buy_60.fillna(0) == 0)

    # A7 賣壓竭盡：連賣≥5 日後首度翻買
    total_neg_streak = g["total_net"].transform(lambda s: _streak(s < 0))
    total_neg_streak_prev = g["total_net"].transform(lambda s: _streak(s < 0).shift(1))
    base["sell_exhaust_flag"] = (total_neg_streak_prev >= 5) & (total_net > 0)

    # A8/A9：連買/連賣≥5日 × 同期股價漲幅背離類股中位
    total_pos_streak = g["total_net"].transform(lambda s: _streak(s > 0))
    price_chg5 = g["close"].transform(lambda s: s.pct_change(5, fill_method=None) * 100)
    base["_price_chg5"] = price_chg5
    sector_med_chg5 = base.groupby(["date", "sector_id"], sort=False)["_price_chg5"].transform("median")
    base["absorb_flag"] = (total_pos_streak >= 5) & (price_chg5 < sector_med_chg5)
    base["receive_flag"] = (total_neg_streak >= 5) & (price_chg5 >= sector_med_chg5)

    # ── B. 融資融券（日頻） ──
    margin_balance = base["margin_balance"].astype(float)
    base["margin_chg5"] = g["margin_balance"].transform(lambda s: s.pct_change(5, fill_method=None) * 100)
    base["margin_chg10"] = g["margin_balance"].transform(lambda s: s.pct_change(10, fill_method=None) * 100)
    base["margin_chg20"] = g["margin_balance"].transform(lambda s: s.pct_change(20, fill_method=None) * 100)

    daily_ret = g["close"].transform(lambda s: s.pct_change(fill_method=None))
    margin_bal_prev = g["margin_balance"].transform(lambda s: s.shift(1))
    margin_1d_pct = base["margin_change"].astype(float) / margin_bal_prev.replace(0, np.nan)
    base["margin_down_price_up"] = (base["margin_change"].astype(float) < 0) & (daily_ret > 0)

    sq_raw = base["short_balance"].astype(float) / margin_balance.replace(0, np.nan)
    base["_sq_raw"] = sq_raw
    base["short_margin_ratio_pct"] = _sector_pct(base, "_sq_raw")
    sq_self_q80 = g["_sq_raw"].transform(
        lambda s: s.rolling(_YEAR_WIN, min_periods=_YEAR_MIN).quantile(0.8)
    )
    close_20high = g["close"].transform(lambda s: s.rolling(20, min_periods=20).max())
    base["squeeze_flag"] = (sq_raw >= sq_self_q80) & (base["close"] >= close_20high)

    base["washout_flag"] = (daily_ret <= -0.05) & (margin_1d_pct <= -0.02)

    # ── D. 借券／當沖 ──
    base["sbl_chg5"] = g["sbl_balance"].transform(lambda s: s.pct_change(5, fill_method=None) * 100)
    base["sbl_chg10"] = g["sbl_balance"].transform(lambda s: s.pct_change(10, fill_method=None) * 100)
    base["sbl_chg20"] = g["sbl_balance"].transform(lambda s: s.pct_change(20, fill_method=None) * 100)
    # 空單回補：借券餘額自 60 日高點回落 > 20%（門檻屬特徵工程假設，已於註解標明）
    sbl_max60 = g["sbl_balance"].transform(lambda s: s.rolling(60, min_periods=20).max())
    base["short_cover_flag"] = base["sbl_balance"].astype(float) <= sbl_max60 * 0.8

    vol_lots = base["volume"].astype(float) / 1000.0  # 成交股數 → 張，對齊 dt_volume 單位
    dt_raw = base["dt_volume"].astype(float) / vol_lots.replace(0, np.nan)
    base["_dt_raw"] = dt_raw
    base["daytrade_pct"] = _sector_pct(base, "_dt_raw")
    dt_max20 = g["_dt_raw"].transform(lambda s: s.rolling(20, min_periods=10).max())
    # 當沖比自 20 日高檔驟降超過一半（門檻屬特徵工程假設，已於註解標明）
    base["daytrade_drop_flag"] = base["_dt_raw"] <= dt_max20 * 0.5

    # ── E. 董監（月頻）— insider_buyback 用的一年自身價格 q30 ──
    price_q30 = g["close"].transform(
        lambda s: s.rolling(_YEAR_WIN, min_periods=_YEAR_MIN).quantile(0.3)
    )

    # ── C. 集保級距（週頻）— forward-fill 以「資料日+3 曆日」為可用日 ──
    if not share.empty:
        share = share.merge(pool, on="stock_id", how="inner")
        share["date"] = pd.to_datetime(share["date"])
        share = share.sort_values(["stock_id", "date"]).reset_index(drop=True)
        sg = share.groupby("stock_id", sort=False)
        big_up_streak = sg["big_pct"].transform(lambda s: _streak(s.diff() > 0))
        share["big_holder_wk_up"] = big_up_streak >= 3
        share["big_down"] = sg["big_pct"].transform(lambda s: s.diff() < 0)
        share["retail_cnt_chg"] = sg["holders"].transform(lambda s: s.pct_change(fill_method=None) * 100)
        conc = share["over1000_pct"].astype(float) - share["small_pct"].astype(float)
        share["_conc"] = conc
        share["conc_diff_chg"] = share.groupby("stock_id", sort=False)["_conc"].transform(lambda s: s.diff())
        # 100~400 張級距 DB 無此細級距，以 400~1000 張(big_pct−over1000_pct)近似「中實戶」，
        # 與 spec §5 C4 字面定義（100~400 張）不同，屬資料限制下的替代口徑，已於此標明。
        mid = share["big_pct"].astype(float) - share["over1000_pct"].astype(float)
        share["_mid"] = mid
        share["mid_holder_up"] = share.groupby("stock_id", sort=False)["_mid"].transform(
            lambda s: s.diff() > 0
        )
        share["avail_date"] = share["date"] + pd.Timedelta(days=3)

        share_cols = [
            "big_holder_wk_up", "big_down", "retail_cnt_chg", "conc_diff_chg", "mid_holder_up",
        ]
        share_ff = share[["stock_id", "avail_date", *share_cols]].sort_values(
            ["avail_date", "stock_id"]
        )
        base = base.sort_values(["date", "stock_id"]).reset_index(drop=True)
        base = pd.merge_asof(
            base, share_ff, left_on="date", right_on="avail_date", by="stock_id", direction="backward"
        ).drop(columns=["avail_date"])
    else:
        for c in ("big_holder_wk_up", "big_down", "retail_cnt_chg", "conc_diff_chg", "mid_holder_up"):
            base[c] = np.nan

    # ── E. 董監（月頻）— forward-fill 以「資料月月底+10 曆日」為可用日 ──
    if not insider.empty:
        insider = insider.merge(pool, on="stock_id", how="inner")
        insider = insider.sort_values(["stock_id", "year", "month"]).reset_index(drop=True)
        insider["insider_chg"] = insider.groupby("stock_id", sort=False)["director_shares"].transform(
            lambda s: s.pct_change(fill_method=None) * 100
        )
        month_start = pd.to_datetime(
            dict(year=insider["year"], month=insider["month"], day=1)
        )
        insider["avail_date"] = month_start + pd.offsets.MonthEnd(0) + pd.Timedelta(days=10)
        insider_ff = insider[["stock_id", "avail_date", "insider_chg"]].sort_values(
            ["avail_date", "stock_id"]
        )
        base = base.sort_values(["date", "stock_id"]).reset_index(drop=True)
        base = pd.merge_asof(
            base, insider_ff, left_on="date", right_on="avail_date", by="stock_id", direction="backward"
        ).drop(columns=["avail_date"])
    else:
        base["insider_chg"] = np.nan

    base = base.sort_values(["stock_id", "date"]).reset_index(drop=True)

    # ── F. 複合型態（跨表） ──
    base["big_absorb_flag"] = base["big_holder_wk_up"].astype("boolean").fillna(False) & (
        base["_price_chg5"] < base.groupby(["date", "sector_id"], sort=False)["_price_chg5"].transform("median")
    )
    base["insider_buyback_flag"] = (base["close"] <= price_q30) & (base["insider_chg"].fillna(0) > 0)

    margin_chg5 = base["margin_chg5"]
    inst_sum5_sell = g["total_net"].transform(lambda s: s.rolling(5, min_periods=5).sum()) < 0
    base["distribute_warn_flag"] = (
        base["big_down"].astype("boolean").fillna(False)
        & (base["retail_cnt_chg"].fillna(0) > 0)
        & (margin_chg5 >= 5)
        & inst_sum5_sell
    )

    keep = [
        "stock_id", "date", "sector_id",
        "foreign_net", "trust_net", "dual_net_flag",
        "netbuy_turnover_pct", "netbuy_accel", "trust_adopt_flag", "sell_exhaust_flag",
        "absorb_flag", "receive_flag", "margin_chg5", "margin_chg10", "margin_chg20",
        "margin_down_price_up", "short_margin_ratio_pct", "squeeze_flag", "washout_flag",
        "big_holder_wk_up", "retail_cnt_chg", "conc_diff_chg", "mid_holder_up",
        "big_absorb_flag", "sbl_chg5", "sbl_chg10", "sbl_chg20", "short_cover_flag",
        "daytrade_pct", "daytrade_drop_flag", "insider_chg", "insider_buyback_flag",
        "distribute_warn_flag",
    ]
    out = base[keep].copy()
    bool_cols = [
        "dual_net_flag", "trust_adopt_flag", "sell_exhaust_flag", "absorb_flag", "receive_flag",
        "margin_down_price_up", "squeeze_flag", "washout_flag", "big_holder_wk_up",
        "mid_holder_up", "big_absorb_flag", "short_cover_flag", "daytrade_drop_flag",
        "insider_buyback_flag", "distribute_warn_flag",
    ]
    for c in bool_cols:
        out[c] = out[c].astype("boolean").fillna(False).astype(bool)
    return out


def chip_templates() -> list[Template]:
    """spec §5 A1~F4 模板清單（含參數格），對映 chip_feature_frame 欄位。"""
    q_grid = [0.7, 0.8, 0.9]
    return [
        # A. 三大法人
        Template("A1", "chip", "foreign_net", "consec_ge", {"n": [3, 5, 10, 15]}),
        Template("A2", "chip", "trust_net", "consec_ge", {"n": [3, 5, 10]}),
        Template("A3", "chip", "dual_net_flag", "flag", {}),
        Template("A4", "chip", "netbuy_turnover_pct", "q_hi", {"q": q_grid}),
        Template("A5", "chip", "netbuy_accel", "q_hi", {"q": q_grid}),
        Template("A6", "chip", "trust_adopt_flag", "flag", {}),
        Template("A7", "chip", "sell_exhaust_flag", "flag", {}),
        Template("A8", "chip", "absorb_flag", "flag", {}),
        Template("A9", "chip", "receive_flag", "flag", {}),
        # B. 融資融券
        Template("B1_n5", "chip", "margin_chg5", "q_hi", {"q": q_grid}),
        Template("B1_n10", "chip", "margin_chg10", "q_hi", {"q": q_grid}),
        Template("B1_n20", "chip", "margin_chg20", "q_hi", {"q": q_grid}),
        Template("B2", "chip", "margin_down_price_up", "flag", {}),
        Template("B3", "chip", "short_margin_ratio_pct", "q_hi", {"q": q_grid}),
        Template("B4", "chip", "squeeze_flag", "flag", {}),
        Template("B5", "chip", "washout_flag", "flag", {}),
        # C. 集保級距
        Template("C1", "chip", "big_holder_wk_up", "flag", {}),
        Template("C2", "chip", "retail_cnt_chg", "q_hi", {"q": q_grid}),
        Template("C3", "chip", "conc_diff_chg", "q_hi", {"q": q_grid}),
        Template("C4", "chip", "mid_holder_up", "flag", {}),
        Template("C5", "chip", "big_absorb_flag", "flag", {}),
        # D. 借券／當沖
        Template("D1_n5", "chip", "sbl_chg5", "q_hi", {"q": q_grid}),
        Template("D1_n10", "chip", "sbl_chg10", "q_hi", {"q": q_grid}),
        Template("D1_n20", "chip", "sbl_chg20", "q_hi", {"q": q_grid}),
        Template("D2", "chip", "short_cover_flag", "flag", {}),
        Template("D3", "chip", "daytrade_pct", "q_hi", {"q": q_grid}),
        Template("D4", "chip", "daytrade_drop_flag", "flag", {}),
        # E. 董監
        Template("E1", "chip", "insider_chg", "q_hi", {"q": q_grid}),
        Template("E2", "chip", "insider_buyback_flag", "flag", {}),
        # F. 複合型態（跨表；F1~F3 因 DB 無對應細分級距，以既有最接近的複合旗標近似，
        # 已於 chip.py 模組註解／task-3-report 標明此簡化）
        Template("F1", "chip", "absorb_flag", "flag", {}),
        Template("F2", "chip", "big_absorb_flag", "flag", {}),
        Template("F3", "chip", "squeeze_flag", "flag", {}),
        Template("F4", "chip", "distribute_warn_flag", "flag", {}),
    ]
