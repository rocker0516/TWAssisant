"""mlentry API：status / board / health / runs 的形狀與 shadow 定位欄位；空 DB 也不得 500。"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app import auth, main
from app.api import routes_mlentry
from app.storage import models
from app.storage.database import init_db, session_scope

RUN_ID = "2019-01-02_test_stack_000000000000"
SIGNAL = date(2019, 1, 2)                       # 遠早於真實資料


@pytest.fixture(scope="module", autouse=True)
def _db():
    init_db()
    with session_scope() as s:
        s.merge(models.MLEntryRun(run_id=RUN_ID, signal_date=SIGNAL, as_of_timestamp=datetime(2019, 1, 2, 21, 30),
                                  dataset_version="ds_t", universe_version="u", feature_version="f", label_version="l",
                                  model_version="test_stack", calibration_version="c", policy_version="p", policy_name="policy_baseline_v1",
                                  model_status="RESEARCH_SHADOW", deployment_mode="SHADOW", promotion_eligible=False, code_commit="abc",
                                  status="OK", no_trade=False, no_trade_reason=None, universe_count=3, qualified_count=2,
                                  recommendation_count=1, health_json=json.dumps({"data_quality": {"ok": True}, "feature_health": {"ok": True, "n_drifted": 0, "drifted_psi": {}},
                                                                   "recommendation": {"qualified_count": 2},
                                                                   "diagnostics": {"freshness": {"evaluated": True, "attention": True, "sources": {}},
                                                                                   "sanity": {"evaluated": False, "attention": False, "reason": "NO_DATA"},
                                                                                   "feature_shift": {"evaluated": False, "attention": False, "error_type": "ValueError", "error": "should be stripped"},
                                                                                   "recommendation": {"evaluated": True, "attention": False}}}),
                                  audit_json=json.dumps({"requested_as_of": "2019-01-02", "feature_snapshot_as_of": "2019-01-02", "data_snapshot_id": "abcdef012345",
                                                         "serving_stack_hash": "0123456789ab", "code_commit": "abc", "runtime": {"hostname": "SECRET-HOST"},
                                                         "sources": {}, "config_hashes": {}})))
        s.flush()                                   # 無 ORM relationship：先落 run 列再寫 FK 子列
        for sid, rec, rank, gp in (("2330", True, 1, True), ("1101", False, 2, True), ("2317", False, None, False)):
            s.merge(models.MLEntryPrediction(run_id=RUN_ID, stock_id=sid, signal_date=SIGNAL, p_target_10d=0.2, p_stop_10d=0.3,
                                             p_target_vn=0.96, p_stop_vn=0.1, gate_pass=gp, gate_failure_reason=0 if gp else 8,
                                             recommendation_score=0.9, rank=rank, recommended=rec))
    yield
    with session_scope() as s:
        s.query(models.MLEntryPrediction).filter_by(run_id=RUN_ID).delete()
        s.query(models.MLEntryRun).filter_by(run_id=RUN_ID).delete()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "auth_enabled", lambda: False)
    return TestClient(main.app)


def test_status_carries_shadow_positioning(client, monkeypatch):
    monkeypatch.setattr(routes_mlentry, "load_champion", lambda: None)
    r = client.get("/api/mlentry/status"); assert r.status_code == 200
    j = r.json()
    assert j["stack"]["recommendation_label"] == "Research Recommendation" and j["final_holdout_access"] is False
    assert "target_lift_at_5_min" in j["promotion_contract"]


def test_board_returns_recommended_and_candidates(client, monkeypatch):
    monkeypatch.setattr(routes_mlentry, "load_champion", lambda: None)
    r = client.get(f"/api/mlentry/board?signal_date={SIGNAL.isoformat()}"); assert r.status_code == 200
    j = r.json()
    assert j["run"]["run_id"] == RUN_ID and j["run"]["status"] == "OK"
    assert [i["stock_id"] for i in j["items"]] == ["2330"] and j["items"][0]["rank"] == 1
    assert [c["stock_id"] for c in j["candidates"]] == ["1101"]           # 通過 gate 未推薦；2317 未過 gate 不列
    assert j["run"]["health"]["feature_health"]["ok"] is True


def test_health_and_runs_shape(client, monkeypatch):
    monkeypatch.setattr(routes_mlentry, "load_champion", lambda: None)
    r = client.get("/api/mlentry/health?limit=5"); assert r.status_code == 200
    j = r.json()
    assert j["frozen_validation"]["promotion_result"] == "FAIL" and "thresholds" in j["frozen_validation"]
    assert "windows" in j["live"] and any(h["signal_date"] == SIGNAL.isoformat() for h in j["history"])
    r = client.get("/api/mlentry/runs?limit=5"); assert r.status_code == 200
    assert any(x["run_id"] == RUN_ID for x in r.json())
    assert client.get("/api/mlentry/runs/nope").status_code == 404


def test_board_new_fields(client, monkeypatch):
    monkeypatch.setattr(routes_mlentry, "load_champion", lambda: None)
    j = client.get(f"/api/mlentry/board?signal_date={SIGNAL.isoformat()}").json()
    it = j["items"][0]
    assert "est_target_price" in it and "est_stop_price" in it and "pred_mfe_5d" in it
    assert set(j["gate_thresholds"]) == {"target_vn_min", "stop_vn_max"}
    v = j["run"]["verdict"]
    assert v["tone"] == "ok" and v["headline"] == "正常出單 1 檔"
    assert "drifted_psi" in j["run"]["health"]["feature_health"]


def test_status_live_progress(client, monkeypatch):
    monkeypatch.setattr(routes_mlentry, "load_champion", lambda: None)
    j = client.get("/api/mlentry/status").json()
    assert j["live_progress"]["observe_at"] == 20 and j["live_progress"]["decide_at"] == 60
    assert isinstance(j["live_progress"]["matured_days"], int)
    assert j["last_run"]["verdict"]["headline"]


def test_health_convergence_and_days(client, monkeypatch):
    monkeypatch.setattr(routes_mlentry, "load_champion", lambda: None)
    j = client.get("/api/mlentry/health?limit=5").json()
    keys = [r["key"] for r in j["convergence"]]
    assert keys[:3] == ["lift_at_1", "lift_at_3", "lift_at_5"]
    assert isinstance(j["live"]["days"], list)
    assert all("n_drifted" in h for h in j["history"])


def test_tracking_shape(client):
    r = client.get("/api/mlentry/tracking?days=10"); assert r.status_code == 200
    j = r.json()
    assert set(j) == {"as_of", "summary", "items"} and j["summary"]["n"] == len(j["items"])


def test_tracking_unexpected_error_returns_empty(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(routes_mlentry, "load_tracking", boom)
    r = client.get("/api/mlentry/tracking?days=10")
    assert r.status_code == 200
    j = r.json()
    assert j["as_of"] is None and j["items"] == []
    assert j["summary"] == {"n": 0, "target": 0, "stop": 0, "timeout": 0, "live": 0, "pending": 0}


def test_run_info_diagnostics_and_audit_whitelist(client, monkeypatch):
    monkeypatch.setattr(routes_mlentry, "load_champion", lambda: None)
    j = client.get(f"/api/mlentry/board?signal_date={SIGNAL.isoformat()}").json()
    run = j["run"]
    assert set(run["diagnostics"]) == {"freshness", "sanity", "feature_shift", "recommendation"}
    assert run["diagnostics"]["feature_shift"] == {"evaluated": False, "attention": False, "error_type": "ValueError"}
    assert run["audit"] == {"requested_as_of": "2019-01-02", "feature_snapshot_as_of": "2019-01-02", "data_snapshot_id": "abcdef012345",
                            "serving_stack_hash": "0123456789ab", "code_commit": "abc", "as_of_mismatch": False}
    assert "SECRET-HOST" not in json.dumps(j) and "should be stripped" not in json.dumps(j)


def test_health_history_attention_count(client, monkeypatch):
    monkeypatch.setattr(routes_mlentry, "load_champion", lambda: None)
    j = client.get("/api/mlentry/health?limit=5").json()
    h = next(x for x in j["history"] if x["signal_date"] == SIGNAL.isoformat())
    assert h["attention_count"] == 1


def test_legacy_run_without_diagnostics_or_audit(client, monkeypatch):
    from app.storage.database import session_scope
    rid = "2019-01-03_legacy_000000000000"
    with session_scope() as s:
        s.merge(models.MLEntryRun(run_id=rid, signal_date=date(2019, 1, 3), as_of_timestamp=datetime(2019, 1, 3, 21, 30),
                                  dataset_version="ds_t", universe_version="u", feature_version="f", label_version="l",
                                  model_version="test_stack", calibration_version="c", policy_version="p", policy_name="policy_baseline_v1",
                                  model_status="RESEARCH_SHADOW", deployment_mode="SHADOW", promotion_eligible=False, code_commit="abc",
                                  status="NO_TRADE", no_trade=True, no_trade_reason="POLICY_NO_CANDIDATE", universe_count=3,
                                  qualified_count=0, recommendation_count=0, health_json=json.dumps({"data_quality": {"ok": True}})))
    try:
        monkeypatch.setattr(routes_mlentry, "load_champion", lambda: None)
        r = client.get(f"/api/mlentry/runs/{rid}"); assert r.status_code == 200
        assert r.json()["diagnostics"] is None and r.json()["audit"] is None
        j = client.get("/api/mlentry/health?limit=5").json()
        assert next(x for x in j["history"] if x["signal_date"] == "2019-01-03")["attention_count"] is None
    finally:
        with session_scope() as s:
            s.query(models.MLEntryRun).filter_by(run_id=rid).delete()
