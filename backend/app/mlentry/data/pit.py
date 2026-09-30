"""Point-in-time 規則（FRS §3.3）：source.available_at <= as_of 的唯一實作點。

來源                     available_at
daily_prices(t)          t
market_index(t)          t
attention_listings       公告日 date；t 日狀態 = 存在公告 date<=t 且 begin<=t<=end
月營收／季報             max(法定期限, first_seen)（真觀測）；回補列退回法定期限並標 pit_assumed
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from .calendar import TradingCalendar

# fundamental_first_seen 側表 2026-08-27 啟用並一次寫入全歷史（回補），那天的
# first_seen 不是觀測；之後逐日記錄的才是。
FIRST_SEEN_LIVE_SINCE = date(2026, 8, 28)


def event_mask(windows: pd.DataFrame, cal: TradingCalendar, columns: pd.Index,
               kind: str) -> pd.DataFrame:
    """bool 矩陣：t 日處於 kind（notice / punish）期間。只用公告日 <= t 的列（PIT）。"""
    out = np.zeros((len(cal), len(columns)), dtype=bool)
    col_pos = {str(c): i for i, c in enumerate(columns)}
    dates = cal.dates
    for r in windows[windows["kind"] == kind].itertuples(index=False):
        j = col_pos.get(str(r.stock_id))
        if j is None:
            continue
        start = max(str(r.begin_date), str(r.date))     # 公告前不可知
        s = int(np.searchsorted(dates, start))
        e = int(np.searchsorted(dates, str(r.end_date), side="right"))
        if e > s:
            out[s:e, j] = True
    return pd.DataFrame(out, index=dates, columns=columns)


def fundamental_available_at(statutory: pd.Series, first_seen: pd.Series,
                             live_since: date = FIRST_SEEN_LIVE_SINCE,
                             ) -> tuple[pd.Series, pd.Series]:
    """→ (available_at, pit_assumed)。first_seen >= live_since 才是真觀測，取 max。

    v1 特徵集不吃基本面；此函式定義契約，供 B/C 接基本面時使用。
    """
    st = pd.to_datetime(statutory)
    fs = pd.to_datetime(first_seen)
    live = fs.notna() & (fs >= pd.Timestamp(live_since))
    avail = st.where(~live, np.maximum(st, fs))
    return avail, ~live


def truncate(m: dict[str, pd.DataFrame], cal: TradingCalendar, as_of) -> dict[str, pd.DataFrame]:
    """矩陣截斷到 as_of（含）。Feature 函式只能拿到這個切片。"""
    idx = cal.truncate(as_of)
    return {k: v.loc[idx] for k, v in m.items()}
