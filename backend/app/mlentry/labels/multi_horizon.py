"""§5.5 衍生標籤：target_hit_H / stop_hit_H，先後順序優先於是否碰到。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import LabelConfig
from .barriers import Event


def derived_labels(outcome: dict[str, pd.DataFrame], cfg: LabelConfig) -> dict[str, pd.DataFrame]:
    ev = outcome["event_type"].to_numpy()
    tday = outcome["target_first_hit_day"].to_numpy(dtype=float)
    sday = outcome["stop_first_hit_day"].to_numpy(dtype=float)
    undefined = (ev == int(Event.NOT_ENTERED)) | (ev == int(Event.PENDING))
    idx, cols = outcome["event_type"].index, outcome["event_type"].columns
    out = {}
    for h in cfg.horizons:
        th = (ev == int(Event.TARGET)) & (tday <= h)
        sh = ((ev == int(Event.STOP)) | (ev == int(Event.STOP_AMBIGUOUS))) & (sday <= h)
        out[f"target_hit_{h}d"] = pd.DataFrame(np.where(undefined, np.nan, th.astype(float)),
                                               index=idx, columns=cols).astype("float32")
        out[f"stop_hit_{h}d"] = pd.DataFrame(np.where(undefined, np.nan, sh.astype(float)),
                                             index=idx, columns=cols).astype("float32")
    return out
