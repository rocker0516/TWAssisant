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


# ── Trading Eligibility（FRS §3 v1.1）──
#
# 判準是「排除的理由」而非排除的後果：流動性與處置回答的是「這檔股票能不能買」，
# 屬 Universe 天職；動能／估值那類回答「會不會漲」的條件才是被禁止的 Alpha 條件。

ADV20_FLOOR = 5e7        # 20 日均成交值下限（新台幣）。FRS 凍結常數，不得因績效回調。
ADV_WINDOW = 20
ADV_MIN_PERIODS = 10

# ADV20 暖身期（rolling 20 / min_periods 10）與 rolling 特徵回看窗（如 pos240）
# 需要的暖身期之後（設計 §4.2）。研究與生產必須共用同一個裁切點——這是
# build_tradable_universe 的一部分，不得只在研究端裁切（見最終審查 I1）。
RESEARCH_START = "2020-02-01"


def adv20(turnover: pd.DataFrame) -> pd.DataFrame:
    """20 日均成交值矩陣。只回看；不足 ADV_MIN_PERIODS 日的暖身期為 NaN。"""
    return turnover.rolling(ADV_WINDOW, min_periods=ADV_MIN_PERIODS).mean()


def punish_mask(windows: pd.DataFrame, index: pd.Index,
                columns: pd.Index) -> pd.DataFrame:
    """處置期間布林矩陣：T ∈ [begin_date, end_date]（含兩端）為 True。

    windows 欄位需含 stock_id / begin_date / end_date（YYYY-MM-DD 字串）。
    只處理處置（punish）——注意股（notice）仍為正常競價撮合，不排除。
    """
    out = pd.DataFrame(False, index=index, columns=columns)
    for row in windows.itertuples():
        if row.stock_id not in out.columns:
            continue
        sel = (index >= str(row.begin_date)) & (index <= str(row.end_date))
        if sel.any():
            out.loc[index[sel], row.stock_id] = True
    return out


def tradable_mask(close: pd.DataFrame, turnover: pd.DataFrame,
                  punish: pd.DataFrame, floor: float = ADV20_FLOOR) -> pd.DataFrame:
    """U_t 布林矩陣＝存在性 ∧ 流動性 ∧ 非處置。三個矩陣需同形狀。"""
    return universe_mask(close) & (adv20(turnover) >= floor) & ~punish


def load_turnover(con, eligible: set[str], index: pd.Index,
                  columns: pd.Index) -> pd.DataFrame:
    """合格股票的成交值矩陣，對齊 close 矩陣形狀（缺格為 NaN）。"""
    df = pd.read_sql_query(
        "SELECT stock_id, date, turnover FROM daily_prices "
        "WHERE turnover IS NOT NULL", con)
    return (df[df["stock_id"].isin(eligible)]
            .pivot_table(index="date", columns="stock_id", values="turnover",
                         aggfunc="last")
            .reindex(index=index, columns=columns))


def load_punish_windows(con) -> pd.DataFrame:
    """處置股區間長表（stock_id, begin_date, end_date）。notice 不在此列。"""
    return pd.read_sql_query(
        "SELECT stock_id, begin_date, end_date FROM attention_listings "
        "WHERE kind = 'punish' AND begin_date IS NOT NULL "
        "AND end_date IS NOT NULL", con)


def build_tradable_universe(con) -> tuple[pd.DataFrame, pd.DataFrame]:
    """DB → (close 矩陣, U_t 布林矩陣)。**研究與 Production 的唯一入口。**

    設計 §6：本次改版的三個裂縫皆源於兩條路徑各自組裝。任何新的呼叫端都必須走這裡，
    不得自行拼裝 eligible_ids / close_matrix / tradable_mask。

    RESEARCH_START 裁切也在這裡做（而非只在研究端）：兩端必須共用同一個訓練母體與
    同一份 rolling 特徵暖身窗，否則生產端會多吃 2020 年初的暖身列、rolling 特徵
    （如 pos240）的回看窗也會與研究端分歧（最終審查 I1）。
    """
    stocks = load_stocks(con)
    elig = eligible_ids(stocks)
    close = close_matrix(load_close_prices(con, elig))
    turnover = load_turnover(con, elig, close.index, close.columns)
    punish = punish_mask(load_punish_windows(con), close.index, close.columns)
    mask = tradable_mask(close, turnover, punish)
    keep = close.index >= RESEARCH_START
    return close.loc[keep], mask.loc[keep]
