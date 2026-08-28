"""Level 1 Point-in-Time Research Universe（FRS §3）。

U_t = T 日「當日有收盤價」的台灣上市／上櫃普通股。

兩層過濾，職責分明（Universe 只管「能不能被選」，不藏 Alpha 條件）：

1. 靜態合格性（eligible_ids）：股號為 4 位數字（排除 ETN 02xxxx、DR 91xxxx、
   特別股 2887A 型）、is_etf=0、market ∈ {上市, 上櫃}、
   industry_category 不屬指數/受益證券等非普通股類別。
   股號型態與類別雙重把關——兩者來源不同，互為備援。
2. 動態存在性（每日）：T 日在 daily_prices 有收盤價才進 U_t。
   價格列本身就是 point-in-time 事實：未上市沒有列、下市後不再有列、
   停牌當日沒有列。不可用今日股票清單回填歷史（§3）。

已知限制（Survivorship，文件化）：
- daily_prices 歷史來自全市場逐日快照，2020 以來下市的普通股約 71 檔「有」保留
  其上市期間資料；但在系統開始收資料前就消失、或主檔從未收錄者補不到。
  結論只能宣稱「部分無存活者偏差」。
"""

from __future__ import annotations

import re

import pandas as pd

# 非普通股的 industry_category 值（與股號型態規則互為備援）
_NON_COMMON_CATEGORIES = frozenset({
    "存託憑證", "ETN", "指數投資證券(ETN)", "受益證券", "所有證券", "Index", "大盤",
})
_COMMON_ID = re.compile(r"^\d{4}$")


def eligible_ids(stocks: pd.DataFrame) -> set[str]:
    """靜態合格股號集合。

    stocks 欄位需含：id, is_etf, market, industry_category（stocks 主檔全量）。
    """
    df = stocks
    ok = (
        df["id"].astype(str).str.match(_COMMON_ID)
        & ~df["is_etf"].astype(bool)
        & df["market"].isin(["上市", "上櫃"])
        & ~df["industry_category"].fillna("").isin(_NON_COMMON_CATEGORIES)
    )
    return set(df.loc[ok, "id"].astype(str))


def load_stocks(con) -> pd.DataFrame:
    """讀 stocks 主檔（sqlite3 connection 或 SQLAlchemy connectable）。"""
    return pd.read_sql_query(
        "SELECT id, name, is_etf, market, industry_category, listed_date FROM stocks", con)


def load_close_prices(con, eligible: set[str]) -> pd.DataFrame:
    """合格股票的收盤價長表（stock_id, date, close），date 為 str YYYY-MM-DD。"""
    df = pd.read_sql_query(
        "SELECT stock_id, date, close FROM daily_prices WHERE close IS NOT NULL", con)
    return df[df["stock_id"].isin(eligible)].reset_index(drop=True)


def close_matrix(prices: pd.DataFrame) -> pd.DataFrame:
    """長表 → 收盤價矩陣（index=交易日升冪, columns=stock_id）。

    NaN 即「該股該日不存在／停牌」——U_t 的動態存在性直接由非 NaN 判定。
    """
    mat = prices.pivot_table(index="date", columns="stock_id", values="close",
                             aggfunc="last")
    return mat.sort_index()


def universe_mask(close: pd.DataFrame) -> pd.DataFrame:
    """U_t 布林矩陣：close 非 NaN。與 close_matrix 同形狀。"""
    return close.notna()
