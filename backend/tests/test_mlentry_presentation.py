"""presentation：估算價（台股升降單位、保守側取整）與健康條判讀句。"""

from __future__ import annotations

import pytest

from app.mlentry.serving.presentation import est_barrier_prices


@pytest.mark.parametrize("close, target, stop", [
    (16.35, 17.95, 15.55),      # 17.985 → 向下 0.05；15.5325 → 向上 0.05
    (43.15, 47.45, 41.00),      # 47.465 → 47.45；40.9925 → 41.00
    (9.50, 10.45, 9.03),        # 10.45 落 10–50 區（0.05）；9.025 落 <10 區（0.01）向上
    (100.0, 110.0, 95.0),       # 110 落 100–500（0.5）；95 落 50–100（0.1）
    (1000.0, 1100.0, 950.0),    # 1100 落 ≥1000（5）；950 落 500–1000（1）
])
def test_est_barrier_prices_rounds_conservatively(close, target, stop):
    assert est_barrier_prices(close) == (target, stop)


@pytest.mark.parametrize("close", [None, 0.0, -1.0, float("nan")])
def test_est_barrier_prices_invalid_close(close):
    assert est_barrier_prices(close) == (None, None)


from app.mlentry.serving.presentation import build_verdict


def test_verdict_ok_within_band():
    v = build_verdict("OK", None, None, 1721, 6, 5, {}, (3.0, 28.6))
    assert v == {"headline": "正常出單 5 檔", "tone": "ok",
                 "detail": "Universe 1721 → 通過 Gate 6 → Top-K 5 ｜ 候選數在 OOF 常態範圍內"}


def test_verdict_ok_outside_band():
    v = build_verdict("OK", None, None, 1721, 40, 5, {}, (3.0, 28.6))
    assert v["detail"].endswith("候選數超出 OOF 常態範圍（p5–p95 3–28.6）")


def test_verdict_ok_without_band():
    v = build_verdict("OK", None, None, 1721, 6, 5, {}, None)
    assert v["detail"] == "Universe 1721 → 通過 Gate 6 → Top-K 5"


def test_verdict_market_no_trade():
    v = build_verdict("NO_TRADE", "POLICY_NO_CANDIDATE", "今日沒有股票同時通過 Alpha 與 Risk Gate（市場無機會，非系統故障）",
                      1700, 0, 0, {}, (3.0, 28.6))
    assert v["headline"] == "今日不出單：市場無機會" and v["tone"] == "quiet"
    assert "非系統故障" in v["detail"]


def test_verdict_feature_drift_with_psi():
    health = {"feature_health": {"ok": False, "n_drifted": 12, "drifted": ["a", "b", "c", "d"],
                                 "drifted_psi": {"a": {"psi": 0.41, "thr": 0.25}, "b": {"psi": 0.33, "thr": 0.25}}}}
    v = build_verdict("SYSTEM_NO_TRADE", "FEATURE_DRIFT", "特徵分布漂移超過門檻，系統依 fail-closed 不出單", 1721, 0, 0, health, None)
    assert v["headline"] == "系統暫停出單（fail-closed）" and v["tone"] == "fail"
    assert v["detail"] == "特徵漂移：12 個特徵超過門檻（a PSI 0.41>0.25、b PSI 0.33>0.25、c…）。系統依 fail-closed 不出單。"


def test_verdict_feature_drift_legacy_run_without_psi():
    health = {"feature_health": {"ok": False, "n_drifted": 2, "drifted": ["a", "b"]}}
    v = build_verdict("SYSTEM_NO_TRADE", "FEATURE_DRIFT", None, 1721, 0, 0, health, None)
    assert v["detail"] == "特徵漂移：2 個特徵超過門檻（a、b）。系統依 fail-closed 不出單。"


def test_verdict_other_system_reason_uses_text():
    v = build_verdict("SYSTEM_NO_TRADE", "DATA_HEALTH_FAIL", "資料品質未通過", 900, 0, 0, {}, None)
    assert v == {"headline": "系統暫停出單（fail-closed）", "detail": "資料品質未通過", "tone": "fail"}
