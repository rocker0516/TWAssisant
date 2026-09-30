# backend/app/mlentry/serving/audit.py
"""§22 run provenance（Spec A §3）：「這次 run 用了什麼」。與 diagnostics（有沒有異常）分開存。

data_snapshot_id 是 run 實際可見 source view 的指紋，只供比對，不宣稱可重建資料。
完整內容存 MLEntryRun.audit_json；API 只透出 audit_summary() 白名單。
"""

from __future__ import annotations

import json
import logging
import platform
import socket
from pathlib import Path

from ..config import CONFIG_DIR, load_yaml
from ..fingerprint import canonical_hash

log = logging.getLogger(__name__)

AUDIT_SUMMARY_KEYS = ("requested_as_of", "feature_snapshot_as_of", "data_snapshot_id", "serving_stack_hash", "code_commit")
_SOURCE_KEYS = ("max_business_date", "max_available_at", "rows_visible_at_as_of")


def serving_stack_hash(stack_dir: Path) -> str | None:
    """stack.json＋artifacts.json 解析後的 canonical hash（縮排／key 順序不影響）。"""
    parts = []
    for name in ("stack.json", "artifacts.json"):
        p = Path(stack_dir) / name
        if not p.exists():
            return None
        parts.append(json.loads(p.read_text(encoding="utf-8")))
    return canonical_hash(parts)


def config_hashes(policy_name: str, config_dir: Path = CONFIG_DIR) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for key, name in (("monitoring", "monitoring"), ("policy", policy_name), ("features", "features")):
        try:
            out[key] = canonical_hash(load_yaml(name, config_dir))
        except (OSError, ValueError):
            out[key] = None
    return out


def runtime_info() -> dict:
    import lightgbm, numpy, pandas  # noqa: E401
    return {"python": platform.python_version(), "lightgbm": lightgbm.__version__, "pandas": pandas.__version__,
            "numpy": numpy.__version__, "hostname": socket.gethostname()}


def build_audit(requested_as_of: str, feature_snapshot_as_of: str | None, stack, sources: dict[str, dict]) -> dict:
    src = {name: {k: (s or {}).get(k) for k in _SOURCE_KEYS} for name, s in sources.items()}
    return {"requested_as_of": str(requested_as_of), "feature_snapshot_as_of": feature_snapshot_as_of,
            "data_snapshot_id": canonical_hash(src), "sources": src,
            "serving_stack_hash": serving_stack_hash(stack.dir), "config_hashes": config_hashes(stack.policy_name),
            "runtime": runtime_info(), "code_commit": stack.code_commit}


def safe_build_audit(requested_as_of, feature_snapshot_as_of, stack, sources) -> dict:
    try:
        return build_audit(requested_as_of, feature_snapshot_as_of, stack, sources)
    except Exception as exc:                                  # noqa: BLE001 — audit 永不讓 run 失敗
        log.exception("build_audit failed")
        return {"error_type": type(exc).__name__}


def audit_summary(audit: dict | None) -> dict | None:
    if not audit:
        return None
    out = {k: audit.get(k) for k in AUDIT_SUMMARY_KEYS}
    a, b = out["requested_as_of"], out["feature_snapshot_as_of"]
    out["as_of_mismatch"] = bool(a and b and a != b)
    return out
