"""動能/量能、基本面、消息、事件四家族特徵組裝（context-routing-matrix task-4）。

資料表：daily_prices / indicators / stocks / company_profile /
revenue_monthly / financials_quarterly / scores(track="long") /
attention_listings / events / etf_index_events。

防前視紀律（比照 chip.py 的風格與紀律）：
  - 技術欄優先讀 `indicators` 現成值（bias_20 等），不重算。
  - 月營收（revenue_monthly）表內只有「年/月」、無公布日欄位。防前視假設
    （已於 brief 明確要求並在此標明）：以「資料月月底 + 10 個日曆日」視為
    可用日（比照 TWSE/TPEx 月報公告慣例延遲，同 chip.py 董監月頻的做法）。
  - 季財報（financials_quarterly）同樣只有「年/季」、無公布日欄位。防前視
    假設（已於 brief 明確要求並在此標明）：以「季底 + 45 個日曆日」視為
    可用日（比照公開資訊觀測站財報法定申報期限，寬鬆估計）。
  - forward-fill 一律用 `avail_date`（可用日）對齊到日頻，用
    `merge_asof(direction="backward")`，避免用「未來才公布的基本面」推論
    「過去的訊號」。
  - Score 表（track="long"）本身即日頻（每個交易日皆有一列，逐日評分），
    直接以 (stock_id, date) 對齊，不位移。
  - ETF（股號 `00` 開頭 / `stocks.is_etf`）不入標的池，比照 chip.py。
  - `rel_ret20` 用「個股20日報酬 − 該股所屬類股（sector_id）內等權平均20日
    報酬」的中性化口徑（比照 `mega_mine.add_sector_neutral` 的相對化思路），
    不採用 `corners.py` 裡已被稽核判定「市場成分過重、方向翻號」的
    `sec_ret20`（類股 ret20 中位數，稽核見 mega_mine2.py:322-324）。
  - `rev_yoy_chg` 與 `margin_chg5` 是既有稽核的 `_FLAGGED` 名單（衰減>60%
    但同號，見 mega_mine2.py:326）——本層照樣入池，Task 6 pipeline 稽核會
    重新判定去留。
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from .templates import Template

_Q_HI = [0.7, 0.8, 0.9]
_Q_LO = [0.1, 0.2, 0.3]

_EMPTY_COLS = [
    "stock_id", "date", "sector_id",
    "ret5", "ret20", "bias20", "break20_flag", "vol_ratio5", "turnover_pct", "rel_ret20",
    "rev_yoy", "rev_yoy_chg", "rev_yoy_accel", "rev_mom", "eps_qoq", "score_chg20",
    "attn_flag", "attn_cnt20", "news_cnt5_pct",
    "etf_add_win_flag", "etf_del_win_flag",
]


def _read(con: sqlite3.Connection, sql: str, cols: list[str]) -> pd.DataFrame:
    df = pd.read_sql_query(sql, con)
    for c in cols:
        if c not in df.columns:
            df[c] = np.nan
    return df


def other_feature_frame(db_path: str) -> pd.DataFrame:
    """從 SQLite 組出日頻「動能/量能、基本面、消息、事件」四家族特徵 frame。"""
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
        ind = _read(
            con,
            "SELECT stock_id, date, bias_20 FROM indicators",
            ["bias_20"],
        )
        profile = _read(
            con,
            "SELECT stock_id, issued_shares FROM company_profile",
            ["issued_shares"],
        )
        revenue = _read(
            con,
            "SELECT stock_id, year, month, yoy, mom FROM revenue_monthly",
            ["yoy", "mom"],
        )
        fin = _read(
            con,
            "SELECT stock_id, year, quarter, eps FROM financials_quarterly",
            ["eps"],
        )
        score = _read(
            con,
            "SELECT stock_id, date, total_score FROM scores WHERE track='long'",
            ["total_score"],
        )
        attn = _read(
            con,
            "SELECT stock_id, date FROM attention_listings",
            [],
        )
        events = _read(
            con,
            "SELECT stock_id, date FROM events WHERE stock_id IS NOT NULL",
            [],
        )
        etf_ev = _read(
            con,
            "SELECT stock_id, action, effective_date FROM etf_index_events "
            "WHERE effective_date IS NOT NULL",
            ["action", "effective_date"],
        )
    finally:
        con.close()

    if prices.empty:
        return pd.DataFrame(columns=_EMPTY_COLS)

    prices["date"] = pd.to_datetime(prices["date"])
    base = prices.merge(pool, on="stock_id", how="inner").sort_values(["stock_id", "date"])
    base = base.reset_index(drop=True)

    if not ind.empty:
        ind = ind.copy()
        ind["date"] = pd.to_datetime(ind["date"])
        base = base.merge(ind[["stock_id", "date", "bias_20"]], on=["stock_id", "date"], how="left")
    else:
        base["bias_20"] = np.nan

    base = base.sort_values(["stock_id", "date"]).reset_index(drop=True)
    g = base.groupby("stock_id", sort=False)

    # ── 動能/量能 ──
    base["ret5"] = g["close"].transform(lambda s: s.pct_change(5, fill_method=None) * 100)
    base["ret20"] = g["close"].transform(lambda s: s.pct_change(20, fill_method=None) * 100)
    # bias20：優先讀 indicators 現成值；缺值時用 close/ma20-1 自算補上
    ma20 = g["close"].transform(lambda s: s.rolling(20, min_periods=20).mean())
    bias20_fallback = (base["close"] / ma20.replace(0, np.nan) - 1.0) * 100
    base["bias20"] = base["bias_20"].astype(float)
    base["bias20"] = base["bias20"].where(base["bias20"].notna(), bias20_fallback)

    # break20_flag：收盤價突破「前 20 日（不含當日）最高收盤價」
    prior_high20 = g["close"].transform(lambda s: s.rolling(20, min_periods=20).max().shift(1))
    base["break20_flag"] = base["close"] > prior_high20

    # vol_ratio5：近5日均量 ÷ 近20日均量（量能相對強度）
    vol_ma5 = g["volume"].transform(lambda s: s.rolling(5, min_periods=5).mean())
    vol_ma20 = g["volume"].transform(lambda s: s.rolling(20, min_periods=20).mean())
    base["vol_ratio5"] = vol_ma5 / vol_ma20.replace(0, np.nan)

    # turnover_pct：當日成交股數 ÷ 已發行股數（company_profile 為現況快照，非逐日
    # 歷史股本，屬資料限制下的簡化口徑——已於模組docstring/報告標明）
    if not profile.empty:
        base = base.merge(profile, on="stock_id", how="left")
    else:
        base["issued_shares"] = np.nan
    base["turnover_pct"] = base["volume"].astype(float) / base["issued_shares"].astype(float).replace(0, np.nan) * 100

    # rel_ret20：個股20日報酬 − 類股(sector_id)當日等權平均20日報酬（中性化口徑，
    # 不用市場成分過重、已翻號的 sec_ret20 中位數版本——見模組 docstring）
    sector_mean_ret20 = base.groupby(["date", "sector_id"], sort=False)["ret20"].transform("mean")
    sector_n = base.groupby(["date", "sector_id"], sort=False)["ret20"].transform("count")
    base["rel_ret20"] = base["ret20"] - sector_mean_ret20
    base.loc[sector_n < 5, "rel_ret20"] = np.nan  # 同股同稀薄類股(<5檔)不給值，比照 corners.py 慣例

    # ── 基本面動能：月營收（+10曆日可用）／季財報（+45曆日可用）／Score(long) ──
    if not revenue.empty:
        revenue = revenue.merge(pool, on="stock_id", how="inner")
        revenue = revenue.sort_values(["stock_id", "year", "month"]).reset_index(drop=True)
        rg = revenue.groupby("stock_id", sort=False)
        revenue["rev_yoy"] = revenue["yoy"].astype(float)
        revenue["rev_mom"] = revenue["mom"].astype(float)
        revenue["rev_yoy_chg"] = rg["yoy"].transform(lambda s: s.diff())
        revenue["rev_yoy_accel"] = rg["rev_yoy_chg"].transform(lambda s: s.diff())
        month_start = pd.to_datetime(dict(year=revenue["year"], month=revenue["month"], day=1))
        revenue["avail_date"] = month_start + pd.offsets.MonthEnd(0) + pd.Timedelta(days=10)
        rev_ff = revenue[
            ["stock_id", "avail_date", "rev_yoy", "rev_yoy_chg", "rev_yoy_accel", "rev_mom"]
        ].sort_values(["avail_date", "stock_id"])
        base = base.sort_values(["date", "stock_id"]).reset_index(drop=True)
        base = pd.merge_asof(
            base, rev_ff, left_on="date", right_on="avail_date", by="stock_id", direction="backward"
        ).drop(columns=["avail_date"])
    else:
        for c in ("rev_yoy", "rev_yoy_chg", "rev_yoy_accel", "rev_mom"):
            base[c] = np.nan

    if not fin.empty:
        fin = fin.merge(pool, on="stock_id", how="inner")
        fin = fin.sort_values(["stock_id", "year", "quarter"]).reset_index(drop=True)
        fg = fin.groupby("stock_id", sort=False)
        fin["eps_qoq"] = fg["eps"].transform(lambda s: s.pct_change(fill_method=None) * 100)
        q_end_month = fin["quarter"].astype(int) * 3
        q_end = pd.to_datetime(dict(year=fin["year"], month=q_end_month, day=1)) + pd.offsets.MonthEnd(0)
        fin["avail_date"] = q_end + pd.Timedelta(days=45)
        fin_ff = fin[["stock_id", "avail_date", "eps_qoq"]].sort_values(["avail_date", "stock_id"])
        base = base.sort_values(["date", "stock_id"]).reset_index(drop=True)
        base = pd.merge_asof(
            base, fin_ff, left_on="date", right_on="avail_date", by="stock_id", direction="backward"
        ).drop(columns=["avail_date"])
    else:
        base["eps_qoq"] = np.nan

    base = base.sort_values(["stock_id", "date"]).reset_index(drop=True)
    if not score.empty:
        score = score.copy()
        score["date"] = pd.to_datetime(score["date"])
        score = score.sort_values(["stock_id", "date"]).reset_index(drop=True)
        sg = score.groupby("stock_id", sort=False)
        score["score_chg20"] = sg["total_score"].transform(lambda s: s.diff(20))
        base = base.merge(
            score[["stock_id", "date", "score_chg20"]], on=["stock_id", "date"], how="left"
        )
    else:
        base["score_chg20"] = np.nan

    base = base.sort_values(["stock_id", "date"]).reset_index(drop=True)

    # ── 消息熱度：AttentionListing（讀法比照 backfill_attention.py：以
    #   stock_id/date 對齊）、Event（news_cnt5_pct；個股級 stock_id 若無資料
    #   則整欄 NaN，不 crash） ──
    if not attn.empty:
        attn = attn.merge(pool, on="stock_id", how="inner")
        attn = attn.drop_duplicates(["stock_id", "date"])
        attn["date"] = pd.to_datetime(attn["date"])
        attn["attn_flag"] = True
        base = base.merge(
            attn[["stock_id", "date", "attn_flag"]], on=["stock_id", "date"], how="left"
        )
        base["attn_flag"] = base["attn_flag"].astype("boolean").fillna(False).astype(bool)
        ag = base.groupby("stock_id", sort=False)
        base["attn_cnt20"] = ag["attn_flag"].transform(
            lambda s: s.astype(int).rolling(20, min_periods=1).sum()
        )
    else:
        base["attn_flag"] = False
        base["attn_cnt20"] = np.nan

    if not events.empty:
        events = events.merge(pool, on="stock_id", how="inner")
        events["date"] = pd.to_datetime(events["date"])
        ev_cnt = events.groupby(["stock_id", "date"]).size().rename("_ev_n").reset_index()
        base = base.merge(ev_cnt, on=["stock_id", "date"], how="left")
        base["_ev_n"] = base["_ev_n"].fillna(0)
        eg = base.groupby("stock_id", sort=False)
        news_cnt5 = eg["_ev_n"].transform(lambda s: s.rolling(5, min_periods=1).sum())
        base["_news_cnt5"] = news_cnt5
        base["news_cnt5_pct"] = base.groupby("date", sort=False)["_news_cnt5"].rank(pct=True)
        base = base.drop(columns=["_ev_n", "_news_cnt5"])
    else:
        # events 表無可用個股級資料：整欄 NaN 填充，不 crash（brief 明確要求）
        base["news_cnt5_pct"] = np.nan

    # ── 事件：ETF 定審成分股異動，生效日後第 6~10 交易日窗（已驗證甜蜜點） ──
    base["etf_add_win_flag"] = False
    base["etf_del_win_flag"] = False
    if not etf_ev.empty:
        etf_ev = etf_ev.merge(pool, on="stock_id", how="inner")
        etf_ev["effective_date"] = pd.to_datetime(etf_ev["effective_date"])
        base = base.sort_values(["stock_id", "date"]).reset_index(drop=True)
        # 逐股建立「交易日序號」，用於算「生效日後第 N 個交易日」的日期上下界
        base["_trade_pos"] = base.groupby("stock_id", sort=False).cumcount()
        pos_lookup = base.set_index(["stock_id", "date"])["_trade_pos"]
        date_by_pos = {
            sid: g[["date"]].reset_index(drop=True)
            for sid, g in base.groupby("stock_id", sort=False)
        }

        def _window_dates(row: pd.Series) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
            sid, eff = row["stock_id"], row["effective_date"]
            dates = date_by_pos.get(sid)
            if dates is None or pd.isna(eff):
                return None, None
            # 找 >= 生效日的第一個交易日位置（生效日本身視為第 0 個交易日）
            idx = dates["date"].searchsorted(eff)
            lo_i, hi_i = idx + 6, idx + 10
            if hi_i >= len(dates):
                return None, None
            return dates["date"].iloc[lo_i], dates["date"].iloc[hi_i]

        etf_ev[["_lo", "_hi"]] = etf_ev.apply(_window_dates, axis=1, result_type="expand")
        etf_ev = etf_ev.dropna(subset=["_lo", "_hi"])

        for action, col in (("add", "etf_add_win_flag"), ("remove", "etf_del_win_flag")):
            sub = etf_ev[etf_ev["action"] == action]
            if sub.empty:
                continue
            flag = pd.Series(False, index=base.index)
            for _, r in sub.iterrows():
                mask = (
                    (base["stock_id"] == r["stock_id"])
                    & (base["date"] >= r["_lo"])
                    & (base["date"] <= r["_hi"])
                )
                flag |= mask
            base[col] = base[col] | flag
        base = base.drop(columns=["_trade_pos"])

    base = base.sort_values(["stock_id", "date"]).reset_index(drop=True)

    keep = [
        "stock_id", "date", "sector_id",
        "ret5", "ret20", "bias20", "break20_flag", "vol_ratio5", "turnover_pct", "rel_ret20",
        "rev_yoy", "rev_yoy_chg", "rev_yoy_accel", "rev_mom", "eps_qoq", "score_chg20",
        "attn_flag", "attn_cnt20", "news_cnt5_pct",
        "etf_add_win_flag", "etf_del_win_flag",
    ]
    out = base[keep].copy()
    bool_cols = ["break20_flag", "attn_flag", "etf_add_win_flag", "etf_del_win_flag"]
    for c in bool_cols:
        out[c] = out[c].astype("boolean").fillna(False).astype(bool)
    return out


def other_templates() -> list[Template]:
    """動能/量能、基本面、消息、事件四家族模板清單（每欄至少一模板）。"""
    return [
        # 動能
        Template("M1", "momentum", "ret5", "q_hi", {"q": _Q_HI}),
        Template("M2", "momentum", "ret20", "q_hi", {"q": _Q_HI}),
        Template("M3", "momentum", "bias20", "q_hi", {"q": _Q_HI}),
        Template("M4", "momentum", "break20_flag", "flag", {}),
        Template("M5", "momentum", "rel_ret20", "q_hi", {"q": _Q_HI}),
        # 量能
        Template("V1", "volume", "vol_ratio5", "q_hi", {"q": _Q_HI}),
        Template("V2", "volume", "turnover_pct", "q_hi", {"q": _Q_HI}),
        # 基本面動能
        Template("F1", "fundamental", "rev_yoy", "q_hi", {"q": _Q_HI}),
        Template("F2", "fundamental", "rev_yoy_chg", "q_hi", {"q": _Q_HI}),
        Template("F3", "fundamental", "rev_yoy_accel", "q_hi", {"q": _Q_HI}),
        Template("F4", "fundamental", "rev_mom", "q_hi", {"q": _Q_HI}),
        Template("F5", "fundamental", "eps_qoq", "q_hi", {"q": _Q_HI}),
        Template("F6", "fundamental", "score_chg20", "q_hi", {"q": _Q_HI}),
        # 消息熱度
        Template("N1", "news", "attn_flag", "flag", {}),
        Template("N2", "news", "attn_cnt20", "q_hi", {"q": _Q_HI}),
        Template("N3", "news", "news_cnt5_pct", "q_hi", {"q": _Q_HI}),
        # 事件
        Template("E1", "event", "etf_add_win_flag", "flag", {}),
        Template("E2", "event", "etf_del_win_flag", "flag", {}),
    ]
