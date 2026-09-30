"""§17 Recommendation Policy（B9）：波動度中性化 → 聯合 Gate → Ranking → Dynamic Top-K → NO_TRADE。

Policy 由 YAML 定義（configs/mlentry/policy_*.yaml），policy_version = 內容雜湊。
apply_policy 對「同日 U_t 全體」計算並保留每列的 gate_pass / gate_failure_reason /
recommendation_score / rank / recommended（§17.6），日層級輸出 no_trade_reason。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from ..config import CONFIG_DIR
from .gate import GATE_FAIL

NO_TRADE_REASONS = ("MARKET_NO_OPPORTUNITY", "POLICY_NO_CANDIDATE", "EXECUTION_RISK", "DATA_HEALTH_FAIL",
                    "FEATURE_DRIFT", "MODEL_HEALTH_FAIL", "CALIBRATION_FAIL", "MANUAL_HALT")


@dataclass(frozen=True)
class PolicyConfig:
    name: str
    raw: dict[str, Any]

    @property
    def gate(self) -> dict: return self.raw["gate"]
    @property
    def ranking(self) -> dict: return self.raw["ranking"]
    @property
    def vn(self) -> dict: return self.raw["volatility_neutralization"]
    @property
    def k_max(self) -> int: return int(self.raw["ranking"]["k_max"])
    @property
    def cost_rt(self) -> float: return float(self.raw.get("cost_rt", 0.0))

    @property
    def version(self) -> str:
        return "p_" + hashlib.sha256(json.dumps(self.raw, sort_keys=True, default=str).encode()).hexdigest()[:8]


def load_policy(name: str = "policy_baseline_v1", config_dir: Path = CONFIG_DIR) -> PolicyConfig:
    with open(config_dir / f"{name}.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return PolicyConfig(raw["name"], raw)


def vn_percentile(df: pd.DataFrame, col: str, vol_col: str, n_buckets: int) -> pd.Series:
    """同日 × 波動度分位桶內百分位（average rank，(0,1]）。"""
    vol = df[vol_col].fillna(df[vol_col].median())
    r = vol.groupby(df["signal_date"]).rank(pct=True, method="first")
    bucket = np.clip((r * n_buckets).astype(int), 0, n_buckets - 1)
    return df.groupby([df["signal_date"], bucket])[col].rank(pct=True, method="average")


def apply_policy(df: pd.DataFrame, cfg: PolicyConfig, eligible: pd.Series | None = None,
                 ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """df：同日 U_t 全體列（需 signal_date、gate 兩欄、vol_col）。

    → (逐列表: p_target_vn, p_stop_vn, gate_pass, gate_failure_reason, recommendation_score, rank, recommended,
       逐日表: signal_date, universe_count, qualified_count, recommendation_count, no_trade, no_trade_reason)
    """
    g, v = cfg.gate, cfg.vn
    out = pd.DataFrame(index=df.index)
    out["p_target_vn"] = vn_percentile(df, g["alpha_col"], v["vol_col"], int(v["n_buckets"])).astype("float32")
    out["p_stop_vn"] = vn_percentile(df, g["risk_col"], v["vol_col"], int(v["n_buckets"])).astype("float32")
    fail = pd.Series(0, index=df.index, dtype="int16")
    if eligible is not None:
        fail = fail | (~eligible.astype(bool)).astype("int16") * GATE_FAIL["ELIGIBILITY"]
    if g.get("theta_exec") is not None and "p_executable" in df.columns:
        fail = fail | (df["p_executable"] < float(g["theta_exec"])).astype("int16") * GATE_FAIL["EXECUTION"]
    fail = fail | (out["p_stop_vn"] > float(g["theta_risk_pct"])).astype("int16") * GATE_FAIL["RISK"]
    fail = fail | (out["p_target_vn"] < float(g["theta_alpha_pct"])).astype("int16") * GATE_FAIL["ALPHA"]
    out["gate_pass"] = fail == 0
    out["gate_failure_reason"] = fail
    method = cfg.ranking["method"]
    if method == "vn_diff":
        score = out["p_target_vn"] - out["p_stop_vn"]
    elif method == "p_target":
        score = df[g["alpha_col"]].astype(float)
    else:
        raise ValueError(method)
    out["recommendation_score"] = score.astype("float32")
    ranked = score.where(out["gate_pass"]).groupby(df["signal_date"]).rank(ascending=False, method="first")
    out["rank"] = ranked.astype("float32")
    out["recommended"] = (ranked <= cfg.k_max).fillna(False).to_numpy()

    day = pd.DataFrame({"universe_count": df.groupby("signal_date").size(),
                        "qualified_count": out["gate_pass"].groupby(df["signal_date"]).sum(),
                        "recommendation_count": out["recommended"].groupby(df["signal_date"]).sum()})
    day["no_trade"] = day["recommendation_count"] == 0
    day["no_trade_reason"] = np.where(day["no_trade"], "POLICY_NO_CANDIDATE", None)
    return out, day.reset_index()
