"""法人／融資融券流量矩陣（F3 challenger）。

PIT：盤後公告（約 21:30 後才齊），歷史一致性採 **lag 1**——t 日訊號只用 t−1（含）以前的列。
lag 在這裡實作（shift(1)），feature 函式拿到的矩陣已是「t 日可用」的語意。
單位：張（shares/1000）；正規化交給 feature 層（÷ 20 日均量）。
"""

from __future__ import annotations

import pandas as pd

from .calendar import TradingCalendar

INST_COLS = ("foreign_net", "trust_net", "dealer_net", "total_net")
MARGIN_COLS = ("margin_balance", "margin_change", "short_balance", "short_change")
LAG = 1


def _pivot(df: pd.DataFrame, cols, cal: TradingCalendar, columns: pd.Index) -> dict[str, pd.DataFrame]:
    df = df[df["stock_id"].astype(str).isin(set(columns))].copy()
    df["stock_id"] = df["stock_id"].astype(str)
    out = {}
    for c in cols:
        mat = df.pivot_table(index="date", columns="stock_id", values=c, aggfunc="last")
        out[c] = mat.reindex(index=cal.dates, columns=columns).astype("float64").shift(LAG)
    return out


def load_flow_matrices(con, cal: TradingCalendar, columns: pd.Index) -> dict[str, pd.DataFrame]:
    inst = pd.read_sql_query("SELECT stock_id, date, foreign_net, trust_net, dealer_net, total_net FROM institutional", con)
    mar = pd.read_sql_query("SELECT stock_id, date, margin_balance, margin_change, short_balance, short_change FROM margin", con)
    return {**_pivot(inst, INST_COLS, cal, columns), **_pivot(mar, MARGIN_COLS, cal, columns)}
