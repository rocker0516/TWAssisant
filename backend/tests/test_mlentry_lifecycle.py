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


def test_lifecycle_config_treats_null_as_frozen(tmp_path, monkeypatch):
    _patch_yaml(monkeypatch, {"observation_freeze": None, "auto_retrain": None})
    c = lc.lifecycle_config()
    assert c["observation_freeze"] is True and c["auto_retrain"] is False
    with pytest.raises(lc.FreezeError):
        lc.guard_champion_change("promote", "alice", "m1", root=tmp_path)


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
    import hashlib
    sha = lambda: hashlib.sha256((tmp_path / "m1" / "stack.json").read_bytes()).hexdigest()
    before = sha()
    with pytest.raises(lc.FreezeError):
        reg.set_champion(s, tmp_path)
    with pytest.raises(lc.FreezeError):
        reg.promote(s, {"eligible": True}, "alice", tmp_path)
    assert sha() == before                                  # 凍結 promote 不得動 stack.json
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


def test_promote_same_version_keeps_previous_so_rollback_still_works(tmp_path, monkeypatch):
    _patch_yaml(monkeypatch, {"observation_freeze": False})
    s1, s2 = _stack(tmp_path, "m1"), _stack(tmp_path, "m2")
    reg.set_champion(s1, tmp_path); reg.set_champion(s2, tmp_path)
    reg.promote(s2, {"eligible": True}, "alice", tmp_path)
    c = json.loads((tmp_path / "champion.json").read_text(encoding="utf-8"))
    assert c["model_version"] == "m2" and c["previous_model_version"] == "m1"
    assert lc.rollback("alice", "x", root=tmp_path).model_version == "m1"
