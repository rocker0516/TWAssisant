"""C1–C3：registry / train_stack artifact、daily run（fail-closed、immutable run、全 universe ledger）、成熟回填、live metrics。"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.mlentry.config import load_yaml
from app.mlentry.datasets import builder, store
from app.mlentry.monitoring import health, performance
from app.mlentry.registry import versions as reg
from app.mlentry.serving import daily_run, train_stack
from app.storage.database import Base
from tests.test_mlentry_dataset import _cfg, _db

TINY = {"lgbm_binary": {"n_estimators": 15, "num_leaves": 7, "min_child_samples": 5, "verbose": -1, "n_jobs": 1},
        "lgbm_regression": {"objective": "huber", "n_estimators": 15, "num_leaves": 7, "min_child_samples": 5, "verbose": -1, "n_jobs": 1},
        "logreg": {"C": 1.0}, "mfe_winsor_quantile": 0.9}


def _file_db(tmp_path, n_days=90):
    """合成 DB 寫成檔案（sqlite3 與 SQLAlchemy 各開一條連線）。"""
    mem, dates = _db(n_days=n_days, stocks=tuple(f"{1000 + i}" for i in range(12)) + ("0050",))
    path = tmp_path / "twa.db"
    mem.commit()
    disk = sqlite3.connect(str(path)); mem.backup(disk); disk.close(); mem.close()
    return path, dates


@pytest.fixture
def env(tmp_path, monkeypatch):
    path, dates = _file_db(tmp_path)
    cfg = _cfg()
    con = sqlite3.connect(str(path))
    ds = builder.build(con, cfg)
    hold = ds.features["signal_date"] >= ds.split.holdout_start
    d = store.write_dataset(tmp_path / "ds", ds.manifest, ds.sample_index, ds.features, ds.outcomes, hold)
    (d / "splits.json").write_text(ds.split.to_json(), encoding="utf-8")
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
    stack = train_stack.train_stack(d, root=tmp_path / "serving", models_cfg=TINY)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(daily_run, "load_champion", lambda: stack)
    return {"con": con, "Session": Session, "stack": stack, "cfg": cfg, "dates": dates, "ds": d}


def test_train_stack_artifacts_and_status(env):
    s = env["stack"]
    assert s.model_status == reg.STATUS_RESEARCH_SHADOW and s.deployment_mode == "SHADOW" and not s.promotion_eligible
    for t in s.tasks:
        assert (s.dir / f"{t}.lgbm.txt").exists()
    art = json.loads((s.dir / "artifacts.json").read_text(encoding="utf-8"))
    assert art["mfe_10d"]["winsor_cap"] is not None and art["execution"]["kind"] == "binary"
    ref = json.loads((s.dir / "feature_reference.json").read_text(encoding="utf-8"))
    assert set(ref["features"]) == set(s.feature_names) and "universe_daily" in ref
    assert reg.load_champion(s.dir.parent).model_version == s.model_version
    with pytest.raises(PermissionError):
        reg.promote(s, {"eligible": False}, "someone", root=s.dir.parent)


def _lenient_mon():
    """合成資料只有 12 檔，特徵 drift 門檻放寬到不可能觸發；fail-closed 路徑另用 manual_halt / data_quality 測。"""
    mon = load_yaml("monitoring")
    return {**mon, "feature_drift": {**mon["feature_drift"], "psi_hard": 99.0, "max_features_drifted": 999, "max_missing_rate_shift": 1.0}}


def test_daily_run_writes_immutable_run_and_full_universe(env):
    con, Session, cfg, dates = env["con"], env["Session"], env["cfg"], env["dates"]
    mon = _lenient_mon()
    with Session() as session:
        r = daily_run.run_daily(con, session, dates[60], cfg=cfg, monitoring=mon)
        assert r.signal_date == dates[60] and r.status in ("OK", "NO_TRADE")
        r2 = daily_run.run_daily(con, session, dates[60], cfg=cfg, monitoring=mon)
        assert r2.run_id != r.run_id                                  # 重跑同日 → 新 run，不覆蓋
        from app.storage import models
        n = session.query(models.MLEntryPrediction).filter_by(run_id=r.run_id).count()
        assert n == r.universe_count > 0                              # 全 universe 保存
        row = session.query(models.MLEntryRun).filter_by(run_id=r.run_id).one()
        assert row.model_status == "RESEARCH_SHADOW" and row.deployment_mode == "SHADOW" and not row.promotion_eligible
        assert json.loads(row.health_json)["feature_health"]["ok"]


def test_daily_run_fail_closed_on_manual_halt_and_bad_data(env):
    con, Session, cfg, dates = env["con"], env["Session"], env["cfg"], env["dates"]
    mon = _lenient_mon()
    with Session() as session:
        r = daily_run.run_daily(con, session, dates[61], cfg=cfg, monitoring={**mon, "manual_halt": True})
        assert r.status == "SYSTEM_NO_TRADE" and r.no_trade_reason == "MANUAL_HALT" and r.recommendation_count == 0
        strict = {**mon, "data_quality": {**mon["data_quality"], "min_universe_ratio": 5.0}}
        r = daily_run.run_daily(con, session, dates[62], cfg=cfg, monitoring=strict)
        assert r.status == "SYSTEM_NO_TRADE" and r.no_trade_reason == "DATA_HEALTH_FAIL"
        from app.storage import models
        assert session.query(models.MLEntryPrediction).filter_by(run_id=r.run_id).count() > 0   # shadow 預測仍保存


def test_mature_outcomes_fills_ledger_and_live_metrics(env):
    con, Session, cfg, dates = env["con"], env["Session"], env["cfg"], env["dates"]
    mon = _lenient_mon()
    with Session() as session:
        daily_run.run_daily(con, session, dates[50], cfg=cfg, monitoring=mon)      # 成熟（50+10 < 90）
        daily_run.run_daily(con, session, dates[85], cfg=cfg, monitoring=mon)      # 未成熟
        n = daily_run.mature_outcomes(con, session, cfg)
        assert n > 0
        from app.storage import models
        m = session.query(models.MLEntryPrediction).filter(models.MLEntryPrediction.signal_date == pd.Timestamp(dates[50]).date()).all()
        assert all(x.matured_at is not None and x.event_type is not None and x.label_available_date is not None for x in m)
        assert all(x.entry_date is not None for x in m)
        u = session.query(models.MLEntryPrediction).filter(models.MLEntryPrediction.signal_date == pd.Timestamp(dates[85]).date()).all()
        assert all(x.matured_at is None for x in u) and all(x.entry_status is not None for x in u)
        df = performance.load_matured(session)
        live = performance.rolling_live_metrics(df, windows=(20,), k=5)
        assert live["matured_days"] == 1 and "20" in live["windows"]


def test_health_gates_units():
    ok = health.data_quality_gate(1000, {"median": 1000}, {"close": 0.0}, {"min_universe_ratio": 0.8, "max_core_missing_rate": 0.02})
    assert ok.ok
    bad = health.data_quality_gate(500, {"median": 1000}, {"close": 0.0}, {"min_universe_ratio": 0.8, "max_core_missing_rate": 0.02})
    assert not bad.ok and bad.reason == "DATA_HEALTH_FAIL"
    ref = {"a": {"q": {"0.01": -2, "0.05": -1.6, "0.25": -0.7, "0.5": 0, "0.75": 0.7, "0.95": 1.6, "0.99": 2}, "missing_rate": 0.0,
                 "day_level": False, "psi_p99": 0.1},
           "mkt": {"q": {}, "missing_rate": 0.0, "day_level": True, "daily_q": {"0.005": -0.05, "0.995": 0.05}}}
    cfg = {"psi_hard": 0.25, "max_features_drifted": 0, "max_missing_rate_shift": 0.2}
    rng = np.random.default_rng(0)
    snap = pd.DataFrame({"a": rng.normal(size=500), "mkt": 0.01})
    assert health.feature_health_gate(snap, ref, cfg).ok
    snap2 = pd.DataFrame({"a": rng.normal(3, size=500), "mkt": 0.01})
    assert health.feature_health_gate(snap2, ref, cfg).reason == "FEATURE_DRIFT"
    snap3 = pd.DataFrame({"a": rng.normal(size=500), "mkt": 0.2})
    assert "mkt" in health.feature_health_gate(snap3, ref, cfg).details["out_of_range_day_level"]
    pr = health.prediction_health_gate({"t": np.full(10, 0.5)}, {"t": {"mean": 0.15, "std": 0.1}}, {"max_mean_shift_z": 3})
    assert not pr.ok and pr.reason == "MODEL_HEALTH_FAIL"


def test_feature_health_gate_records_drifted_psi():
    rng = np.random.default_rng(0)
    ref_q = {str(q): float(v) for q, v in zip(("0.01", "0.05", "0.25", "0.5", "0.75", "0.95", "0.99"),
                                               np.quantile(rng.normal(0, 1, 5000), (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)))}
    feature_ref = {"f_ok": {"q": ref_q, "missing_rate": 0.0, "psi_p99": 0.1},
                   "f_shift": {"q": ref_q, "missing_rate": 0.0, "psi_p99": 0.1}}
    snap = pd.DataFrame({"f_ok": rng.normal(0, 1, 2000), "f_shift": rng.normal(3, 1, 2000)})
    cfg = {"psi_hard": 0.25, "max_features_drifted": 8, "max_missing_rate_shift": 0.2}
    res = health.feature_health_gate(snap, feature_ref, cfg)
    d = res.details
    assert d["drifted"] == ["f_shift"]
    assert set(d["drifted_psi"]) == {"f_shift"}
    assert d["drifted_psi"]["f_shift"]["thr"] == 0.25
    assert d["drifted_psi"]["f_shift"]["psi"] > 0.25
    assert res.ok is True                      # 1 個漂移 ≤ 8：判定不變


GATE_KEYS = ("data_quality", "feature_health", "prediction_health", "recommendation")
BASELINE = Path(__file__).parent / "fixtures" / "mlentry_gates_baseline.json"


def _gates_canonical(health: dict) -> str:
    from app.mlentry.fingerprint import canonical_json
    return canonical_json({k: health.get(k) for k in GATE_KEYS})


def test_four_health_gates_unchanged_by_observation_layer(env):
    """Spec A 規則 1：加 diagnostics／audit 前後，四個 gate 子樹語意與內容完全相同（canonical JSON 比對）。"""
    con, Session, cfg, dates = env["con"], env["Session"], env["cfg"], env["dates"]
    with Session() as session:
        r = daily_run.run_daily(con, session, dates[60], cfg=cfg, monitoring=_lenient_mon())
    got = _gates_canonical(r.health)
    if not BASELINE.exists():
        if os.environ.get("MLENTRY_WRITE_GATE_BASELINE") == "1":   # 只在明確要求時，於改 daily_run 前由舊碼產生
            BASELINE.parent.mkdir(exist_ok=True)
            BASELINE.write_text(got, encoding="utf-8")
        else:
            pytest.fail("baseline fixture missing; set MLENTRY_WRITE_GATE_BASELINE=1 to regenerate from pre-change code")
    assert got == BASELINE.read_text(encoding="utf-8")


def test_daily_run_writes_diagnostics_and_audit(env):
    con, Session, cfg, dates = env["con"], env["Session"], env["cfg"], env["dates"]
    with Session() as session:
        r = daily_run.run_daily(con, session, dates[60], cfg=cfg, monitoring=_lenient_mon())
        from app.storage import models
        row = session.query(models.MLEntryRun).filter_by(run_id=r.run_id).one()
    health = json.loads(row.health_json)
    diag = health["diagnostics"]
    assert set(diag) == {"freshness", "sanity", "feature_shift", "recommendation"}
    for v in diag.values():
        assert {"evaluated", "attention"} <= set(v)
    assert diag["feature_shift"]["monitor_mode_source"] == "explicit"          # 新 stack 原生 monitor_mode
    assert diag["sanity"]["evaluated"] and diag["freshness"]["sources"]["daily_prices"]["lag_days"] == 0
    a = json.loads(row.audit_json)
    assert a["requested_as_of"] == dates[60] and a["feature_snapshot_as_of"] == dates[60]
    assert len(a["data_snapshot_id"]) == 12 and a["serving_stack_hash"] and "daily_prices" in a["sources"]
    assert a["sources"]["daily_prices"]["rows_visible_at_as_of"] >= r.universe_count       # 有價格檔數 ≥ eligible 檔數
    assert a["sources"]["market_index"]["rows_visible_at_as_of"] in (0, 1)


def test_observation_prep_exception_never_aborts_run(env, monkeypatch):
    from app.storage import models
    con, Session, cfg, dates = env["con"], env["Session"], env["cfg"], env["dates"]
    with Session() as session:
        base = daily_run.run_daily(con, session, dates[62], cfg=cfg, monitoring=_lenient_mon())

    def boom(*a, **k):
        raise RuntimeError("secret prep")
    monkeypatch.setattr(daily_run, "load_monitor_modes", boom)
    with Session() as session:
        r = daily_run.run_daily(con, session, dates[62], cfg=cfg, monitoring=_lenient_mon())
        row = session.query(models.MLEntryRun).filter_by(run_id=r.run_id).one()
    assert r.status == base.status
    err = {"evaluated": False, "attention": False, "error_type": "RuntimeError"}
    d = json.loads(row.health_json)["diagnostics"]
    assert d == {k: err for k in ("freshness", "sanity", "feature_shift", "recommendation")}
    assert json.loads(row.audit_json) == {"error_type": "RuntimeError"}
    assert "secret prep" not in row.health_json and "secret prep" not in row.audit_json


def test_diagnostic_exception_never_changes_status_or_leaks_message(env, monkeypatch):
    from app.mlentry.monitoring import diagnostics
    con, Session, cfg, dates = env["con"], env["Session"], env["cfg"], env["dates"]

    def boom(*a, **k):
        raise ValueError("secret path C:/db")
    monkeypatch.setattr(diagnostics, "feature_shift", boom)
    with Session() as session:
        r = daily_run.run_daily(con, session, dates[61], cfg=cfg, monitoring=_lenient_mon())
        from app.storage import models
        row = session.query(models.MLEntryRun).filter_by(run_id=r.run_id).one()
    assert r.status in ("OK", "NO_TRADE")
    d = json.loads(row.health_json)["diagnostics"]["feature_shift"]
    assert d == {"evaluated": False, "attention": False, "error_type": "ValueError"}
    assert "secret path" not in row.health_json


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
