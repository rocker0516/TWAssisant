"""訊號模板：參數格展開＋挖掘窗分位數定門檻。"""
from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import product

import numpy as np
import pandas as pd

_MIN_SHARE, _MAX_SHARE = 0.002, 0.6


@dataclass(frozen=True)
class Template:
    id: str
    family: str
    col: str
    op: str          # q_hi | q_lo | consec_ge | flag
    params: dict


def expand(templates: list[Template]) -> list[Template]:
    out: list[Template] = []
    for t in templates:
        grid_keys = [k for k, v in t.params.items() if isinstance(v, list)]
        if not grid_keys:
            out.append(t)
            continue
        for combo in product(*(t.params[k] for k in grid_keys)):
            p = dict(t.params)
            p.update(dict(zip(grid_keys, combo)))
            suffix = "_".join(f"{k}{v}" for k, v in zip(grid_keys, combo))
            out.append(replace(t, id=f"{t.id}_{suffix}", params=p))
    return out


def _consec_pos(g: pd.Series) -> pd.Series:
    """各列止於當日的連續正值天數。"""
    pos = (g > 0).astype(int)
    grp = (pos == 0).cumsum()
    return pos.groupby(grp).cumsum()


def _mask_one(t: Template, df: pd.DataFrame, threshold_mine: pd.DataFrame | None = None) -> np.ndarray | None:
    """對任意 frame 產遮罩。

    Args:
        t: Template
        df: 要產遮罩的 dataframe（通常為 target）
        threshold_mine: 用來計算 q_hi/q_lo 門檻的 dataframe（通常為 mine），若為 None 則用 df
    """
    if t.col not in df.columns:
        return None
    v = df[t.col]
    threshold_df = threshold_mine if threshold_mine is not None else df

    if t.op == "q_hi":
        thr = threshold_df[t.col].quantile(t.params["q"])
        return (v >= thr).to_numpy()
    if t.op == "q_lo":
        thr = threshold_df[t.col].quantile(t.params["q"])
        return (v <= thr).to_numpy()
    if t.op == "consec_ge":
        streak = df.groupby("stock_id", sort=False)[t.col].transform(_consec_pos)
        return (streak >= t.params["n"]).to_numpy()
    if t.op == "flag":
        return (v.fillna(0) != 0).to_numpy()
    raise ValueError(f"unknown op: {t.op}")


def build_masks(tpls: list[Template], mine: pd.DataFrame, target: pd.DataFrame) -> list[tuple[str, np.ndarray]]:
    """計算遮罩。支持度過濾用 mine 上的遮罩判定，輸出 target 遮罩。"""
    out: list[tuple[str, np.ndarray]] = []
    for t in tpls:
        # 在 mine 上產遮罩，用於計算 share
        mine_mask = _mask_one(t, mine, threshold_mine=None)
        if mine_mask is None:
            continue
        share = float(np.nanmean(mine_mask))
        if not (_MIN_SHARE <= share <= _MAX_SHARE):
            continue
        # 在 target 上產遮罩，用於輸出
        target_mask = _mask_one(t, target, threshold_mine=mine)
        if target_mask is None:
            continue
        out.append((t.id, target_mask))
    return out
