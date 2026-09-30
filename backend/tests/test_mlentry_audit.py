# backend/tests/test_mlentry_audit.py
"""§22 provenance audit：canonical hash、來源 view metadata、API 白名單。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.mlentry.serving import audit


def _stack(tmp_path):
    (tmp_path / "stack.json").write_text(json.dumps({"b": 1, "a": [1, 2]}, indent=2), encoding="utf-8")
    (tmp_path / "artifacts.json").write_text(json.dumps({"target_10d": {"kind": "binary"}}), encoding="utf-8")
    return SimpleNamespace(dir=tmp_path, policy_name="policy_baseline_v1", code_commit="abc1234")


def test_serving_stack_hash_is_semantic(tmp_path):
    s = _stack(tmp_path)
    h1 = audit.serving_stack_hash(tmp_path)
    (tmp_path / "stack.json").write_text(json.dumps({"a": [1, 2], "b": 1}), encoding="utf-8")   # 重排＋去縮排
    assert audit.serving_stack_hash(tmp_path) == h1 and len(h1) == 12
    (tmp_path / "stack.json").write_text(json.dumps({"a": [1, 3], "b": 1}), encoding="utf-8")
    assert audit.serving_stack_hash(tmp_path) != h1
    assert audit.serving_stack_hash(tmp_path / "nope") is None
    assert s.policy_name == "policy_baseline_v1"


def test_config_hashes_real_configs_are_stable_12hex():
    h = audit.config_hashes("policy_baseline_v1")
    assert set(h) == {"monitoring", "policy", "features"} and all(len(v) == 12 for v in h.values())
    assert audit.config_hashes("no_such_policy")["policy"] is None


def test_build_audit_shape_and_snapshot_id(tmp_path):
    s = _stack(tmp_path)
    sources = {"daily_prices": {"max_business_date": "2026-09-29", "max_available_at": None, "rows_visible_at_as_of": 1783},
               "attention_listings": {"max_business_date": "2026-09-24", "max_available_at": "2026-09-28 21:49:00", "rows_visible_at_as_of": 163}}
    a = audit.build_audit("2026-09-29", "2026-09-29", s, sources)
    assert set(a) == {"requested_as_of", "feature_snapshot_as_of", "data_snapshot_id", "sources", "serving_stack_hash",
                      "config_hashes", "runtime", "code_commit"}
    assert a["sources"]["daily_prices"]["max_available_at"] is None
    assert a["data_snapshot_id"] == audit.build_audit("2026-09-29", "2026-09-29", s, json.loads(json.dumps(sources)))["data_snapshot_id"]
    sources["daily_prices"]["rows_visible_at_as_of"] = 1784
    assert a["data_snapshot_id"] != audit.build_audit("2026-09-29", "2026-09-29", s, sources)["data_snapshot_id"]
    assert {"python", "lightgbm", "pandas", "numpy", "hostname"} <= set(a["runtime"])


def test_audit_summary_whitelist_and_mismatch():
    full = {"requested_as_of": "2026-09-29", "feature_snapshot_as_of": "2026-09-28", "data_snapshot_id": "x", "serving_stack_hash": "y",
            "code_commit": "z", "runtime": {"hostname": "SECRET-HOST"}, "sources": {}, "config_hashes": {}}
    s = audit.audit_summary(full)
    assert s == {"requested_as_of": "2026-09-29", "feature_snapshot_as_of": "2026-09-28", "data_snapshot_id": "x",
                 "serving_stack_hash": "y", "code_commit": "z", "as_of_mismatch": True}
    assert "SECRET-HOST" not in json.dumps(s)
    assert audit.audit_summary(None) is None
    assert audit.audit_summary({"error_type": "OSError"})["as_of_mismatch"] is False


def test_safe_build_audit_returns_error_type_only(caplog):
    with caplog.at_level("ERROR"):
        out = audit.safe_build_audit("2026-09-29", "2026-09-29", SimpleNamespace(dir=None, policy_name="p", code_commit="c"), {})
    assert out == {"error_type": "TypeError"}                    # Path(None) 拋 TypeError；訊息只在 log
    assert any("build_audit failed" in m for m in caplog.messages)


def test_config_hashes_malformed_yaml_degrades_per_key(tmp_path):
    (tmp_path / "monitoring.yaml").write_text("a: [unclosed", encoding="utf-8")
    h = audit.config_hashes("policy_baseline_v1", config_dir=tmp_path)
    assert h == {"monitoring": None, "policy": None, "features": None}


def test_serving_stack_hash_malformed_json_is_none(tmp_path):
    _stack(tmp_path)
    (tmp_path / "stack.json").write_text("{not json", encoding="utf-8")
    assert audit.serving_stack_hash(tmp_path) is None


def test_build_audit_survives_malformed_stack(tmp_path):
    s = _stack(tmp_path)
    (tmp_path / "stack.json").write_text("{not json", encoding="utf-8")
    a = audit.build_audit("2026-09-29", "2026-09-29", s, {})
    assert "error_type" not in a and len(a) == 8 and a["serving_stack_hash"] is None
