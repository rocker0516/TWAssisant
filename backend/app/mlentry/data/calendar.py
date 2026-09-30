"""交易日曆（FRS §3、附錄 A）：唯一的 t+k 運算來源。

日曆 = market_index 的日期集合（與 daily_prices distinct date 一致，由測試釘住）。
所有 horizon、lookback、purge 皆為「位置」運算，不做曆日加減。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


class TradingCalendar:
    def __init__(self, dates):
        idx = pd.Index(pd.Index(dates).astype(str)).sort_values()
        if idx.has_duplicates:
            raise ValueError("calendar dates must be unique")
        self.dates: pd.Index = pd.Index(idx, name="date")
        self._pos = {d: i for i, d in enumerate(self.dates)}

    def __len__(self) -> int:
        return len(self.dates)

    def __contains__(self, d) -> bool:
        return str(d) in self._pos

    def pos(self, d) -> int:
        try:
            return self._pos[str(d)]
        except KeyError:
            raise KeyError(f"{d} is not a trading day") from None

    def at(self, i: int) -> str | None:
        """位置 → 日期；越界回 None（未來尚未到 / 早於起點）。"""
        return self.dates[i] if 0 <= i < len(self.dates) else None

    def shift(self, d, k: int) -> str | None:
        return self.at(self.pos(d) + k)

    def next(self, d) -> str | None:
        return self.shift(d, 1)

    def window(self, end, lookback: int) -> pd.Index:
        """截至 end（含）的最近 lookback 個交易日。"""
        e = self.pos(end)
        return self.dates[max(0, e - lookback + 1): e + 1]

    def truncate(self, as_of) -> pd.Index:
        return self.dates[: self.pos(as_of) + 1]

    def positions(self, dates) -> np.ndarray:
        return np.fromiter((self.pos(d) for d in dates), dtype=np.int64, count=len(dates))


def load_calendar(con) -> TradingCalendar:
    df = pd.read_sql_query("SELECT date FROM market_index ORDER BY date", con)
    return TradingCalendar(df["date"].astype(str))
