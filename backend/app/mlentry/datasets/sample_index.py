"""§10.1 Sample Index：coverage × signal_date 全格（含不合格列，供 Universe shrinkage 稽核）。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.calendar import TradingCalendar
from ..data.universe import primary_reason


def sample_id(stock_id: pd.Series | np.ndarray, signal_date: pd.Series | np.ndarray) -> pd.Series:
    return pd.Series(np.char.add(np.char.add(np.asarray(stock_id, dtype=str), "_"),
                                 np.asarray(signal_date, dtype=str)))


def build_sample_index(eligible: pd.DataFrame, elig_flags: pd.DataFrame, hard: pd.DataFrame,
                       soft: pd.DataFrame, cal: TradingCalendar, versions: dict[str, str],
                       start: str) -> pd.DataFrame:
    dates = eligible.index
    keep = dates >= start
    dates = dates[keep]
    cols = eligible.columns
    n, m = len(dates), len(cols)
    rows = np.repeat(np.arange(n), m)
    cs = np.tile(np.arange(m), n)
    sd = dates.to_numpy()[rows]
    sid = cols.to_numpy()[cs]
    pos = cal.positions(dates)
    entry = np.array([cal.at(int(p) + 1) for p in pos], dtype=object)[rows]
    reason = primary_reason(elig_flags.loc[dates]).to_numpy()[rows, cs]
    df = pd.DataFrame({
        "sample_id": sample_id(sid, sd).to_numpy(),
        "stock_id": sid,
        "signal_date": sd,
        "entry_date": entry,
        "universe_version": versions["universe_version"],
        "feature_version": versions["feature_version"],
        "label_version": versions["label_version"],
        "eligible": eligible.loc[dates].to_numpy()[rows, cs],
        "eligibility_flags": elig_flags.loc[dates].to_numpy()[rows, cs].astype("uint16"),
        "primary_exclusion_reason": reason,
        "data_quality_flag": hard.loc[dates].to_numpy()[rows, cs].astype("uint16"),
        "quality_soft_flags": soft.loc[dates].to_numpy()[rows, cs].astype("uint16"),
    })
    return df
