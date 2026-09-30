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
