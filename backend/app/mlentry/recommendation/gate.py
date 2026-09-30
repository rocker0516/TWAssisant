"""§17.1 Candidate Gate（B7）：以「同日橫斷面百分位」表達的聯合門檻，比原始機率跨 fold / regime 穩定。

candidate = eligible
            ∧ pct(p_target_10d)  >= theta_alpha_pct
            ∧ pct(p_stop_10d)    <= theta_risk_pct
            ∧ p_executable       >= theta_exec      （Execution model 若 ≈ 常數則 V1 policy 不依賴）

單獨用 p_target 排序等於買波動度（B checkpoint 1 實證），因此 Gate 必須是聯合條件。
Threshold 由 OOF grid 決定（policy_grid.py），這裡只定義規則與版本。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

GATE_FAIL = {"ELIGIBILITY": 1, "EXECUTION": 2, "RISK": 4, "ALPHA": 8}


@dataclass(frozen=True)
class GateConfig:
    theta_alpha_pct: float          # target percentile 下限，如 0.90
    theta_risk_pct: float           # stop percentile 上限，如 0.40
    theta_exec: float | None = None  # None = 不用 execution gate
    alpha_col: str = "p_target_10d"
    risk_col: str = "p_stop_10d"

    @property
    def version(self) -> str:
        return "g_" + hashlib.sha256(json.dumps(self.__dict__, sort_keys=True).encode()).hexdigest()[:8]


def daily_pct(df: pd.DataFrame, col: str) -> pd.Series:
    return df.groupby("signal_date")[col].rank(pct=True, method="average")


def apply_gate(df: pd.DataFrame, cfg: GateConfig, eligible: pd.Series | None = None,
               ) -> tuple[pd.Series, pd.Series]:
    """→ (gate_pass bool, gate_failure_reason bitmask)。df 需含 signal_date 與 alpha/risk 欄。"""
    fail = pd.Series(0, index=df.index, dtype="int16")
    if eligible is not None:
        fail = fail | (~eligible.astype(bool)).astype("int16") * GATE_FAIL["ELIGIBILITY"]
    if cfg.theta_exec is not None and "p_executable" in df.columns:
        fail = fail | (df["p_executable"] < cfg.theta_exec).astype("int16") * GATE_FAIL["EXECUTION"]
    fail = fail | (daily_pct(df, cfg.risk_col) > cfg.theta_risk_pct).astype("int16") * GATE_FAIL["RISK"]
    fail = fail | (daily_pct(df, cfg.alpha_col) < cfg.theta_alpha_pct).astype("int16") * GATE_FAIL["ALPHA"]
    return fail == 0, fail
