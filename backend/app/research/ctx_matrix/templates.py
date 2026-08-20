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


def _mask_one(t: Template, mine: pd.DataFrame, target: pd.DataFrame) -> np.ndarray | None:
    if t.col not in target.columns:
        return None
    v = target[t.col]
    if t.op == "q_hi":
        thr = mine[t.col].quantile(t.params["q"])
        return (v >= thr).to_numpy()
    if t.op == "q_lo":
        thr = mine[t.col].quantile(t.params["q"])
        return (v <= thr).to_numpy()
    if t.op == "consec_ge":
        streak = target.groupby("stock_id", sort=False)[t.col].transform(_consec_pos)
        return (streak >= t.params["n"]).to_numpy()
    if t.op == "flag":
        return (v.fillna(0) != 0).to_numpy()
    raise ValueError(f"unknown op: {t.op}")


def build_masks(tpls: list[Template], mine: pd.DataFrame, target: pd.DataFrame) -> list[tuple[str, np.ndarray]]:
    out: list[tuple[str, np.ndarray]] = []
    for t in tpls:
        m = _mask_one(t, mine, target)
        if m is None:
            continue
        share = float(np.nanmean(m))
        if not (_MIN_SHARE <= share <= _MAX_SHARE):
            continue
        out.append((t.id, m))
    return out
