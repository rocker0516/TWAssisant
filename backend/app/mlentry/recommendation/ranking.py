"""§17.3–§17.4 Ranking 與 Dynamic Top-K（B8）。

V1 只比較四個可解釋的排序（不做 meta model）：
  A  p_target_10d
  B  p_target_10d − p_stop_10d
  C  pct(p_target_10d) − pct(p_stop_10d)
  D  FRS 加權 percentile：Σ w_j Q(x_j)，Q 為同日 U_t 內百分位
K_t = min(K_max, qualified_count_t)。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .gate import daily_pct

FRS_DEFAULT_WEIGHTS = {"p_target_10d": 1.0, "p_target_5d": 0.5, "p_target_3d": 0.25,
                       "pred_mfe_10d": 0.5, "p_stop_10d": -1.0, "p_stop_5d": -0.5}


@dataclass(frozen=True)
class RankingConfig:
    method: str                                  # A | B | C | D
    weights: dict = field(default_factory=lambda: dict(FRS_DEFAULT_WEIGHTS))
    k_max: int = 5

    @property
    def version(self) -> str:
        return "r_" + hashlib.sha256(json.dumps({"m": self.method, "w": self.weights if self.method == "D" else None,
                                                  "k": self.k_max}, sort_keys=True).encode()).hexdigest()[:8]


def score(df: pd.DataFrame, cfg: RankingConfig) -> pd.Series:
    """同日 U_t 全體算分（Gate 前後皆可；percentile 以全 U_t 為分母，避免候選數影響尺度）。"""
    if cfg.method == "A":
        return df["p_target_10d"].astype(float)
    if cfg.method == "B":
        return (df["p_target_10d"] - df["p_stop_10d"]).astype(float)
    if cfg.method == "C":
        return daily_pct(df, "p_target_10d") - daily_pct(df, "p_stop_10d")
    if cfg.method == "D":
        s = pd.Series(0.0, index=df.index)
        for col, w in cfg.weights.items():
            if col in df.columns:
                s = s + w * daily_pct(df, col).fillna(0.5)
        return s
    raise ValueError(cfg.method)


def rank_and_select(df: pd.DataFrame, gate_pass: pd.Series, cfg: RankingConfig) -> pd.DataFrame:
    """→ DataFrame(recommendation_score, rank, recommended)。rank 只在 gate_pass 內；1 = 最強。"""
    s = score(df, cfg)
    out = pd.DataFrame({"recommendation_score": s.astype("float32")}, index=df.index)
    ranked = s.where(gate_pass).groupby(df["signal_date"]).rank(ascending=False, method="first")
    out["rank"] = ranked.astype("float32")
    out["recommended"] = (ranked <= cfg.k_max).fillna(False).to_numpy()
    return out
