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
    """缺鍵一律 fail-closed：凍結、自動化關閉。"""
    raw = (load_yaml("monitoring").get("lifecycle") or {})
    return {"observation_freeze": bool(raw.get("observation_freeze", True)),
            "freeze_until_mature_days": int(raw.get("freeze_until_mature_days", 60)),
            "auto_retrain": bool(raw.get("auto_retrain", False)),
            "auto_promote": bool(raw.get("auto_promote", False))}


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
