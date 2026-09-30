"""§11、§25、§24.3 Registry：ServingStack = Model + Calibration + Policy，三者獨立版本；champion / challenger 分離。

data/mlentry/serving/
  <model_version>/            model artifacts + calibrators + feature_reference.json + stack.json
  champion.json               目前 champion 指標（含 model_status / deployment_mode / promotion_eligible）
  challengers.json            shadow 挑戰者清單

MODEL_STATUS: RESEARCH_SHADOW（未過 promotion contract，只做研究型 shadow 推薦）/ PROMOTED。
promote() 需 promotion contract 全過且顯式 approved_by；本 v1 不會被呼叫。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..datasets.store import DEFAULT_ROOT

SERVING_ROOT = DEFAULT_ROOT / "serving"
STATUS_RESEARCH_SHADOW = "RESEARCH_SHADOW"
STATUS_PROMOTED = "PROMOTED"


@dataclass
class ServingStack:
    model_version: str
    calibration_version: str
    policy_version: str
    policy_name: str
    feature_version: str
    label_version: str
    universe_version: str
    dataset_version: str
    split_version: str
    trained_through: str                  # 訓練資料最後 signal_date（成熟）
    train_window_days: int | None
    feature_names: list[str]
    tasks: list[str]
    calibration_methods: dict[str, str]
    code_commit: str
    model_status: str = STATUS_RESEARCH_SHADOW
    deployment_mode: str = "SHADOW"
    promotion_eligible: bool = False
    promotion_check: dict = field(default_factory=dict)
    frozen_validation: dict = field(default_factory=dict)      # B9 policy 指標摘要（供頁面「Frozen Validation」）
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    serving_root: str = ""                # artifact 所在根目錄（save/load 時設定；不同機器可搬移）

    @property
    def dir(self) -> Path:
        return Path(self.serving_root or SERVING_ROOT) / self.model_version

    def save(self, root: Path = SERVING_ROOT) -> Path:
        self.serving_root = str(root)
        d = root / self.model_version
        d.mkdir(parents=True, exist_ok=True)
        (d / "stack.json").write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        return d

    @classmethod
    def load(cls, model_version: str, root: Path = SERVING_ROOT) -> "ServingStack":
        s = cls(**json.loads((root / model_version / "stack.json").read_text(encoding="utf-8")))
        s.serving_root = str(root)
        return s


def set_champion(stack: ServingStack, root: Path = SERVING_ROOT, *, actor: str | None = None) -> None:
    """設定目前 serving champion（shadow 亦然）。凍結中拒絕（Spec B §24）；寫 previous_model_version 供 rollback。"""
    from .lifecycle import append_audit, guard_champion_change          # 延遲 import 避免循環
    guard_champion_change("set_champion", actor, stack.model_version, root)
    root.mkdir(parents=True, exist_ok=True)
    p = root / "champion.json"
    existing = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    previous = existing.get("model_version")
    keep_previous = existing.get("previous_model_version") if previous == stack.model_version else previous
    p.write_text(json.dumps({
        "model_version": stack.model_version, "calibration_version": stack.calibration_version,
        "policy_version": stack.policy_version, "policy_name": stack.policy_name,
        "model_status": stack.model_status, "deployment_mode": stack.deployment_mode,
        "promotion_eligible": stack.promotion_eligible, "set_at": datetime.now(timezone.utc).isoformat(),
        "previous_model_version": keep_previous,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    append_audit({"event": "set_champion", "action": "set_champion", "actor": actor, "model_version": stack.model_version,
                  "from_model_version": previous, "reason": None}, root=root)


def load_champion(root: Path = SERVING_ROOT) -> ServingStack | None:
    p = root / "champion.json"
    if not p.exists():
        return None
    return ServingStack.load(json.loads(p.read_text(encoding="utf-8"))["model_version"], root)


def promote(stack: ServingStack, promotion_check: dict, approved_by: str, root: Path = SERVING_ROOT) -> ServingStack:
    """Promotion 必須通過 contract（promotion_check['eligible']）且有人核可；否則拒絕。"""
    from .lifecycle import append_audit, guard_champion_change
    guard_champion_change("promote", approved_by or None, stack.model_version, root)
    if not promotion_check.get("eligible"):
        raise PermissionError("promotion contract not satisfied; stack stays RESEARCH_SHADOW")
    if not approved_by:
        raise PermissionError("promotion requires explicit approver")
    stack.model_status = STATUS_PROMOTED
    stack.deployment_mode = "LIVE"
    stack.promotion_eligible = True
    stack.promotion_check = {**promotion_check, "approved_by": approved_by,
                             "approved_at": datetime.now(timezone.utc).isoformat()}
    stack.save(root)
    set_champion(stack, root, actor=approved_by)
    append_audit({"event": "promote", "action": "promote", "actor": approved_by, "model_version": stack.model_version,
                  "from_model_version": None, "reason": "promotion contract satisfied"}, root=root)
    return stack
