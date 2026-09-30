# Observation Hardening v1 — Spec B（§18 Frozen diagnostics＋§24 lifecycle 骨架）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 依 spec `docs/superpowers/specs/2026-10-01-observation-hardening-b-diagnostics-lifecycle-design.md`：對 champion 的 dev OOF 產生唯讀 evaluation diagnostics（Lift@K／timing／ranking／regime，全標 diagnostic only）並在體檢頁折疊顯示；建立 lifecycle 骨架（凍結閘門、challenger registry、promotion audit、rollback metadata），全部 disabled，serving stack 釘死。

**Architecture:** 純函式模組 `evaluation/diagnostics_frozen.py` 只吃 DataFrame；唯讀腳本負責讀 OOF＋policy＋features 並寫 `diagnostics_frozen.json`。`registry/lifecycle.py` 持有 freeze 閘門、JSON 註冊表與 append-only audit；`versions.py` 只在 `set_champion`／`promote` 各加一行閘門並存 `previous_model_version`。API 唯讀透出；前端只 render。

**Tech Stack:** FastAPI + SQLAlchemy + pandas/numpy/pyarrow（backend/.venv）、pytest；React + TypeScript + Tailwind（`npm run build`）。

## Global Constraints

- Spec A §0 全部沿用（四 gate 不動、`configs/` 只允許 `monitoring.yaml` 新增區、frozen champion artifact 目錄既有檔案不得寫入、例外訊息不進 JSON/API）。
- §18 每個輸出區塊含 `"diagnostic_only": true, "not_used_for_policy": true`；UI 每張表固定 `Diagnostic only · Not used for policy selection`；Live 欄顯示 `等待 60D（目前 N）`。
- §18 只算 Frozen dev OOF；不呼叫 `load_final_holdout`、不跑 policy grid；唯一寫入 `policy/<policy>/diagnostics_frozen.json`。
- Regime 切點由 dev 資料分位算出並寫進輸出 `cuts`；程式碼不硬寫門檻；`n < 30` 的格子值為 `null`；industry 只列推薦列數 ≥ 30 的類股。
- 市值 = 現行 `company_profile.issued_shares × 當日 close`，輸出附 `"mcap_basis": "current_company_profile"`。
- `monitoring.yaml` 新增 `lifecycle: {observation_freeze: true, freeze_until_mature_days: 60, auto_retrain: false, auto_promote: false}`；缺鍵視為凍結（fail-closed）。
- 凍結中 `set_champion`／`promote`／`rollback` 一律 `raise FreezeError` 並寫 audit `refuse`；解凍只由人改 yaml。
- `versions.py` 改動僅限：`set_champion` 與 `promote` 各加一行閘門、`set_champion` 寫 `previous_model_version`、成功後寫 audit；`load_champion`／`ServingStack`／`daily_run` 不變。
- 不做：排程、自動 retrain、challenger 評估流程、rollback CLI／API 寫入端、DB 表、Live 側 §18。
- 後端測試一律在 `backend/` 下：`.venv/Scripts/python.exe -m pytest ...`。
- Commit 訊息結尾：`Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。

## File Structure

| 檔案 | 責任 |
|---|---|
| `backend/configs/mlentry/monitoring.yaml` | 新增 `lifecycle:` 區 |
| `backend/app/mlentry/registry/lifecycle.py` | 新增：`FreezeError`、`lifecycle_config`、`freeze_state`、`append_audit`、`read_audit`、`guard_champion_change`、`register_challenger`、`list_challengers`、`mark_challenger`、`rollback` |
| `backend/app/mlentry/registry/versions.py` | `set_champion`／`promote` 閘門＋`previous_model_version`＋audit |
| `backend/app/mlentry/serving/train_stack.py` | `register_as` 參數 |
| `backend/app/mlentry/evaluation/diagnostics_frozen.py` | 新增：`topk_mask`、`lift_at_k`、`timing`、`ranking_diagnostics`、`regime_breakdown`、`build_diagnostics` |
| `backend/scripts/mlentry_frozen_diagnostics.py` | 新增：唯讀腳本 |
| `backend/app/api/routes_mlentry.py` | `/health` `diagnostics_frozen`＋`live_gate`；`/status` `lifecycle` |
| `frontend/src/api/client.ts`、`components/MLEntryHealth.tsx`、`components/MLEntrySystem.tsx` | 型別、診斷折疊區、lifecycle 列 |
| tests | `test_mlentry_lifecycle.py`、`test_mlentry_diagnostics_frozen.py`、`test_mlentry_serving.py`（修改）、`test_mlentry_api.py`（修改） |

---

### Task 1: `lifecycle.py` 核心——config、freeze_state、audit、閘門

**Files:**
- Modify: `backend/configs/mlentry/monitoring.yaml`（檔尾新增）
- Create: `backend/app/mlentry/registry/lifecycle.py`
- Test: `backend/tests/test_mlentry_lifecycle.py`

**Interfaces:**
- Consumes: `config.load_yaml`、`registry.versions.SERVING_ROOT`
- Produces: `FreezeError(PermissionError)`；`lifecycle_config() -> dict`（缺 `observation_freeze` → True）；`freeze_state(session=None) -> dict`；`append_audit(event: dict, root=SERVING_ROOT) -> dict`；`read_audit(root=SERVING_ROOT, limit=100) -> list[dict]`；`guard_champion_change(action: str, actor: str | None, model_version: str | None, root=SERVING_ROOT) -> None`；`AUDIT_FILE = "promotion_audit.jsonl"`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_mlentry_lifecycle.py
"""§24 lifecycle 骨架：freeze 閘門（fail-closed）、append-only audit、challenger registry、rollback metadata。全部 disabled。"""

from __future__ import annotations

import json

import pytest

from app.mlentry.registry import lifecycle as lc


def _patch_yaml(monkeypatch, lifecycle: dict | None):
    from app.mlentry.config import load_yaml
    def fake(name, *a, **k):
        base = load_yaml(name)
        if name == "monitoring":
            base = {k2: v for k2, v in base.items() if k2 != "lifecycle"}
            if lifecycle is not None:
                base["lifecycle"] = lifecycle
        return base
    monkeypatch.setattr(lc, "load_yaml", fake)


def test_lifecycle_config_defaults_to_frozen_when_missing(monkeypatch):
    _patch_yaml(monkeypatch, None)
    c = lc.lifecycle_config()
    assert c["observation_freeze"] is True and c["auto_retrain"] is False and c["auto_promote"] is False


def test_real_yaml_is_frozen_and_disabled():
    c = lc.lifecycle_config()
    assert c == {"observation_freeze": True, "freeze_until_mature_days": 60, "auto_retrain": False, "auto_promote": False}


def test_freeze_state_without_session_has_none_mature_days(monkeypatch):
    _patch_yaml(monkeypatch, {"observation_freeze": True, "freeze_until_mature_days": 60, "auto_retrain": False, "auto_promote": False})
    st = lc.freeze_state()
    assert st["observation_freeze"] is True and st["mature_days"] is None and st["freeze_until_mature_days"] == 60


def test_append_and_read_audit_is_append_only(tmp_path, monkeypatch):
    _patch_yaml(monkeypatch, {"observation_freeze": True})
    e1 = lc.append_audit({"event": "register_challenger", "model_version": "m1", "actor": "t"}, root=tmp_path)
    e2 = lc.append_audit({"event": "refuse", "action": "promote", "model_version": "m1", "actor": None}, root=tmp_path)
    assert "at" in e1 and e1["freeze_state"]["observation_freeze"] is True
    lines = (tmp_path / lc.AUDIT_FILE).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2 and json.loads(lines[1])["event"] == "refuse"
    assert [e["event"] for e in lc.read_audit(root=tmp_path)] == ["register_challenger", "refuse"]
    assert lc.read_audit(root=tmp_path, limit=1) == [e2]
    assert lc.read_audit(root=tmp_path / "nope") == []


def test_guard_refuses_and_audits_when_frozen(tmp_path, monkeypatch):
    _patch_yaml(monkeypatch, {"observation_freeze": True})
    with pytest.raises(lc.FreezeError):
        lc.guard_champion_change("promote", "alice", "m1", root=tmp_path)
    (e,) = lc.read_audit(root=tmp_path)
    assert e["event"] == "refuse" and e["action"] == "promote" and e["actor"] == "alice" and e["model_version"] == "m1"


def test_guard_passes_when_unfrozen(tmp_path, monkeypatch):
    _patch_yaml(monkeypatch, {"observation_freeze": False})
    lc.guard_champion_change("promote", "alice", "m1", root=tmp_path)
    assert lc.read_audit(root=tmp_path) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_lifecycle.py -v`
Expected: FAIL（`ImportError: cannot import name 'lifecycle'`）

- [ ] **Step 3: Write implementation**

`backend/configs/mlentry/monitoring.yaml` 檔尾**新增**（其他區不動）：

```yaml
lifecycle:                        # Spec B §24：觀察期 lifecycle 骨架，全部 disabled；解凍只由人改 observation_freeze
  observation_freeze: true        # true → set_champion / promote / rollback 一律拒絕並寫 promotion_audit.jsonl
  freeze_until_mature_days: 60    # 宣告用，不自動解凍
  auto_retrain: false
  auto_promote: false
```

```python
# backend/app/mlentry/registry/lifecycle.py
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
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_lifecycle.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/configs/mlentry/monitoring.yaml backend/app/mlentry/registry/lifecycle.py backend/tests/test_mlentry_lifecycle.py
git commit -m "feat(mlentry): lifecycle 骨架——monitoring.yaml lifecycle 區（全 disabled）、fail-closed freeze 閘門、append-only promotion audit

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: versions.py 閘門＋registry＋rollback＋train_stack `register_as`

**Files:**
- Modify: `backend/app/mlentry/registry/versions.py`（`set_champion`、`promote`）
- Modify: `backend/app/mlentry/registry/lifecycle.py`（檔尾）
- Modify: `backend/app/mlentry/serving/train_stack.py:96-97, 186-189`
- Test: `backend/tests/test_mlentry_lifecycle.py`（新增）、`backend/tests/test_mlentry_serving.py`（fixture 修改＋新增）

**Interfaces:**
- Consumes: Task 1 全部
- Produces: `set_champion(stack, root=SERVING_ROOT, *, actor=None)`（凍結 → FreezeError；champion.json 多 `previous_model_version`）；`promote(..., root)`（先閘門）；`register_challenger(stack, evaluation: dict | None = None, note="", actor=None, root=SERVING_ROOT) -> dict`；`list_challengers(root=SERVING_ROOT) -> list[dict]`；`mark_challenger(model_version, status, note="", actor=None, root=SERVING_ROOT) -> dict`；`rollback(actor: str, reason: str, root=SERVING_ROOT) -> ServingStack`；`train_stack(..., set_as_champion=True, register_as: str | None = None, actor: str | None = None)`

- [ ] **Step 1: Write the failing tests**

在 `tests/test_mlentry_lifecycle.py` 檔尾加：

```python
from app.mlentry.registry import versions as reg


def _stack(tmp_path, mv: str) -> reg.ServingStack:
    s = reg.ServingStack(model_version=mv, calibration_version="c", policy_version="p", policy_name="policy_baseline_v1",
                         feature_version="f", label_version="l", universe_version="u", dataset_version="ds", split_version="s",
                         trained_through="2026-01-01", train_window_days=None, feature_names=["x"], tasks=["target_10d"],
                         calibration_methods={"target_10d": "none"}, code_commit="abc")
    s.save(tmp_path)
    return s


def test_set_champion_and_promote_refused_when_frozen(tmp_path, monkeypatch):
    _patch_yaml(monkeypatch, {"observation_freeze": True})
    s = _stack(tmp_path, "m1")
    with pytest.raises(lc.FreezeError):
        reg.set_champion(s, tmp_path)
    with pytest.raises(lc.FreezeError):
        reg.promote(s, {"eligible": True}, "alice", tmp_path)
    assert not (tmp_path / "champion.json").exists()
    assert [e["action"] for e in lc.read_audit(root=tmp_path)] == ["set_champion", "promote"]


def test_set_champion_records_previous_and_audit_when_unfrozen(tmp_path, monkeypatch):
    _patch_yaml(monkeypatch, {"observation_freeze": False})
    s1, s2 = _stack(tmp_path, "m1"), _stack(tmp_path, "m2")
    reg.set_champion(s1, tmp_path)
    c1 = json.loads((tmp_path / "champion.json").read_text(encoding="utf-8"))
    assert c1["model_version"] == "m1" and c1["previous_model_version"] is None
    reg.set_champion(s2, tmp_path, actor="bob")
    c2 = json.loads((tmp_path / "champion.json").read_text(encoding="utf-8"))
    assert c2["model_version"] == "m2" and c2["previous_model_version"] == "m1"
    ev = lc.read_audit(root=tmp_path)
    assert ev[-1]["event"] == "set_champion" and ev[-1]["from_model_version"] == "m1" and ev[-1]["actor"] == "bob"


def test_promote_unfrozen_still_requires_contract_and_approver(tmp_path, monkeypatch):
    _patch_yaml(monkeypatch, {"observation_freeze": False})
    s = _stack(tmp_path, "m1")
    with pytest.raises(PermissionError):
        reg.promote(s, {"eligible": False}, "alice", tmp_path)
    with pytest.raises(PermissionError):
        reg.promote(s, {"eligible": True}, "", tmp_path)
    out = reg.promote(s, {"eligible": True}, "alice", tmp_path)
    assert out.model_status == reg.STATUS_PROMOTED and lc.read_audit(root=tmp_path)[-1]["event"] == "promote"


def test_register_and_mark_challenger_idempotent_and_never_touches_champion(tmp_path, monkeypatch):
    _patch_yaml(monkeypatch, {"observation_freeze": True})
    s = _stack(tmp_path, "m9")
    r1 = lc.register_challenger(s, evaluation={"target_lift_at_5": 1.1}, note="first", actor="t", root=tmp_path)
    r2 = lc.register_challenger(s, evaluation={"target_lift_at_5": 1.2}, note="second", actor="t", root=tmp_path)
    ch = lc.list_challengers(root=tmp_path)
    assert len(ch) == 1 and ch[0]["model_version"] == "m9" and ch[0]["status"] == "registered"
    assert ch[0]["evaluation"]["target_lift_at_5"] == 1.2 and ch[0]["note"] == "second" and r1["registered_at"] == r2["registered_at"]
    assert not (tmp_path / "champion.json").exists()
    m = lc.mark_challenger("m9", "rejected", note="worse", actor="t", root=tmp_path)
    assert m["status"] == "rejected" and lc.list_challengers(root=tmp_path)[0]["status"] == "rejected"
    with pytest.raises(ValueError):
        lc.mark_challenger("m9", "bogus", root=tmp_path)
    with pytest.raises(KeyError):
        lc.mark_challenger("nope", "rejected", root=tmp_path)
    assert [e["event"] for e in lc.read_audit(root=tmp_path)] == ["register_challenger", "register_challenger", "mark_challenger"]
    doc = json.loads((tmp_path / lc.CHALLENGERS_FILE).read_text(encoding="utf-8"))
    assert doc["schema_version"] == 1


def test_rollback_frozen_refused_and_unfrozen_switches_back(tmp_path, monkeypatch):
    _patch_yaml(monkeypatch, {"observation_freeze": False})
    s1, s2 = _stack(tmp_path, "m1"), _stack(tmp_path, "m2")
    reg.set_champion(s1, tmp_path); reg.set_champion(s2, tmp_path)
    _patch_yaml(monkeypatch, {"observation_freeze": True})
    with pytest.raises(lc.FreezeError):
        lc.rollback("alice", "bad live", root=tmp_path)
    assert reg.load_champion(tmp_path).model_version == "m2"
    _patch_yaml(monkeypatch, {"observation_freeze": False})
    back = lc.rollback("alice", "bad live", root=tmp_path)
    assert back.model_version == "m1" and reg.load_champion(tmp_path).model_version == "m1"
    c = json.loads((tmp_path / "champion.json").read_text(encoding="utf-8"))
    assert c["previous_model_version"] == "m2"
    ev = lc.read_audit(root=tmp_path)[-1]
    assert ev["event"] == "rollback" and ev["model_version"] == "m1" and ev["from_model_version"] == "m2" and ev["reason"] == "bad live"
    with pytest.raises(ValueError):                              # previous 不存在
        (tmp_path / "champion.json").write_text(json.dumps({"model_version": "m1", "previous_model_version": None}), encoding="utf-8")
        lc.rollback("alice", "x", root=tmp_path)
```

在 `tests/test_mlentry_serving.py`：
- fixture `env` 的 `load_yaml` monkeypatch 改成也覆蓋 `monitoring` 的 lifecycle：

```python
    def _yaml(name):
        if name == "validation":
            return {"train_window_days": None}
        base = load_yaml(name)
        if name == "monitoring":
            base = {**base, "lifecycle": {**(base.get("lifecycle") or {}), "observation_freeze": False}}
        return base
    monkeypatch.setattr(train_stack, "load_yaml", _yaml)
    from app.mlentry.registry import lifecycle as _lc
    monkeypatch.setattr(_lc, "load_yaml", _yaml)
```

- 檔尾加：

```python
def test_train_stack_frozen_refuses_champion_but_registers_challenger(env, tmp_path, monkeypatch):
    from app.mlentry.registry import lifecycle as lc
    from app.mlentry.registry import versions as reg
    frozen = lambda name: {**load_yaml(name), "lifecycle": {"observation_freeze": True}} if name == "monitoring" \
        else ({"train_window_days": None} if name == "validation" else load_yaml(name))
    monkeypatch.setattr(train_stack, "load_yaml", frozen); monkeypatch.setattr(lc, "load_yaml", frozen)
    root = tmp_path / "serving2"
    with pytest.raises(lc.FreezeError):
        train_stack.train_stack(env["ds"], root=root, models_cfg=TINY)
    assert any(p.name == "stack.json" for p in root.rglob("stack.json"))      # artifact 已存
    assert not (root / "champion.json").exists()
    s = train_stack.train_stack(env["ds"], root=root, models_cfg=TINY, register_as="challenger", actor="t")
    assert not (root / "champion.json").exists()
    assert lc.list_challengers(root=root)[0]["model_version"] == s.model_version
    assert reg.load_champion(root) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_lifecycle.py tests/test_mlentry_serving.py -v -k "champion or challenger or rollback or promote"`
Expected: 新測試 FAIL（`AttributeError: register_challenger` / champion.json 被寫入 / `TypeError: register_as`）

- [ ] **Step 3: Write implementation**

`versions.py`：

```python
def set_champion(stack: ServingStack, root: Path = SERVING_ROOT, *, actor: str | None = None) -> None:
    """設定目前 serving champion（shadow 亦然）。凍結中拒絕（Spec B §24）；寫 previous_model_version 供 rollback。"""
    from .lifecycle import append_audit, guard_champion_change          # 延遲 import 避免循環
    guard_champion_change("set_champion", actor, stack.model_version, root)
    root.mkdir(parents=True, exist_ok=True)
    p = root / "champion.json"
    previous = json.loads(p.read_text(encoding="utf-8")).get("model_version") if p.exists() else None
    p.write_text(json.dumps({
        "model_version": stack.model_version, "calibration_version": stack.calibration_version,
        "policy_version": stack.policy_version, "policy_name": stack.policy_name,
        "model_status": stack.model_status, "deployment_mode": stack.deployment_mode,
        "promotion_eligible": stack.promotion_eligible, "set_at": datetime.now(timezone.utc).isoformat(),
        "previous_model_version": previous if previous != stack.model_version else None,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    append_audit({"event": "set_champion", "action": "set_champion", "actor": actor, "model_version": stack.model_version,
                  "from_model_version": previous, "reason": None}, root=root)
```

`promote(...)` 第一行加：
```python
    from .lifecycle import append_audit, guard_champion_change
    guard_champion_change("promote", approved_by or None, stack.model_version, root)
```
並在 `set_champion(stack, root)` 呼叫改為 `set_champion(stack, root, actor=approved_by)`，`return stack` 前加：
```python
    append_audit({"event": "promote", "action": "promote", "actor": approved_by, "model_version": stack.model_version,
                  "from_model_version": None, "reason": "promotion contract satisfied"}, root=root)
```

`lifecycle.py` 檔尾加：

```python
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
```

`train_stack.py`：簽名改為
```python
def train_stack(ds_dir: Path | None = None, policy_name: str = "policy_baseline_v1", root: Path = SERVING_ROOT,
                models_cfg: dict | None = None, set_as_champion: bool = True,
                register_as: str | None = None, actor: str | None = None) -> ServingStack:
```
檔尾 `if set_as_champion: set_champion(stack, root)` 改為：
```python
    if register_as == "challenger":
        from ..registry.lifecycle import register_challenger
        register_challenger(stack, evaluation=frozen, actor=actor, root=root)       # 不碰 champion（Spec B §24）
    elif set_as_champion:
        set_champion(stack, root, actor=actor)
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_lifecycle.py tests/test_mlentry_serving.py tests/test_mlentry_api.py -q`
Expected: 全數 PASS（既有 serving 測試因 fixture 解凍照常；api 測試不受影響）

- [ ] **Step 5: Commit**

```bash
git add backend/app/mlentry/registry/versions.py backend/app/mlentry/registry/lifecycle.py backend/app/mlentry/serving/train_stack.py backend/tests/test_mlentry_lifecycle.py backend/tests/test_mlentry_serving.py
git commit -m "feat(mlentry): set_champion/promote 過 freeze 閘門並寫 audit；previous_model_version；challenger registry（冪等）；rollback；train_stack register_as

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `diagnostics_frozen.py`——`topk_mask`、`lift_at_k`、`timing`

**Files:**
- Create: `backend/app/mlentry/evaluation/diagnostics_frozen.py`
- Test: `backend/tests/test_mlentry_diagnostics_frozen.py`

**Interfaces:**
- Consumes: `evaluation.policy_metrics.daily_aggregates`、`evaluation.volatility_control.daily_topk`
- Produces: `topk_mask(df, k) -> pd.Series[bool]`（gate_pass 且 score 非 NaN 的每日 Top-K）；`lift_at_k(df, ks=(1,3,5,10)) -> dict[str, dict]`（每 K：`row_weighted`／`day_weighted` 兩組 `{target_rate, market_target_rate, target_lift, stop_rate, market_stop_rate, stop_ratio, n, days_with_rec}`）；`timing(df, k=5) -> dict`（`p_target_le_3d/5d/10d, median_time_to_target, n, n_target`）；`DIAG_FLAGS = {"diagnostic_only": True, "not_used_for_policy": True}`
- `df` 必要欄：`signal_date, stock_id, gate_pass, recommendation_score, target, stop, event_type, return_10d, target_first_hit_day`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_mlentry_diagnostics_frozen.py
"""§18 唯讀 Frozen diagnostics：Lift@K（row/day-weighted）、timing、ranking、regime；全部 diagnostic only。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.mlentry.evaluation import diagnostics_frozen as dfz

TARGET, STOP, TIMEOUT = 1, 2, 4


def _frame():
    """2 天 × 6 檔。score 由高到低 s0..s5；gate_pass 前 4 檔。
    day1: target 於 s0,s1,s4；stop 於 s2。 day2: target 於 s1；stop 於 s0,s3。"""
    rows = []
    spec = {"2026-01-01": {"target": {"s0", "s1", "s4"}, "stop": {"s2"}, "hit": {"s0": 2, "s1": 7, "s4": 3}},
            "2026-01-02": {"target": {"s1"}, "stop": {"s0", "s3"}, "hit": {"s1": 4}}}
    for d, sp in spec.items():
        for i in range(6):
            sid = f"s{i}"
            t, s = sid in sp["target"], sid in sp["stop"]
            rows.append({"signal_date": d, "stock_id": sid, "gate_pass": i < 4, "recommendation_score": 1.0 - i * 0.1,
                         "target": float(t), "stop": float(s), "event_type": TARGET if t else (STOP if s else TIMEOUT),
                         "return_10d": 0.1 if t else (-0.05 if s else 0.0), "mfe_10d": 0.1, "mae_10d": -0.02,
                         "target_first_hit_day": sp["hit"].get(sid, np.nan)})
    return pd.DataFrame(rows)


def test_topk_mask_uses_gate_pass_and_score_order():
    df = _frame()
    m = dfz.topk_mask(df, 2)
    assert set(df.loc[m, "stock_id"]) == {"s0", "s1"} and int(m.sum()) == 4
    df.loc[0, "recommendation_score"] = np.nan                      # day1 s0 無分數 → 不進 Top-K
    m2 = dfz.topk_mask(df, 2)
    assert set(df.loc[m2 & (df["signal_date"] == "2026-01-01"), "stock_id"]) == {"s1", "s2"}


def test_lift_at_k_row_and_day_weighted():
    out = dfz.lift_at_k(_frame(), ks=(1, 2))
    assert set(out) == {"1", "2"} and out["1"]["diagnostic_only"] is True and out["1"]["not_used_for_policy"] is True
    rw = out["2"]["row_weighted"]
    # Top-2 = s0,s1 兩天共 4 列：target 3/4；市場基率 = 全 12 列 target 4/12
    assert rw["target_rate"] == pytest.approx(0.75) and rw["market_target_rate"] == pytest.approx(4 / 12)
    assert rw["target_lift"] == pytest.approx(0.75 / (4 / 12)) and rw["n"] == 4 and rw["days_with_rec"] == 2
    assert rw["stop_rate"] == pytest.approx(0.25) and rw["market_stop_rate"] == pytest.approx(3 / 12)
    dw = out["2"]["day_weighted"]
    # day1 Top-2 target 2/2, market 3/6；day2 Top-2 target 1/2, market 1/6 → 平均 0.75 vs (0.5+0.1667)/2
    assert dw["target_rate"] == pytest.approx(0.75) and dw["market_target_rate"] == pytest.approx((3 / 6 + 1 / 6) / 2)
    assert dw["target_lift"] == pytest.approx(0.75 / ((3 / 6 + 1 / 6) / 2))


def test_lift_at_k_empty_gate_days_and_k_larger_than_pool():
    df = _frame()
    df.loc[df["signal_date"] == "2026-01-02", "gate_pass"] = False
    out = dfz.lift_at_k(df, ks=(10,))
    rw = out["10"]["row_weighted"]
    assert rw["n"] == 4 and rw["days_with_rec"] == 1                   # 只有 day1 的 4 個 gate_pass
    assert out["10"]["day_weighted"]["days_with_rec"] == 1


def test_timing_top5():
    out = dfz.timing(_frame(), k=5)
    # Top-4（gate_pass 只有 4）×2 天 = 8 列；target: day1 s0(2),s1(7)；day2 s1(4) → 3 個
    assert out["n"] == 8 and out["n_target"] == 3
    assert out["p_target_le_3d"] == pytest.approx(1 / 8) and out["p_target_le_5d"] == pytest.approx(2 / 8)
    assert out["p_target_le_10d"] == pytest.approx(3 / 8) and out["median_time_to_target"] == pytest.approx(4.0)
    assert out["diagnostic_only"] is True


def test_timing_no_targets_is_null_median():
    df = _frame(); df["target"] = 0.0; df["event_type"] = TIMEOUT; df["target_first_hit_day"] = np.nan
    out = dfz.timing(df, k=5)
    assert out["n_target"] == 0 and out["median_time_to_target"] is None and out["p_target_le_10d"] == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_diagnostics_frozen.py -v`
Expected: FAIL（`ModuleNotFoundError: app.mlentry.evaluation.diagnostics_frozen`）

- [ ] **Step 3: Write implementation**

```python
# backend/app/mlentry/evaluation/diagnostics_frozen.py
"""§18 唯讀 evaluation diagnostics（Spec B）——只算 Frozen dev OOF，描述性，不得回頭改 Gate／Feature／Policy。

每個輸出區塊附 DIAG_FLAGS；樣本 n < MIN_N 的格子為 None。輸入 df 已由腳本合併好（OOF＋policy per_row＋dev outcomes＋
regime 特徵＋sector／mcap）。本檔不讀檔、不寫檔、不讀 holdout。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .policy_metrics import daily_aggregates
from .volatility_control import daily_topk

DIAG_FLAGS = {"diagnostic_only": True, "not_used_for_policy": True}
MIN_N = 30
HORIZONS = (3, 5, 10)


def _f(x) -> float | None:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(x) else x


def topk_mask(df: pd.DataFrame, k: int) -> pd.Series:
    """gate_pass 且 recommendation_score 非 NaN 的列，依分數每日 Top-K。"""
    pool = df["gate_pass"].astype(bool) & df["recommendation_score"].notna()
    sub = df.loc[pool, ["signal_date", "recommendation_score"]]
    m = pd.Series(False, index=df.index)
    if len(sub):
        m.loc[sub.index] = daily_topk(sub, "recommendation_score", k).to_numpy()
    return m


def _weighted_block(df: pd.DataFrame, mask: pd.Series) -> dict:
    sel, mkt = daily_aggregates(df, mask, 0.0), daily_aggregates(df, pd.Series(True, index=df.index), 0.0)
    n = float(sel["n"].sum())
    rw = {"target_rate": None, "market_target_rate": _f(mkt["target"].sum() / mkt["n"].sum()),
          "target_lift": None, "stop_rate": None, "market_stop_rate": _f(mkt["stop"].sum() / mkt["n"].sum()),
          "stop_ratio": None, "n": int(n), "days_with_rec": int((sel["n"] > 0).sum())}
    if n > 0:
        rw["target_rate"] = _f(sel["target"].sum() / n); rw["stop_rate"] = _f(sel["stop"].sum() / n)
        rw["target_lift"] = _f(rw["target_rate"] / rw["market_target_rate"]) if rw["market_target_rate"] else None
        rw["stop_ratio"] = _f(rw["stop_rate"] / rw["market_stop_rate"]) if rw["market_stop_rate"] else None
    days = sel.index[sel["n"] > 0]
    dw = {"target_rate": None, "market_target_rate": None, "target_lift": None, "stop_rate": None,
          "market_stop_rate": None, "stop_ratio": None, "n": int(n), "days_with_rec": int(len(days))}
    if len(days):
        s, m = sel.loc[days], mkt.loc[days]
        tr, mtr = (s["target"] / s["n"]).mean(), (m["target"] / m["n"]).mean()
        sr, msr = (s["stop"] / s["n"]).mean(), (m["stop"] / m["n"]).mean()
        dw.update({"target_rate": _f(tr), "market_target_rate": _f(mtr), "target_lift": _f(tr / mtr) if mtr else None,
                   "stop_rate": _f(sr), "market_stop_rate": _f(msr), "stop_ratio": _f(sr / msr) if msr else None})
    return {"row_weighted": rw, "day_weighted": dw}


def lift_at_k(df: pd.DataFrame, ks: tuple[int, ...] = (1, 3, 5, 10)) -> dict[str, dict]:
    return {str(k): {**_weighted_block(df, topk_mask(df, k)), **DIAG_FLAGS} for k in ks}


def timing(df: pd.DataFrame, k: int = 5) -> dict:
    rec = df.loc[topk_mask(df, k)]
    n = int(len(rec))
    hit = rec.loc[rec["target"] == 1, "target_first_hit_day"].astype(float)
    hit = hit[np.isfinite(hit)]
    out = {"k": k, "n": n, "n_target": int(len(hit)),
           "median_time_to_target": _f(hit.median()) if len(hit) else None, **DIAG_FLAGS}
    for h in HORIZONS:
        out[f"p_target_le_{h}d"] = _f((hit <= h).sum() / n) if n else None
    return out
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_diagnostics_frozen.py -v`
Expected: 5 passed。若 `test_timing_top5` 的 `n_target` 為 3 但 `p_target_le_3d` 不符，檢查 `hit` 是否只取 `target == 1` 列（s4 在 day1 target 但不在 Top-4？s4 不是 gate_pass，正確被排除）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/mlentry/evaluation/diagnostics_frozen.py backend/tests/test_mlentry_diagnostics_frozen.py
git commit -m "feat(mlentry): diagnostics_frozen——Top-K mask、Lift@K（row/day-weighted）、timing；全標 diagnostic only

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `ranking_diagnostics`、`regime_breakdown`、`build_diagnostics`

**Files:**
- Modify: `backend/app/mlentry/evaluation/diagnostics_frozen.py`（檔尾）
- Test: `backend/tests/test_mlentry_diagnostics_frozen.py`（新增）

**Interfaces:**
- Produces: `ranking_diagnostics(df, k=5) -> dict`（`precision_at_k, recall_at_k, ndcg_at_k, ic: {mean, std, positive_share, days}, n, days`）；`regime_breakdown(df, k=5) -> dict`（`market/volatility/breadth/mcap/industry` 各 `{groups: {name: {lift_at_5, stop_ratio_at_5, n, days}}, cuts}`，`mcap_basis`）；`build_diagnostics(df, policy_name, dataset_version, k=5) -> dict`
- `df` 額外欄（regime 用）：`market_ret_20d, market_volatility, breadth_ma20, mcap, sector_id`

- [ ] **Step 1: Write the failing tests**

檔尾加：

```python
def test_ranking_precision_recall_ndcg_ic():
    out = dfz.ranking_diagnostics(_frame(), k=2)
    # Top-2 = s0,s1：day1 target 2/2、day2 1/2 → precision 3/4
    assert out["precision_at_k"] == pytest.approx(0.75)
    # recall：day1 gate_pass 內 target = s0,s1（s4 非 gate_pass）→ 2/2；day2 = s1 → 1/1 → 平均 1.0
    assert out["recall_at_k"] == pytest.approx(1.0)
    # NDCG@2：day1 rel=[1,1] → DCG=1+1/log2(3)，IDCG 同 → 1；day2 rel=[0,1] → DCG=1/log2(3)，IDCG=1 → 0.6309；平均 0.8155
    assert out["ndcg_at_k"] == pytest.approx((1 + 1 / np.log2(3)) / 2, abs=1e-4)
    # IC：gate_pass 4 列，score 遞減；day1 ret=[.1,.1,-.05,0]、day2 ret=[-.05,.1,0,-.05]；Spearman 手算（tie 平均秩）
    assert out["ic"]["days"] == 0                                       # 每日 gate_pass < 5 列 → 略過
    assert out["diagnostic_only"] is True and out["n"] == 4 and out["days"] == 2


def test_ranking_ic_perfect_and_reverse():
    rows = [{"signal_date": "d", "stock_id": f"s{i}", "gate_pass": True, "recommendation_score": i, "target": 0.0, "stop": 0.0,
             "event_type": TIMEOUT, "return_10d": i * 0.01, "mfe_10d": 0.0, "mae_10d": 0.0, "target_first_hit_day": np.nan}
            for i in range(6)]
    df = pd.DataFrame(rows)
    assert dfz.ranking_diagnostics(df, k=3)["ic"]["mean"] == pytest.approx(1.0)
    df["return_10d"] = -df["return_10d"]
    ic = dfz.ranking_diagnostics(df, k=3)["ic"]
    assert ic["mean"] == pytest.approx(-1.0) and ic["positive_share"] == 0.0 and ic["days"] == 1


def _regime_frame(n_days=40, per_day=20, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(n_days):
        mret = (d - n_days / 2) / n_days                      # 單調：前半負、後半正
        for i in range(per_day):
            score = rng.random()
            t = rng.random() < (0.5 if score > 0.7 else 0.2)
            rows.append({"signal_date": f"2026-{1 + d // 28:02d}-{1 + d % 28:02d}", "stock_id": f"s{i}", "gate_pass": True,
                         "recommendation_score": score, "target": float(t), "stop": float((not t) and rng.random() < 0.3),
                         "event_type": TARGET if t else TIMEOUT, "return_10d": 0.1 if t else 0.0, "mfe_10d": 0.05, "mae_10d": -0.02,
                         "target_first_hit_day": 3.0 if t else np.nan,
                         "market_ret_20d": mret, "market_volatility": 0.01 + 0.02 * (d % 2), "breadth_ma20": 0.3 + 0.4 * (d % 3 == 0),
                         "mcap": float(i + 1) * 1e9, "sector_id": float(i % 3)})
    return pd.DataFrame(rows)


def test_regime_breakdown_groups_cuts_and_min_n():
    df = _regime_frame()
    out = dfz.regime_breakdown(df, k=5)
    assert set(out) == {"market", "volatility", "breadth", "mcap", "industry", "mcap_basis", "diagnostic_only", "not_used_for_policy"}
    assert out["mcap_basis"] == "current_company_profile"
    mk = out["market"]
    assert set(mk["groups"]) == {"bear", "neutral", "bull"} and len(mk["cuts"]) == 2 and mk["cuts"][0] < mk["cuts"][1]
    assert all(g["n"] >= dfz.MIN_N for g in mk["groups"].values())
    assert all(g["lift_at_5"] is not None for g in mk["groups"].values())
    assert set(out["volatility"]["groups"]) == {"low", "high"} and set(out["breadth"]["groups"]) == {"low", "high"}
    assert set(out["mcap"]["groups"]) == {"small", "mid", "large"}
    ind = out["industry"]["groups"]
    assert set(ind) <= {"0", "1", "2"} and all(g["n"] >= dfz.MIN_N for g in ind.values())
    small = dfz.regime_breakdown(df[df["signal_date"] < "2026-01-04"], k=5)
    assert all(g["lift_at_5"] is None and g["n"] < dfz.MIN_N for g in small["market"]["groups"].values())
    assert small["industry"]["groups"] == {}


def test_build_diagnostics_shape():
    out = dfz.build_diagnostics(_regime_frame(), "policy_baseline_v1", "ds_x", k=5)
    assert {"generated_at", "policy_name", "dataset_version", "k", "n_eval_rows", "n_days", "lift_at_k", "timing", "ranking", "regime"} <= set(out)
    assert out["diagnostic_only"] is True and out["not_used_for_policy"] is True
    assert set(out["lift_at_k"]) == {"1", "3", "5", "10"} and out["n_days"] == 40
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_diagnostics_frozen.py -v`
Expected: 新 4 個 FAIL（`AttributeError: ranking_diagnostics`）

- [ ] **Step 3: Write implementation**

檔尾加：

```python
def ranking_diagnostics(df: pd.DataFrame, k: int = 5) -> dict:
    """Precision@K（＝Top-K target rate）、Recall@K（每日）、NDCG@K（binary relevance）、每日 rank IC（gate_pass ≥ 5 列）。"""
    pool = df.loc[df["gate_pass"].astype(bool) & df["recommendation_score"].notna()]
    top = topk_mask(df, k)
    precision = _f(df.loc[top, "target"].mean()) if top.any() else None
    recalls, ndcgs, ics = [], [], []
    for _, g in pool.groupby("signal_date"):
        g = g.sort_values("recommendation_score", ascending=False)
        rel = g["target"].to_numpy(dtype=float)
        n_t = int(rel.sum())
        if n_t > 0:
            recalls.append(rel[:k].sum() / n_t)
            disc = 1 / np.log2(np.arange(2, min(k, len(rel)) + 2))
            dcg = float((rel[:k] * disc[: len(rel[:k])]).sum())
            idcg = float(disc[: min(k, n_t)].sum())
            ndcgs.append(dcg / idcg if idcg > 0 else 0.0)
        if len(g) >= 5:
            r = g["recommendation_score"].rank().corr(g["return_10d"].rank())
            if np.isfinite(r):
                ics.append(float(r))
    ic = np.array(ics, dtype=float)
    return {"k": k, "n": int(top.sum()), "days": int(pool["signal_date"].nunique()),
            "precision_at_k": precision,
            "recall_at_k": _f(np.mean(recalls)) if recalls else None,
            "ndcg_at_k": _f(np.mean(ndcgs)) if ndcgs else None,
            "ic": {"mean": _f(ic.mean()) if len(ic) else None, "std": _f(ic.std(ddof=0)) if len(ic) else None,
                   "positive_share": _f((ic > 0).mean()) if len(ic) else None, "days": int(len(ic))},
            **DIAG_FLAGS}


def _group_cell(df: pd.DataFrame, gmask: pd.Series, top: pd.Series) -> dict:
    sel = gmask & top
    n = int(sel.sum())
    cell = {"lift_at_5": None, "stop_ratio_at_5": None, "n": n, "days": int(df.loc[gmask, "signal_date"].nunique())}
    if n < MIN_N:
        return cell
    g = df.loc[gmask]
    bt, bs = g["target"].mean(), g["stop"].mean()
    s = df.loc[sel]
    cell["lift_at_5"] = _f(s["target"].mean() / bt) if bt > 0 else None
    cell["stop_ratio_at_5"] = _f(s["stop"].mean() / bs) if bs > 0 else None
    return cell


def _quantile_groups(x: pd.Series, labels: tuple[str, ...]) -> tuple[pd.Series, list[float]]:
    """依 dev 分位切 len(labels) 組；cuts 為內部切點（len(labels)-1 個）。NaN → 無組。"""
    qs = np.linspace(0, 1, len(labels) + 1)[1:-1]
    cuts = [float(v) for v in np.nanquantile(x.to_numpy(dtype=float), qs)]
    bins = [-np.inf, *cuts, np.inf]
    grp = pd.cut(x, bins=bins, labels=list(labels), include_lowest=True).astype(object)
    return grp, cuts


def regime_breakdown(df: pd.DataFrame, k: int = 5) -> dict:
    top = topk_mask(df, k)
    out: dict = {"mcap_basis": "current_company_profile", **DIAG_FLAGS}
    specs = (("market", "market_ret_20d", ("bear", "neutral", "bull")),
             ("volatility", "market_volatility", ("low", "high")),
             ("breadth", "breadth_ma20", ("low", "high")),
             ("mcap", "mcap", ("small", "mid", "large")))
    for key, col, labels in specs:
        if col not in df.columns or df[col].notna().sum() == 0:
            out[key] = {"groups": {lab: {"lift_at_5": None, "stop_ratio_at_5": None, "n": 0, "days": 0} for lab in labels},
                        "cuts": None, "column": col}
            continue
        grp, cuts = _quantile_groups(df[col], labels)
        out[key] = {"groups": {lab: _group_cell(df, grp == lab, top) for lab in labels}, "cuts": cuts, "column": col}
    ind: dict = {}
    if "sector_id" in df.columns:
        for sid, gmask in ((s, df["sector_id"] == s) for s in sorted(df["sector_id"].dropna().unique())):
            cell = _group_cell(df, gmask, top)
            if cell["n"] >= MIN_N:
                ind[str(int(sid))] = cell
    out["industry"] = {"groups": dict(sorted(ind.items(), key=lambda kv: -kv[1]["n"])), "cuts": None, "column": "sector_id"}
    return out


def build_diagnostics(df: pd.DataFrame, policy_name: str, dataset_version: str, k: int = 5) -> dict:
    from datetime import datetime, timezone
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "policy_name": policy_name,
            "dataset_version": dataset_version, "k": k, "n_eval_rows": int(len(df)), "n_days": int(df["signal_date"].nunique()),
            "lift_at_k": lift_at_k(df), "timing": timing(df, k), "ranking": ranking_diagnostics(df, k),
            "regime": regime_breakdown(df, k), **DIAG_FLAGS}
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_diagnostics_frozen.py -v`
Expected: 9 passed。若 `test_regime_breakdown_groups_cuts_and_min_n` 的某組 `n < 30`（隨機），把 `_regime_frame(n_days=40)` 改 60；不得改 `MIN_N`。

- [ ] **Step 5: Commit**

```bash
git add backend/app/mlentry/evaluation/diagnostics_frozen.py backend/tests/test_mlentry_diagnostics_frozen.py
git commit -m "feat(mlentry): diagnostics_frozen——ranking（Precision/Recall/NDCG/IC）、regime 拆解（dev 分位切點寫入輸出、n<30 為 null）、build_diagnostics

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: 唯讀腳本 `mlentry_frozen_diagnostics.py`

**Files:**
- Create: `backend/scripts/mlentry_frozen_diagnostics.py`
- Test: `backend/tests/test_mlentry_diagnostics_frozen.py`（新增）

**Interfaces:**
- Consumes: `scripts.mlentry_policy_report.load_frame`、`datasets.api.load_development`、`pyarrow` 讀 `per_row.parquet`、`prices.load_sector_map`、sqlite `company_profile`
- Produces: `assemble_frame(ds_dir: Path, policy_name: str, con) -> pd.DataFrame`；`main(argv) -> int`；輸出 `policy/<policy>/diagnostics_frozen.json`

- [ ] **Step 1: Write the failing test**

檔尾加：

```python
def test_script_refuses_policy_mismatch_and_writes_only_diagnostics(tmp_path, monkeypatch):
    import hashlib, json as _json
    from types import SimpleNamespace
    from scripts import mlentry_frozen_diagnostics as sc
    pdir = tmp_path / "policy" / "policy_baseline_v1"; pdir.mkdir(parents=True)
    (pdir / "metrics.json").write_text(_json.dumps({"policy": "policy_other"}), encoding="utf-8")
    monkeypatch.setattr(sc, "load_champion", lambda: SimpleNamespace(policy_name="policy_baseline_v1", dataset_version=tmp_path.name))
    monkeypatch.setattr(sc, "DEFAULT_ROOT", tmp_path.parent)
    assert sc.main(["--dataset", str(tmp_path)]) == 1                   # policy 不符 → 拒絕
    (pdir / "metrics.json").write_text(_json.dumps({"policy": "policy_baseline_v1"}), encoding="utf-8")
    monkeypatch.setattr(sc, "assemble_frame", lambda ds_dir, policy_name, con: _regime_frame())
    monkeypatch.setattr(sc, "_connect", lambda: None)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in pdir.iterdir()}
    assert sc.main(["--dataset", str(tmp_path)]) == 0
    out = _json.loads((pdir / "diagnostics_frozen.json").read_text(encoding="utf-8"))
    assert out["policy_name"] == "policy_baseline_v1" and out["dataset_version"] == tmp_path.name and out["diagnostic_only"] is True
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in pdir.iterdir() if p.name != "diagnostics_frozen.json"}
    assert before == after
    assert not (tmp_path / "holdout_access.log").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_diagnostics_frozen.py::test_script_refuses_policy_mismatch_and_writes_only_diagnostics -v`
Expected: FAIL（`ModuleNotFoundError: scripts.mlentry_frozen_diagnostics`）

- [ ] **Step 3: Write implementation**

```python
# backend/scripts/mlentry_frozen_diagnostics.py
"""§18 唯讀 Frozen diagnostics 轉存（Spec B）。

    python -m scripts.mlentry_frozen_diagnostics [--dataset DIR] [--k 5]

只讀 champion dataset 的 dev OOF、policy per_row、dev outcomes／features、sector map、company_profile 股本；
只寫 policy/<policy>/diagnostics_frozen.json。不讀 holdout、不跑 policy grid、不寫回任何 artifact。
Regime 切點由 dev 資料分位算出並寫進輸出；全部標 diagnostic_only。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.mlentry.data import prices  # noqa: E402
from app.mlentry.datasets import api  # noqa: E402
from app.mlentry.datasets.store import DEFAULT_ROOT  # noqa: E402
from app.mlentry.evaluation.diagnostics_frozen import build_diagnostics  # noqa: E402
from app.mlentry.registry.versions import load_champion  # noqa: E402

REGIME_FEATURES = ["market_ret_20d", "market_volatility", "breadth_ma20"]


def _connect():
    return sqlite3.connect(str(get_settings().db_path))


def assemble_frame(ds_dir: Path, policy_name: str, con) -> pd.DataFrame:
    from scripts.mlentry_policy_report import load_frame
    df = load_frame(ds_dir)                                             # OOF + atr_pct + outcomes（含 target/stop/matured）
    df = df[df["target"].notna() & (df["matured"] == 1)].copy()          # 評估列
    per_row = pq.read_table(str(ds_dir / "policy" / policy_name / "per_row.parquet")).to_pandas()
    df = df.merge(per_row[["sample_id", "p_target_vn", "p_stop_vn", "gate_pass", "recommendation_score", "rank", "recommended"]],
                  on="sample_id", how="left")
    dev = api.load_development(ds_dir, feature_columns=REGIME_FEATURES, outcome_columns=["target_first_hit_day"])
    df = df.merge(dev.features[["sample_id", *REGIME_FEATURES]], on="sample_id", how="left")
    df = df.merge(dev.outcomes[["sample_id", "target_first_hit_day"]], on="sample_id", how="left")
    sector = prices.load_sector_map(con)
    df["sector_id"] = sector.reindex(df["stock_id"].astype(str)).to_numpy()
    shares = pd.read_sql_query("SELECT stock_id, issued_shares FROM company_profile", con)
    shares = pd.Series(pd.to_numeric(shares["issued_shares"], errors="coerce").to_numpy(), index=shares["stock_id"].astype(str)).replace(0, float("nan"))
    close = _close_lookup(con, df)
    df["mcap"] = close.to_numpy() * shares.reindex(df["stock_id"].astype(str)).to_numpy()
    return df


def _close_lookup(con, df: pd.DataFrame) -> pd.Series:
    """每列 (stock_id, signal_date) 的收盤；只查用到的日期範圍，不掃全表。"""
    lo, hi = str(df["signal_date"].min()), str(df["signal_date"].max())
    px = pd.read_sql_query("SELECT stock_id, date, close FROM daily_prices WHERE date >= ? AND date <= ?", con, params=(lo, hi))
    key = pd.MultiIndex.from_arrays([px["stock_id"].astype(str), px["date"].astype(str)])
    s = pd.Series(px["close"].to_numpy(dtype=float), index=key)
    want = pd.MultiIndex.from_arrays([df["stock_id"].astype(str), df["signal_date"].astype(str)])
    return s.reindex(want)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--dataset", default=None); ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args(argv)
    champ = load_champion()
    if champ is None:
        print("no champion; abort"); return 1
    ds_dir = Path(args.dataset) if args.dataset else Path(DEFAULT_ROOT) / champ.dataset_version
    pdir = ds_dir / "policy" / champ.policy_name
    metrics = json.loads((pdir / "metrics.json").read_text(encoding="utf-8"))
    if metrics.get("policy") != champ.policy_name:
        print(f"metrics.json policy={metrics.get('policy')} != champion policy={champ.policy_name}; abort"); return 1
    con = _connect()
    try:
        df = assemble_frame(ds_dir, champ.policy_name, con)
    finally:
        if con is not None:
            con.close()
    out = build_diagnostics(df, champ.policy_name, ds_dir.name, k=args.k)
    (pdir / "diagnostics_frozen.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    lk = out["lift_at_k"]
    print("Lift@K row-weighted:", {k: (v["row_weighted"]["target_lift"]) for k, v in lk.items()})
    print("timing:", {k: v for k, v in out["timing"].items() if k.startswith("p_target") or k == "median_time_to_target"})
    print("ic:", out["ranking"]["ic"], "ndcg:", out["ranking"]["ndcg_at_k"])
    for key in ("market", "volatility", "breadth", "mcap", "industry"):
        print(key, {g: (c["n"], c["lift_at_5"]) for g, c in out["regime"][key]["groups"].items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`_close_lookup` 對 `daily_prices` 用日期範圍參數化查詢（dev 期間約 6 年，一次讀取可接受；不是每列查一次）。

- [ ] **Step 4: Run test, then run on real data**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_diagnostics_frozen.py -v`
Expected: 10 passed

Run: `.venv/Scripts/python.exe -m scripts.mlentry_frozen_diagnostics`
Expected: 印出 Lift@1/3/5/10（Lift@5 應 ≈ 1.23，與 B9 一致；Lift@10 較低）、timing、IC、五個 regime 各組 n；`policy/policy_baseline_v1/diagnostics_frozen.json` 產生；`holdout_access.log` 不存在（`ls data/mlentry/<ds>/`）；dataset 目錄其他檔案不變。若 `market_ret_20d` 等欄不在 dev features（KeyError），改讀 `features.yaml` 確認欄名並回報，不要用近似欄替代。

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/mlentry_frozen_diagnostics.py backend/tests/test_mlentry_diagnostics_frozen.py
git commit -m "feat(mlentry): mlentry_frozen_diagnostics——唯讀組裝 OOF＋policy＋dev outcomes／regime 特徵，寫 diagnostics_frozen.json（policy 對齊 champion、不讀 holdout）

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: API——`/health` `diagnostics_frozen`＋`live_gate`、`/status` `lifecycle`

**Files:**
- Modify: `backend/app/api/routes_mlentry.py`
- Test: `backend/tests/test_mlentry_api.py`

**Interfaces:**
- Consumes: `registry.lifecycle.freeze_state`、`list_challengers`、`read_audit`；`_frozen_stats` 同款讀檔法
- Produces: `/health.diagnostics_frozen: dict | null`、`/health.live_gate: {mature_days, decide_at: 60, live_unlocked}`、`/status.lifecycle: {observation_freeze, freeze_until_mature_days, mature_days, auto_retrain, auto_promote, challengers_count, previous_model_version, last_audit_event}`

- [ ] **Step 1: Write the failing tests**

檔尾加：

```python
def test_health_diagnostics_frozen_and_live_gate(client, monkeypatch):
    monkeypatch.setattr(routes_mlentry, "load_champion", lambda: None)
    j = client.get("/api/mlentry/health?limit=5").json()
    assert j["diagnostics_frozen"] is None                                  # 無 champion → null，不 500
    assert j["live_gate"]["decide_at"] == 60 and j["live_gate"]["live_unlocked"] is False
    assert isinstance(j["live_gate"]["mature_days"], int)


def test_health_diagnostics_frozen_reads_file(client, monkeypatch, tmp_path):
    from types import SimpleNamespace
    ds = tmp_path / "ds_t"; (ds / "policy" / "policy_baseline_v1").mkdir(parents=True)
    (ds / "policy" / "policy_baseline_v1" / "diagnostics_frozen.json").write_text(json.dumps({"diagnostic_only": True, "lift_at_k": {"5": {}}}), encoding="utf-8")
    fake = SimpleNamespace(dataset_version="ds_t", policy_name="policy_baseline_v1", frozen_validation={}, promotion_check={},
                           model_version="m", calibration_version="c", policy_version="p", feature_version="f", label_version="l",
                           trained_through="2026-01-01", model_status="RESEARCH_SHADOW", deployment_mode="SHADOW", promotion_eligible=False)
    monkeypatch.setattr(routes_mlentry, "load_champion", lambda: fake)
    monkeypatch.setattr(routes_mlentry.ds_api, "latest_dataset_dir", lambda: ds)
    j = client.get("/api/mlentry/health?limit=5").json()
    assert j["diagnostics_frozen"]["diagnostic_only"] is True and "5" in j["diagnostics_frozen"]["lift_at_k"]


def test_status_lifecycle_block(client, monkeypatch):
    monkeypatch.setattr(routes_mlentry, "load_champion", lambda: None)
    j = client.get("/api/mlentry/status").json()
    lf = j["lifecycle"]
    assert lf["observation_freeze"] is True and lf["auto_retrain"] is False and lf["auto_promote"] is False
    assert lf["freeze_until_mature_days"] == 60 and isinstance(lf["mature_days"], int)
    assert isinstance(lf["challengers_count"], int) and "previous_model_version" in lf and "last_audit_event" in lf
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_api.py -v`
Expected: 新 3 個 FAIL（`KeyError: 'diagnostics_frozen'` / `'lifecycle'`）

- [ ] **Step 3: Write implementation**

`routes_mlentry.py`：
1. import 加 `from ..mlentry.registry import lifecycle as lc`。
2. `_frozen_stats` 之後加：
```python
def _policy_file(s, name: str) -> dict | None:
    """讀 data/mlentry/<champion dataset>/policy/<policy>/<name>；缺檔／壞檔回 None。"""
    if s is None:
        return None
    try:
        p = Path(ds_api.latest_dataset_dir()).parent / s.dataset_version / "policy" / s.policy_name / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except Exception:
        return None


def _lifecycle_block(session: Session) -> dict:
    try:
        st = lc.freeze_state(session)
    except Exception:
        st = {"observation_freeze": True, "freeze_until_mature_days": 60, "auto_retrain": False, "auto_promote": False, "mature_days": None}
    try:
        champ = json.loads((lc.SERVING_ROOT / "champion.json").read_text(encoding="utf-8"))
    except Exception:
        champ = {}
    try:
        audit = lc.read_audit(limit=1)
    except Exception:
        audit = []
    try:
        n_ch = len(lc.list_challengers())
    except Exception:
        n_ch = 0
    return {**{k: st.get(k) for k in ("observation_freeze", "freeze_until_mature_days", "auto_retrain", "auto_promote")},
            "mature_days": int(st.get("mature_days") or 0), "challengers_count": n_ch,
            "previous_model_version": champ.get("previous_model_version"),
            "last_audit_event": ({"at": audit[-1].get("at"), "event": audit[-1].get("event"), "action": audit[-1].get("action"),
                                  "model_version": audit[-1].get("model_version")} if audit else None)}
```
3. `/status` 回傳 dict 加 `"lifecycle": _lifecycle_block(session)`。
4. `/health` 回傳 dict 加：
```python
            "diagnostics_frozen": _policy_file(s, "diagnostics_frozen.json"),
            "live_gate": {"mature_days": int(live.get("matured_days") or 0), "decide_at": 60,
                          "live_unlocked": int(live.get("matured_days") or 0) >= 60},
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_api.py tests/test_mlentry_lifecycle.py -q`
Expected: 全數 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes_mlentry.py backend/tests/test_mlentry_api.py
git commit -m "feat(mlentry): API——/health diagnostics_frozen（缺檔 null）＋live_gate；/status lifecycle（凍結旗標、challengers、previous、last audit）

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: 前端——診斷折疊區、lifecycle 列

**Files:**
- Modify: `frontend/src/api/client.ts`（`MLEntryHealth`、`MLEntryStatus`）
- Modify: `frontend/src/components/MLEntryHealth.tsx`（最後一個 `</section>` 之後、`</div>` 之前）
- Modify: `frontend/src/components/MLEntrySystem.tsx`（Serving stack `<details>` 內 `final_holdout_access` 列之後）

**Interfaces:**
- Consumes: Task 6 JSON
- Produces: `MLEntryFrozenDiagnostics`、`MLEntryLifecycle` 型別；`MLEntryHealth.diagnostics_frozen/live_gate`；`MLEntryStatus.lifecycle`

- [ ] **Step 1: 型別**

`client.ts`，`MLEntryHealth` 之前加：

```ts
export type MLEntryDiagCell = { lift_at_5: number | null; stop_ratio_at_5: number | null; n: number; days: number };
export type MLEntryRegimeBlock = { groups: Record<string, MLEntryDiagCell>; cuts: number[] | null; column: string };
export type MLEntryFrozenDiagnostics = {
  generated_at: string; policy_name: string; dataset_version: string; k: number; n_eval_rows: number; n_days: number;
  diagnostic_only: boolean; not_used_for_policy: boolean;
  lift_at_k: Record<string, { row_weighted: Record<string, number | null>; day_weighted: Record<string, number | null> }>;
  timing: Record<string, number | null | boolean>;
  ranking: { precision_at_k: number | null; recall_at_k: number | null; ndcg_at_k: number | null; n: number; days: number;
             ic: { mean: number | null; std: number | null; positive_share: number | null; days: number } };
  regime: { market: MLEntryRegimeBlock; volatility: MLEntryRegimeBlock; breadth: MLEntryRegimeBlock; mcap: MLEntryRegimeBlock;
            industry: MLEntryRegimeBlock; mcap_basis: string };
};
export type MLEntryLifecycle = {
  observation_freeze: boolean; freeze_until_mature_days: number; mature_days: number; auto_retrain: boolean; auto_promote: boolean;
  challengers_count: number; previous_model_version: string | null;
  last_audit_event: { at: string | null; event: string | null; action: string | null; model_version: string | null } | null;
};
```

`MLEntryHealth` 加 `diagnostics_frozen: MLEntryFrozenDiagnostics | null; live_gate: { mature_days: number; decide_at: number; live_unlocked: boolean };`。
`MLEntryStatus` 加 `lifecycle: MLEntryLifecycle;`。

- [ ] **Step 2: 體檢頁折疊區**

`MLEntryHealth.tsx` import 改為 `import type { MLEntryConvergenceRow, MLEntryDiagCell, MLEntryHealth as HealthT, MLEntryRegimeBlock } from "../api/client";`，在 `fmt` 之後加：

```tsx
// 診斷（只看，不用於 policy）：Frozen dev OOF；Live 欄一律「等待 60D」。數值與切點全來自後端 JSON。
const DIAG_TAG = "Diagnostic only · Not used for policy selection";
const x2 = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(2)}×`);
const p1 = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);
const n3 = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(3));

function DiagTable({ title, head, rows, liveLabel }: { title: string; head: string[]; rows: (string | number | null)[][]; liveLabel: string }) {
  return (
    <div className="rounded-lg border border-gray-800">
      <div className="flex items-center justify-between px-3 py-1.5 text-xs">
        <span className="font-semibold text-gray-300">{title}</span><span className="text-amber-300/80">{DIAG_TAG}</span>
      </div>
      <table className="w-full text-sm">
        <thead className="bg-gray-900 text-left text-xs text-gray-400"><tr>{head.map((h) => <th key={h} className="px-3 py-1.5">{h}</th>)}<th className="px-3 py-1.5">Live</th></tr></thead>
        <tbody>{rows.map((r, i) => (
          <tr key={i} className="border-t border-gray-800/60 tabular-nums">
            {r.map((c, j) => <td key={j} className="px-3 py-1.5" title={c == null ? "n<30" : undefined}>{c == null ? "—" : String(c)}</td>)}
            <td className="px-3 py-1.5 text-gray-500">{liveLabel}</td>
          </tr>))}</tbody>
      </table>
    </div>
  );
}

function regimeRows(b: MLEntryRegimeBlock): (string | number | null)[][] {
  return Object.entries(b.groups).map(([g, c]: [string, MLEntryDiagCell]) => [g, x2(c.lift_at_5), c.stop_ratio_at_5 == null ? null : c.stop_ratio_at_5.toFixed(2), c.n, c.days]);
}
```

在最後一個 `</section>` 之後、外層 `</div>` 之前加：

```tsx
      <details className="rounded-lg border border-gray-800 bg-gray-900/60 p-4">
        <summary className="cursor-pointer text-sm font-semibold">診斷（只看，不用於 policy）<span className="ml-2 text-xs font-normal text-amber-300/80">{DIAG_TAG}</span></summary>
        {(() => {
          const d = health.diagnostics_frozen; const live = `等待 60D（目前 ${health.live_gate.mature_days}）`;
          if (!d) return <div className="mt-2 text-sm text-gray-500">尚未產生：執行 <code>python -m scripts.mlentry_frozen_diagnostics</code></div>;
          const lk = Object.entries(d.lift_at_k).map(([k, v]) => [`@${k}`, x2(v.row_weighted.target_lift), x2(v.day_weighted.target_lift),
            v.row_weighted.stop_ratio == null ? null : v.row_weighted.stop_ratio.toFixed(2), v.row_weighted.n]);
          const tm = [["P(target ≤ 3D)", p1(d.timing.p_target_le_3d as number | null)], ["P(target ≤ 5D)", p1(d.timing.p_target_le_5d as number | null)],
            ["P(target ≤ 10D)", p1(d.timing.p_target_le_10d as number | null)], ["Median time-to-target", d.timing.median_time_to_target == null ? null : `${d.timing.median_time_to_target} 日`]];
          const rk = [["Precision@K", p1(d.ranking.precision_at_k)], ["Recall@K", p1(d.ranking.recall_at_k)], ["NDCG@K", n3(d.ranking.ndcg_at_k)],
            ["IC mean／std", `${n3(d.ranking.ic.mean)}／${n3(d.ranking.ic.std)}`], ["IC>0 日比例", p1(d.ranking.ic.positive_share)]];
          return (
            <div className="mt-3 space-y-3 text-xs text-gray-400">
              <div>Frozen dev OOF・{d.dataset_version}・{d.policy_name}・K={d.k}・{d.n_days} 日 {d.n_eval_rows} 列・{d.generated_at}</div>
              <DiagTable title="Lift@K" head={["K", "Lift（row）", "Lift（day）", "StopRatio", "n"]} rows={lk} liveLabel={live} />
              <DiagTable title="Timing" head={["指標", "Frozen"]} rows={tm} liveLabel={live} />
              <DiagTable title="Ranking" head={["指標", "Frozen"]} rows={rk} liveLabel={live} />
              {(["market", "volatility", "breadth", "mcap", "industry"] as const).map((key) => (
                <DiagTable key={key} title={`Regime：${key}${d.regime[key].cuts ? `（切點 ${d.regime[key].cuts!.map((c) => c.toFixed(3)).join(" / ")}）` : ""}${key === "mcap" ? `・${d.regime.mcap_basis}` : ""}`}
                           head={["組", "Lift@5", "StopRatio@5", "n", "days"]} rows={regimeRows(d.regime[key])} liveLabel={live} />
              ))}
            </div>
          );
        })()}
      </details>
```

- [ ] **Step 3: 系統狀態頁 lifecycle 列**

`MLEntrySystem.tsx`，`<Row k="final_holdout_access" .../>` 之後加：

```tsx
          {status.lifecycle && (<>
            <Row k="observation_freeze" v={status.lifecycle.observation_freeze
              ? <span className="text-amber-300">true・觀察凍結中，promotion 已鎖（至 {status.lifecycle.freeze_until_mature_days} 成熟日，目前 {status.lifecycle.mature_days}）</span>
              : "false"} />
            <Row k="auto_retrain / auto_promote" v={`${status.lifecycle.auto_retrain} / ${status.lifecycle.auto_promote}`} />
            <Row k="challengers" v={String(status.lifecycle.challengers_count)} />
            <Row k="previous_model_version" v={status.lifecycle.previous_model_version ?? "—"} />
            <Row k="last_audit_event" v={status.lifecycle.last_audit_event ? `${status.lifecycle.last_audit_event.event}${status.lifecycle.last_audit_event.action ? `(${status.lifecycle.last_audit_event.action})` : ""} ${status.lifecycle.last_audit_event.at ?? ""}` : "—"} />
          </>)}
```

- [ ] **Step 4: Build**

Run（`frontend/`）：`npm run build`
Expected: 成功。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/components/MLEntryHealth.tsx frontend/src/components/MLEntrySystem.tsx
git commit -m "feat(level1-ui): 體檢頁「診斷（只看，不用於 policy）」折疊區（Lift@K/Timing/Ranking/Regime，Live 欄等待 60D）；系統頁 lifecycle 列（凍結琥珀）

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: 實機驗證與收尾

- [ ] **Step 1: 全部後端測試＋freeze 檢查**

Run（`backend/`）：`.venv/Scripts/python.exe -m pytest tests/test_mlentry_lifecycle.py tests/test_mlentry_diagnostics_frozen.py tests/test_mlentry_serving.py tests/test_mlentry_api.py tests/test_mlentry_diagnostics.py tests/test_mlentry_audit.py tests/test_mlentry_labels.py -q`
Expected: 全數 PASS。

Run（repo 根）：`git diff develop..HEAD --numstat -- backend/configs`
Expected: 只有 `monitoring.yaml`，且只有新增行。`git diff develop..HEAD -- backend/app/mlentry/monitoring/health.py backend/app/mlentry/labels backend/app/mlentry/recommendation backend/app/mlentry/models` 為空。champion 目錄 17 個既有檔案 sha256 不變（Spec A 的 before 清單若已刪，重新以 `git show` 無法取得——改為確認 `git status` 無追蹤變更且 `ls` 只多 sidecar 與可能的 `challengers.json`／`promotion_audit.jsonl` 在 serving root，不在 stack 目錄內）。

- [ ] **Step 2: 實機 API**

`preview_start` name `backend-verify`（:8001）：
- `GET /api/mlentry/health` → `diagnostics_frozen` 非 null（Task 5 已產生）、`live_gate.live_unlocked false`。
- `GET /api/mlentry/status` → `lifecycle.observation_freeze true`、`challengers_count 0`、`last_audit_event null`（尚無事件）。

- [ ] **Step 3: 瀏覽器**

體檢頁：診斷折疊區展開後四類表齊全，每表右上 `Diagnostic only · Not used for policy selection`，Live 欄「等待 60D（目前 N）」，regime 表標題含切點與 `current_company_profile`。系統狀態頁 Serving stack 折疊區出現琥珀「觀察凍結中，promotion 已鎖」列。console 無錯誤；360px 無溢出；截圖留證；還原 desktop、停 preview。

- [ ] **Step 4: 回報**

附截圖、真實 `diagnostics_frozen.json` 摘要（Lift@1/3/5/10、IC、各 regime n）、freeze 檢查結果。
