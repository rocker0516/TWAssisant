"""§24 lifecycle 骨架（Spec B）：freeze 閘門、challenger registry、promotion audit、rollback metadata。

全部 disabled：observation_freeze 為 true（或缺鍵）時任何 champion 變更都拒絕並留 audit。
檔案：<serving root>/challengers.json（schema v1）、<serving root>/promotion_audit.jsonl（append-only）。
不排程、不自動 retrain、不接 API 寫入端。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ..config import load_yaml
from .versions import SERVING_ROOT

log = logging.getLogger(__name__)

AUDIT_FILE = "promotion_audit.jsonl"
CHALLENGERS_FILE = "challengers.json"
CHALLENGER_STATUSES = ("registered", "rejected", "promoted")


class FreezeError(PermissionError):
    """觀察凍結中：拒絕任何 champion 變更。"""


def lifecycle_config() -> dict:
    """缺鍵或 null 一律 fail-closed：凍結、自動化關閉。"""
    raw = (load_yaml("monitoring").get("lifecycle") or {})
    # Treat None as missing (fail-closed defaults)
    v_freeze = raw.get("observation_freeze")
    observation_freeze = True if v_freeze is None else bool(v_freeze)
    v_retrain = raw.get("auto_retrain")
    auto_retrain = False if v_retrain is None else bool(v_retrain)
    v_promote = raw.get("auto_promote")
    auto_promote = False if v_promote is None else bool(v_promote)
    v_mature = raw.get("freeze_until_mature_days")
    freeze_until_mature_days = 60 if v_mature is None else int(v_mature)
    return {"observation_freeze": observation_freeze,
            "freeze_until_mature_days": freeze_until_mature_days,
            "auto_retrain": auto_retrain,
            "auto_promote": auto_promote}


def freeze_state(session=None) -> dict:
    st = {**lifecycle_config(), "mature_days": None}
    if session is not None:
        try:
            from ..monitoring import performance
            df = performance.load_matured(session)
            st["mature_days"] = int(df["signal_date"].nunique()) if len(df) else 0
        except Exception:                                    # noqa: BLE001 — 只是顯示用
            log.exception("freeze_state: mature_days unavailable")
    return st


def append_audit(event: dict, root: Path = SERVING_ROOT) -> dict:
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    rec = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **event, "freeze_state": lifecycle_config()}
    with open(root / AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    return rec


def read_audit(root: Path = SERVING_ROOT, limit: int = 100) -> list[dict]:
    p = Path(root) / AUDIT_FILE
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out[-limit:]


def guard_champion_change(action: str, actor: str | None, model_version: str | None, root: Path = SERVING_ROOT) -> None:
    """凍結中：寫 refuse audit 並拒絕。解凍：直接放行（後續仍受 promote 自身的 eligible／approver 檢查）。"""
    if lifecycle_config()["observation_freeze"]:
        append_audit({"event": "refuse", "action": action, "actor": actor, "model_version": model_version,
                      "from_model_version": None, "reason": "observation_freeze"}, root=root)
        raise FreezeError(f"observation freeze active: {action} refused for {model_version}")


def _read_challengers(root: Path) -> dict:
    p = Path(root) / CHALLENGERS_FILE
    if not p.exists():
        return {"schema_version": 1, "challengers": []}
    return json.loads(p.read_text(encoding="utf-8"))


def _write_challengers(root: Path, doc: dict) -> None:
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    (root / CHALLENGERS_FILE).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def register_challenger(stack, evaluation: dict | None = None, note: str = "", actor: str | None = None,
                        root: Path = SERVING_ROOT) -> dict:
    """冪等：同 model_version 更新 evaluation／note，保留 registered_at 與 status。絕不碰 champion.json。"""
    doc = _read_challengers(root)
    ev = {"target_lift_at_5": None, "stop_ratio_at_5": None, "coverage": None, "worst_fold_lift_at_5": None,
          **{k: v for k, v in (evaluation or {}).items()}}
    existing = next((c for c in doc["challengers"] if c["model_version"] == stack.model_version), None)
    if existing is None:
        existing = {"model_version": stack.model_version,
                    "registered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "registered_by": actor,
                    "status": "registered"}
        doc["challengers"].append(existing)
    existing.update({"dataset_version": stack.dataset_version, "feature_version": stack.feature_version,
                     "policy_name": stack.policy_name, "code_commit": stack.code_commit, "evaluation": ev,
                     "promotion_check": dict(stack.promotion_check or {}), "note": note})
    _write_challengers(root, doc)
    append_audit({"event": "register_challenger", "action": None, "actor": actor, "model_version": stack.model_version,
                  "from_model_version": None, "reason": note or None}, root=root)
    return existing


def list_challengers(root: Path = SERVING_ROOT) -> list[dict]:
    return list(_read_challengers(root)["challengers"])


def mark_challenger(model_version: str, status: str, note: str = "", actor: str | None = None,
                    root: Path = SERVING_ROOT) -> dict:
    if status not in CHALLENGER_STATUSES:
        raise ValueError(f"status must be one of {CHALLENGER_STATUSES}")
    doc = _read_challengers(root)
    c = next((c for c in doc["challengers"] if c["model_version"] == model_version), None)
    if c is None:
        raise KeyError(model_version)
    c["status"] = status
    if note:
        c["note"] = note
    _write_challengers(root, doc)
    append_audit({"event": "mark_challenger", "action": status, "actor": actor, "model_version": model_version,
                  "from_model_version": None, "reason": note or None}, root=root)
    return c


def rollback(actor: str, reason: str, root: Path = SERVING_ROOT):
    """把 champion 切回 champion.json.previous_model_version。凍結中拒絕；只有函式，不接 CLI／API。"""
    from .versions import ServingStack, load_champion, set_champion
    root = Path(root)
    p = root / "champion.json"
    if not p.exists():
        raise ValueError("no champion to roll back from")
    cur = json.loads(p.read_text(encoding="utf-8"))
    prev = cur.get("previous_model_version")
    guard_champion_change("rollback", actor, prev, root)
    if not prev or not (root / prev / "stack.json").exists():
        raise ValueError(f"previous_model_version {prev!r} unavailable; cannot roll back")
    stack = ServingStack.load(prev, root)
    set_champion(stack, root, actor=actor)
    append_audit({"event": "rollback", "action": "rollback", "actor": actor, "model_version": prev,
                  "from_model_version": cur.get("model_version"), "reason": reason}, root=root)
    return load_champion(root)
