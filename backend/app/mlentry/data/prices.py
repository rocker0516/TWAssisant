"""價量矩陣 adapter：沿用 research.level1.prices，對齊 calendar（FRS §35 adapter 原則）。"""

from __future__ import annotations

import pandas as pd

from app.research.level1 import prices as _p
from app.research.level1.universe import eligible_ids, load_stocks

from .calendar import TradingCalendar

PRICE_COLS = ("open", "high", "low", "close", "volume", "turnover")


def coverage_ids(con) -> pd.Index:
    """Coverage Universe（§7.1）：上市／上櫃普通股靜態合格股號，沿用 eligible_ids。"""
    return pd.Index(sorted(eligible_ids(load_stocks(con))), name="stock_id")


def load_matrices(con, cal: TradingCalendar, columns: pd.Index) -> dict[str, pd.DataFrame]:
    """{open,high,low,close,volume,turnover}，形狀 = (calendar, columns)，缺格 NaN。"""
    return _p.load_price_matrices(con, cal.dates, columns, cols=PRICE_COLS)


def load_market_close(con, cal: TradingCalendar) -> pd.Series:
    return _p.load_market_close(con).reindex(cal.dates)


def load_sector_map(con) -> pd.Series:
    return _p.load_sector_map(con)


def load_attention_windows(con) -> pd.DataFrame:
    """注意／處置公告長表（stock_id, kind, date, begin_date, end_date）。date = 公告日（PIT 依此）。"""
    return pd.read_sql_query(
        "SELECT stock_id, kind, date, begin_date, end_date FROM attention_listings "
        "WHERE begin_date IS NOT NULL AND end_date IS NOT NULL", con)
