"""monitor_mode：明確名單為正式來源；day_level 只是 legacy fallback 且要標記。"""

from __future__ import annotations

import json

from app.mlentry.fingerprint import canonical_hash
from app.mlentry.monitoring.monitor_modes import (SIDECAR_NAME, SKIP_FEATURES, build_sidecar, load_monitor_modes,
                                                   monitor_mode_for)

BINARY = {"is_attention_stock", "is_disposition_stock", "limit_up_today", "limit_down_today", "large_gap"}
DISCRETE = {"limit_up_count_20d", "limit_down_count_20d", "consecutive_up_days", "consecutive_down_days",
            "positive_day_ratio", "negative_day_ratio"}
DAY_LEVEL = {"market_ret_1d", "market_ret_5d", "market_ret_20d", "market_volatility", "industry_ret_5d",
             "industry_ret_20d", "industry_strength_rank", "breadth_ma20"}


def _ref(names, day_level=()):
    return {"features": {n: {"q": {}, "mean": 0.0, "std": 1.0, "day_level": n in day_level} for n in names},
            "predictions": {}, "universe_daily": {}}


def test_skip_list_is_exactly_the_19_semantic_names():
    assert SKIP_FEATURES == frozenset(BINARY | DISCRETE | DAY_LEVEL)
    assert monitor_mode_for("is_attention_stock") == "skip"
    assert monitor_mode_for("ret_5d") == "continuous"
    assert monitor_mode_for("dist_limit_up") == "continuous"


def test_build_sidecar_has_hash_and_modes():
    ref = _ref(["ret_5d", "is_attention_stock"])
    side = build_sidecar(ref)
    assert side["monitoring_schema_version"] == 1
    assert side["source_feature_reference_hash"] == canonical_hash(ref)
    assert side["features"] == {"ret_5d": {"monitor_mode": "continuous"}, "is_attention_stock": {"monitor_mode": "skip"}}


def test_load_prefers_artifact_native_monitor_mode(tmp_path):
    ref = _ref(["ret_5d", "x"])
    ref["features"]["ret_5d"]["monitor_mode"] = "skip"          # 人為：artifact 內建優先於名單
    ref["features"]["x"]["monitor_mode"] = "continuous"
    modes, src = load_monitor_modes(tmp_path, ref)
    assert src == "explicit" and modes == {"ret_5d": "skip", "x": "continuous"}


def test_load_uses_sidecar_when_hash_matches(tmp_path):
    ref = _ref(["ret_5d", "is_attention_stock"], day_level=())
    (tmp_path / SIDECAR_NAME).write_text(json.dumps(build_sidecar(ref)), encoding="utf-8")
    modes, src = load_monitor_modes(tmp_path, ref)
    assert src == "explicit" and modes == {"ret_5d": "continuous", "is_attention_stock": "skip"}


def test_load_falls_back_to_day_level_when_sidecar_missing_or_stale(tmp_path, caplog):
    ref = _ref(["ret_5d", "market_ret_1d"], day_level={"market_ret_1d"})
    modes, src = load_monitor_modes(tmp_path, ref)
    assert src == "legacy_day_level_fallback" and modes == {"ret_5d": "continuous", "market_ret_1d": "skip"}
    stale = build_sidecar(_ref(["other"]))
    (tmp_path / SIDECAR_NAME).write_text(json.dumps(stale), encoding="utf-8")
    with caplog.at_level("WARNING"):
        modes2, src2 = load_monitor_modes(tmp_path, ref)
    assert src2 == "legacy_day_level_fallback" and modes2 == modes
    assert any("monitoring.json" in m for m in caplog.messages)


def test_write_sidecar_touches_only_sidecar_and_is_idempotent(tmp_path):
    import hashlib
    from scripts.mlentry_feature_monitoring_sidecar import write_sidecar
    ref = _ref(["ret_5d", "is_attention_stock"])
    (tmp_path / "feature_reference.json").write_text(json.dumps(ref, indent=2), encoding="utf-8")
    (tmp_path / "stack.json").write_text("{}", encoding="utf-8")
    (tmp_path / "target_10d.lgbm.txt").write_bytes(b"model-bytes")
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}
    path, side = write_sidecar(tmp_path)
    path2, side2 = write_sidecar(tmp_path)
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir() if p.name != SIDECAR_NAME}
    assert path == path2 == tmp_path / SIDECAR_NAME and side == side2
    assert before == after                                            # artifact 既有檔案逐 byte 不變
    assert side["features"]["is_attention_stock"]["monitor_mode"] == "skip"


def test_train_stack_reference_carries_native_monitor_mode():
    import numpy as np
    import pandas as pd
    from app.mlentry.serving.train_stack import build_feature_reference
    rng = np.random.default_rng(0)
    n = 400
    df = pd.DataFrame({"signal_date": np.repeat(pd.date_range("2024-01-01", periods=8).astype(str), n // 8),
                       "ret_5d": rng.normal(size=n), "is_attention_stock": rng.integers(0, 2, n).astype(float)})
    ref = build_feature_reference(df, ["ret_5d", "is_attention_stock"], np.ones(n, dtype=bool), sample_every=1)
    assert ref["ret_5d"]["monitor_mode"] == "continuous"
    assert ref["is_attention_stock"]["monitor_mode"] == "skip"
