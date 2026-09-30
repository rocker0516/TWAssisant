"""特徵分布監控模式 monitor_mode ∈ {continuous, skip}（Spec A §2.6）。

正式來源優先序：artifact 內建 monitor_mode（未來新 stack）→ sidecar feature_reference.monitoring.json（hash 須符）
→ legacy fallback（day_level → skip）。fallback 只為向後相容，不是分類規則；輸出會標 monitor_mode_source。
名單以「語意」判定（binary／離散計數比例／日級類股級），與現行 champion 的 day_level 集合相同是巧合。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..fingerprint import canonical_hash

log = logging.getLogger(__name__)

SIDECAR_NAME = "feature_reference.monitoring.json"
SCHEMA_VERSION = 1

_BINARY = ("is_attention_stock", "is_disposition_stock", "limit_up_today", "limit_down_today", "large_gap")
_DISCRETE = ("limit_up_count_20d", "limit_down_count_20d", "consecutive_up_days", "consecutive_down_days",
             "positive_day_ratio", "negative_day_ratio")
_DAY_LEVEL = ("market_ret_1d", "market_ret_5d", "market_ret_20d", "market_volatility",
              "industry_ret_5d", "industry_ret_20d", "industry_strength_rank", "breadth_ma20")
SKIP_FEATURES: frozenset[str] = frozenset(_BINARY + _DISCRETE + _DAY_LEVEL)


def monitor_mode_for(name: str) -> str:
    return "skip" if name in SKIP_FEATURES else "continuous"


def build_sidecar(ref_full: dict) -> dict:
    """由明確名單產生 sidecar；source hash 綁定當下 feature_reference.json 內容。"""
    return {"monitoring_schema_version": SCHEMA_VERSION,
            "source_feature_reference_hash": canonical_hash(ref_full),
            "features": {n: {"monitor_mode": monitor_mode_for(n)} for n in ref_full["features"]}}


def load_monitor_modes(stack_dir: Path, ref_full: dict) -> tuple[dict[str, str], str]:
    feats: dict = ref_full.get("features", {})
    if feats and all(isinstance(v, dict) and v.get("monitor_mode") in ("continuous", "skip") for v in feats.values()):
        return {n: v["monitor_mode"] for n, v in feats.items()}, "explicit"
    p = Path(stack_dir) / SIDECAR_NAME
    if p.exists():
        try:
            side = json.loads(p.read_text(encoding="utf-8"))
            if side.get("source_feature_reference_hash") == canonical_hash(ref_full):
                modes = {n: (side.get("features", {}).get(n) or {}).get("monitor_mode") for n in feats}
                if all(m in ("continuous", "skip") for m in modes.values()):
                    return modes, "explicit"
        except (ValueError, OSError):
            pass
        log.warning("%s ignored: hash mismatch or incomplete; using legacy day_level fallback", SIDECAR_NAME)
    else:
        log.warning("%s missing; using legacy day_level fallback", SIDECAR_NAME)
    return {n: ("skip" if v.get("day_level") else "continuous") for n, v in feats.items()}, "legacy_day_level_fallback"
