"""Level 1 v3 價量矩陣載入（設計 2026-09-28 §1、§2.3）。

v2 只用 close/volume；v3 的 Entry 是 O(t+1)、成交判定要漲停價、尺度族要 high/low，
故一次把 OHLCV+turnover 對齊 close 矩陣形狀載入。缺格 = NaN（該股該日無列）。
"""

from __future__ import annotations

import pandas as pd

PRICE_COLS = ("open", "high", "low", "volume", "turnover")


def load_price_matrices(con, index: pd.Index, columns: pd.Index,
                        cols: tuple[str, ...] = PRICE_COLS) -> dict[str, pd.DataFrame]:
    """daily_prices → {欄名: 矩陣}，形狀對齊 (index, columns)，float64。"""
    df = pd.read_sql_query(
        f"SELECT stock_id, date, {', '.join(cols)} FROM daily_prices", con)
    df = df[df["stock_id"].astype(str).isin(set(columns))]
    df["stock_id"] = df["stock_id"].astype(str)
    out: dict[str, pd.DataFrame] = {}
    for c in cols:
        mat = df.pivot_table(index="date", columns="stock_id", values=c, aggfunc="last")
        out[c] = mat.reindex(index=index, columns=columns).astype("float64")
    return out


def load_sector_map(con) -> pd.Series:
    """stock_id → sector_id（float，無類股 NaN）。類股中性化用。"""
    df = pd.read_sql_query("SELECT id, sector_id FROM stocks", con)
    return pd.Series(df["sector_id"].astype("float64").to_numpy(),
                     index=df["id"].astype(str), name="sector_id")


def load_market_close(con) -> pd.Series:
    """加權指數收盤（index=date str 升冪）。"""
    df = pd.read_sql_query("SELECT date, close FROM market_index ORDER BY date", con)
    return pd.Series(df["close"].astype("float64").to_numpy(),
                     index=df["date"].astype(str), name="mkt_close")
