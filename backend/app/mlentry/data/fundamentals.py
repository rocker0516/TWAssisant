"""基本面 PIT 矩陣（F2 challenger）。只走 mlentry 的 PIT 契約（pit.fundamental_available_at），不碰舊 min() 路徑。

每個欄位輸出一個 (calendar × stock) 矩陣：t 日的值 = available_at <= t 的最新期別值。
available_at 落在非交易日 → 順延到下一個交易日（那天收盤後才「可用於 t 日訊號」的語意：
avail <= t 表示 t 日收盤前已公告；統一以 avail 當日即可用）。
估值（pe/pb/dividend_yield）為逐日資料，available_at = date。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .calendar import TradingCalendar
from .pit import fundamental_available_at

GENERAL_DEADLINES = {1: (0, 5, 15), 2: (0, 8, 14), 3: (0, 11, 14), 4: (1, 3, 31)}
FINANCIAL_DEADLINES = {1: (0, 5, 30), 2: (0, 8, 31), 3: (0, 11, 29), 4: (1, 3, 31)}
FINANCIAL_CATEGORIES = frozenset({"金融保險", "金融業"})

REV_COLS = ("rev_yoy", "rev_mom", "revenue")
FIN_COLS = ("eps", "gross_margin", "op_margin", "net_margin", "fin_revenue")
VAL_COLS = ("pe", "pb", "dividend_yield")


def _first_seen(con, kind: str) -> pd.DataFrame:
    try:
        return pd.read_sql_query(
            "SELECT stock_id, year, period, first_seen FROM fundamental_first_seen WHERE kind = ?", con, params=(kind,))
    except Exception:
        return pd.DataFrame(columns=["stock_id", "year", "period", "first_seen"])


def load_revenue(con) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT stock_id, year, month, revenue, yoy AS rev_yoy, mom AS rev_mom FROM revenue_monthly", con)
    df["stock_id"] = df["stock_id"].astype(str)
    nm = df["month"] % 12 + 1; ny = df["year"] + (df["month"] == 12).astype(int)
    statutory = pd.to_datetime(dict(year=ny, month=nm, day=10))
    fs = _first_seen(con, "rev").rename(columns={"period": "month"})
    df = df.merge(fs, on=["stock_id", "year", "month"], how="left")
    df["avail"], df["pit_assumed"] = fundamental_available_at(statutory, df["first_seen"])
    df["period_key"] = df["year"] * 100 + df["month"]
    return df


def load_financials(con) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT stock_id, year, quarter, eps, revenue AS fin_revenue, gross_margin, op_margin, net_margin "
                           "FROM financials_quarterly", con)
    df["stock_id"] = df["stock_id"].astype(str)
    fin = set(pd.read_sql_query("SELECT id FROM stocks WHERE industry_category IN ('金融保險','金融業')", con)["id"].astype(str))
    dl = [(FINANCIAL_DEADLINES if s in fin else GENERAL_DEADLINES)[int(q)] for s, q in zip(df["stock_id"], df["quarter"])]
    statutory = pd.to_datetime(dict(year=df["year"] + [d[0] for d in dl], month=[d[1] for d in dl], day=[d[2] for d in dl]))
    fs = _first_seen(con, "fin").rename(columns={"period": "quarter"})
    df = df.merge(fs, on=["stock_id", "year", "quarter"], how="left")
    df["avail"], df["pit_assumed"] = fundamental_available_at(statutory, df["first_seen"])
    df["period_key"] = df["year"] * 10 + df["quarter"]
    return df


def load_valuation(con, cal: TradingCalendar, columns: pd.Index) -> dict[str, pd.DataFrame]:
    df = pd.read_sql_query("SELECT stock_id, date, pe, pb, dividend_yield FROM valuation", con)
    df = df[df["stock_id"].astype(str).isin(set(columns))]
    df["stock_id"] = df["stock_id"].astype(str)
    out = {}
    for c in VAL_COLS:
        out[c] = (df.pivot_table(index="date", columns="stock_id", values=c, aggfunc="last")
                  .reindex(index=cal.dates, columns=columns).astype("float64"))
    return out


def asof_matrices(rows: pd.DataFrame, cols: tuple[str, ...], cal: TradingCalendar, columns: pd.Index,
                  lags: dict[str, tuple[int, ...]] | None = None) -> dict[str, pd.DataFrame]:
    """rows（含 stock_id, avail, period_key, cols）→ {col: 矩陣}；lags 可另產「往前 k 期」矩陣（col_lag{k}）。

    以 avail 順延到 calendar 位置，pivot 後 ffill：t 日值 = 最新已可得期別。同日多列取期別最大者。
    """
    r = rows[rows["stock_id"].isin(set(columns))].copy()
    r = r[r["avail"].notna()].sort_values(["stock_id", "period_key"])
    dates = cal.dates
    pos = np.searchsorted(dates, r["avail"].dt.strftime("%Y-%m-%d").to_numpy())
    r = r[pos < len(dates)]
    r["cal_date"] = dates[np.searchsorted(dates, r["avail"].dt.strftime("%Y-%m-%d").to_numpy())]
    out: dict[str, pd.DataFrame] = {}
    lag_specs = lags or {}
    for c in cols:
        base_rows = r
        for k in (0, *lag_specs.get(c, ())):
            rr = base_rows.copy()
            if k:
                rr[c] = rr.groupby("stock_id")[c].shift(k)          # 往前 k 期（同一 avail 日可得）
            name = c if k == 0 else f"{c}_lag{k}"
            rr = rr.sort_values(["stock_id", "cal_date", "period_key"]).drop_duplicates(["stock_id", "cal_date"], keep="last")
            wide = rr.pivot(index="cal_date", columns="stock_id", values=c)
            out[name] = wide.reindex(index=dates, columns=columns).ffill().astype("float64")
    return out


def load_fundamental_matrices(con, cal: TradingCalendar, columns: pd.Index) -> dict[str, pd.DataFrame]:
    rev = asof_matrices(load_revenue(con), REV_COLS, cal, columns, lags={"rev_yoy": (1, 2, 3, 12), "revenue": (12,)})
    fin = asof_matrices(load_financials(con), FIN_COLS, cal, columns, lags={"gross_margin": (4,), "op_margin": (4,),
                                                                            "net_margin": (4,), "eps": (1, 2, 3, 4, 5, 6, 7)})
    val = load_valuation(con, cal, columns)
    return {**rev, **fin, **val}
