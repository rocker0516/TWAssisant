"""§9.4 日期權重：每個交易日 loss 總權重相同，w = 1/|U_t|。class weight 可疊加但不得破壞 day-normalization。"""

from __future__ import annotations

import numpy as np
import pandas as pd


def day_normalized_weights(sample: pd.DataFrame) -> pd.Series:
    """sample 需含 signal_date、eligible。非 eligible 列權重 NaN。"""
    n = sample.loc[sample["eligible"]].groupby("signal_date")["eligible"].transform("size")
    w = pd.Series(np.nan, index=sample.index, dtype="float32")
    w.loc[n.index] = (1.0 / n).astype("float32")
    return w
