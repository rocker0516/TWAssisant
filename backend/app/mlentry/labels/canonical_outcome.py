"""§5.4 Canonical Outcome 長表 + label_available_date + label_version。

矩陣（列 = signal_date）→ 長表（一列 = (stock_id, signal_date)）。只保留 mask 為 True 的格
（builder 傳入 U_t）。entry_date / label_available_date 以 calendar 位置推得。
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd

from ..config import LabelConfig
from ..data.calendar import TradingCalendar
from .barriers import run_barriers
from .multi_horizon import derived_labels

OUTCOME_COLS_ORDER = ("benchmark_entry_price", "entry_status", "entry_executable", "event_type",
                      "target_first_hit_day", "stop_first_hit_day", "mae_before_target",
                      "path_days_available", "path_truncated", "matured")


def label_version(cfg: LabelConfig) -> str:
    return "l_" + hashlib.sha256(json.dumps(
        {"target_pct": cfg.target_pct, "stop_pct": cfg.stop_pct, "max_horizon": cfg.max_horizon,
         "horizons": cfg.horizons, "return_horizons": cfg.return_horizons,
         "entry": "open_t+1", "ambiguous": "STOP_AMBIGUOUS", "k1_is_entry_day": True},
        sort_keys=True).encode()).hexdigest()[:8]


def build_outcome_matrices(m: dict[str, pd.DataFrame], cfg: LabelConfig) -> dict[str, pd.DataFrame]:
    out = run_barriers(m, cfg)
    out.update(derived_labels(out, cfg))
    return out


def to_long(mats: dict[str, pd.DataFrame], mask: pd.DataFrame, cal: TradingCalendar,
            cfg: LabelConfig) -> pd.DataFrame:
    """mask（bool 矩陣，列 = signal_date）為 True 的格 → 長表。"""
    dates = mats["event_type"].index
    cols = mats["event_type"].columns
    rows, cs = np.nonzero(mask.reindex(index=dates, columns=cols).fillna(False).to_numpy())
    df = pd.DataFrame({"stock_id": cols.to_numpy()[cs], "signal_date": dates.to_numpy()[rows]})
    pos = cal.positions(df["signal_date"])
    entry_pos = pos + 1
    avail_pos = pos + cfg.max_horizon
    df["entry_date"] = [cal.at(int(p)) for p in entry_pos]
    df["label_available_date"] = [cal.at(int(p)) for p in avail_pos]
    for name, mat in mats.items():
        df[name] = mat.to_numpy()[rows, cs]
    return df
