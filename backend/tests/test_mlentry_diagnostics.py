# backend/tests/test_mlentry_diagnostics.py
"""§23 只記錄診斷：固定 envelope、freshness（business_date／ingestion_watermark）、sanity、feature_shift、recommendation。"""

from __future__ import annotations

import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

from app.mlentry.data.calendar import TradingCalendar
from app.mlentry.data.quality import HardFlag
from app.mlentry.data.universe import EligFlag
from app.mlentry.monitoring import diagnostics as dg

CAL = TradingCalendar([f"2026-09-{d:02d}" for d in (21, 22, 23, 24, 25, 28, 29)])
AS_OF = "2026-09-29"


def _con(rows):
    """pipeline_runs 假表：rows = [(id, trading_date, status, finished_at, steps_json)]。"""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE pipeline_runs (id INTEGER PRIMARY KEY, trading_date TEXT, started_at TEXT, finished_at TEXT, status TEXT, steps TEXT, error TEXT)")
    con.executemany("INSERT INTO pipeline_runs (id, trading_date, status, finished_at, steps) VALUES (?,?,?,?,?)", rows)
    return con


def _steps(**status):
    return json.dumps([{"name": k, "status": v} for k, v in status.items()])


def test_envelopes_and_safe():
    assert dg.envelope_ok(True, x=1) == {"evaluated": True, "attention": True, "x": 1}
    assert dg.envelope_skip("NO_DATA") == {"evaluated": False, "attention": False, "reason": "NO_DATA"}

    def boom():
        raise ValueError("secret /path/to/db")
    out = dg.safe(boom)
    assert out == {"evaluated": False, "attention": False, "error_type": "ValueError"}
    assert "secret" not in json.dumps(out)


FRESH_CFG = {"daily_prices": {"mode": "business_date", "max_lag_days": 0},
             "market_index": {"mode": "business_date", "max_lag_days": 0},
             "attention_listings": {"mode": "ingestion_watermark", "max_lag_days": 1, "step": "attention"}}


def test_freshness_normal_day_no_attention():
    con = _con([(1, "2026-09-28", "success", "2026-09-28 21:49:00", _steps(fetch="ok", attention="ok"))])
    out = dg.freshness(AS_OF, CAL, {"daily_prices": AS_OF, "market_index": AS_OF}, con, FRESH_CFG)
    assert out["evaluated"] and not out["attention"]
    assert out["sources"]["daily_prices"] == {"mode": "business_date", "max_date": AS_OF, "lag_days": 0, "attention": False}
    a = out["sources"]["attention_listings"]
    assert a["mode"] == "ingestion_watermark" and a["lag_trading_days"] == 1 and not a["attention"]
    assert a["watermark_pipeline_run_id"] == 1 and a["watermark_business_date"] == "2026-09-28"
    assert a["watermark_completed_at"] == "2026-09-28 21:49:00"


def test_freshness_stale_price_and_stale_watermark():
    con = _con([(1, "2026-09-25", "success", "2026-09-25 21:49:00", _steps(attention="ok")),
                (2, "2026-09-28", "failed", "2026-09-28 21:49:00", _steps(attention="failed"))])
    out = dg.freshness(AS_OF, CAL, {"daily_prices": "2026-09-28", "market_index": AS_OF}, con, FRESH_CFG)
    assert out["attention"]
    assert out["sources"]["daily_prices"]["lag_days"] == 1 and out["sources"]["daily_prices"]["attention"]
    a = out["sources"]["attention_listings"]
    assert a["watermark_pipeline_run_id"] == 1 and a["lag_trading_days"] == 2 and a["attention"]


def test_freshness_missing_source_is_attention_and_missing_threshold_is_not():
    con = _con([])
    cfg = {"daily_prices": {"mode": "business_date", "max_lag_days": 0}, "market_index": {"mode": "business_date"},
           "attention_listings": {"mode": "ingestion_watermark", "step": "attention", "max_lag_days": 1}}
    out = dg.freshness(AS_OF, CAL, {"daily_prices": None, "market_index": AS_OF}, con, cfg)
    dp, mi, at = out["sources"]["daily_prices"], out["sources"]["market_index"], out["sources"]["attention_listings"]
    assert dp["max_date"] is None and dp["lag_days"] is None and dp["attention"] is True        # 來源缺資料 → 提醒
    assert mi["lag_days"] == 0 and mi["attention"] is False and mi["reason"] == "THRESHOLD_NOT_CONFIGURED"
    assert at["watermark_pipeline_run_id"] is None and at["attention"] is True
    assert out["attention"]


def test_freshness_survives_missing_pipeline_runs_table():
    con = sqlite3.connect(":memory:")                          # 無 pipeline_runs 表（合成測試 DB）
    assert dg.latest_pipeline_watermark(con, "attention") is None


def test_freshness_without_config_is_skip():
    assert dg.freshness(AS_OF, CAL, {}, _con([]), None)["reason"] == "THRESHOLD_NOT_CONFIGURED"


SANITY_CFG = {"min_hard_flag_count": 5, "max_hard_flag_ratio": 0.005}


def _rows(n_present, n_hard, n_no_price=0):
    ids = [f"S{i}" for i in range(n_present + n_no_price)]
    elig = np.zeros(len(ids), dtype=np.uint16); hard = np.zeros(len(ids), dtype=np.uint16)
    elig[n_present:] = int(EligFlag.NO_PRICE)
    hard[:n_hard] = int(HardFlag.HIGH_LT_LOW); elig[:n_hard] |= int(EligFlag.DATA_QUALITY)
    return pd.Series(elig, index=ids), pd.Series(hard, index=ids)


def test_sanity_counts_and_attention_requires_both_conditions():
    e, h = _rows(2000, 4, n_no_price=10)                      # 4 < min_count → 不亮
    out = dg.sanity_summary(e, h, SANITY_CFG)
    assert out["evaluated"] and not out["attention"]
    assert out["universe_present"] == 2000 and out["hard_flag_count"] == 4 and out["elig"]["NO_PRICE"] == 10
    assert out["hard"]["HIGH_LT_LOW"] == 4 and out["hard_flag_ratio"] == pytest.approx(0.002)
    e, h = _rows(2000, 12)                                     # 12 ≥ 5 且 0.6% > 0.5% → 亮
    assert dg.sanity_summary(e, h, SANITY_CFG)["attention"]
    e, h = _rows(20000, 12)                                    # 12 ≥ 5 但 0.06% ≤ 0.5% → 不亮
    assert not dg.sanity_summary(e, h, SANITY_CFG)["attention"]


def test_sanity_no_data_and_no_threshold():
    e, h = _rows(0, 0, n_no_price=3)
    assert dg.sanity_summary(e, h, SANITY_CFG)["reason"] == "NO_DATA"
    e, h = _rows(10, 0)
    out = dg.sanity_summary(e, h, {})
    assert out["reason"] == "THRESHOLD_NOT_CONFIGURED" and out["hard_flag_count"] == 0


FS_CFG = {"mean_z_threshold": 3.0, "gap_threshold": 0.2, "max_features_mean_shift": 8, "max_features_gap": 8}


def _ref_from(x: np.ndarray, day_level=False):
    return {"q": {q: float(np.quantile(x, float(q))) for q in ("0.01", "0.05", "0.25", "0.5", "0.75", "0.95", "0.99")},
            "mean": float(x.mean()), "std": float(x.std()), "day_level": day_level, "missing_rate": 0.0}


def test_gap_near_zero_for_same_distribution_and_large_for_shift():
    rng = np.random.default_rng(0)
    base = rng.normal(0, 1, 20000)
    ref = _ref_from(base)
    assert dg.quantile_cdf_gap_7pt(ref["q"], rng.normal(0, 1, 3000)) < 0.05
    assert dg.quantile_cdf_gap_7pt(ref["q"], rng.normal(1, 1, 3000)) > 0.2
    assert dg.quantile_cdf_gap_7pt(ref["q"], np.array([np.nan])) is None


def test_feature_shift_skips_and_counts_and_source_label():
    rng = np.random.default_rng(1)
    base = rng.normal(0, 1, 20000)
    ref_full = {"features": {"ret_5d": _ref_from(base), "shifted": _ref_from(base), "is_attention_stock": _ref_from(rng.integers(0, 2, 20000).astype(float), day_level=True)}}
    snap = pd.DataFrame({"ret_5d": rng.normal(0, 1, 2000), "shifted": rng.normal(4, 1, 2000), "is_attention_stock": rng.integers(0, 2, 2000).astype(float)})
    modes = {"ret_5d": "continuous", "shifted": "continuous", "is_attention_stock": "skip"}
    out = dg.feature_shift(snap, ref_full, modes, "explicit", FS_CFG)
    assert out["evaluated"] and not out["attention"]                 # 1 個位移 < 8
    assert out["n_evaluated"] == 2 and out["n_mean_z_gt"] == 1 and out["n_gap_gt"] == 1
    assert out["skipped"] == ["is_attention_stock"] and out["monitor_mode_source"] == "explicit"
    assert out["top"][0]["name"] == "shifted" and out["top"][0]["quantile_cdf_gap_7pt"] > 0.2
    assert all(r["name"] != "is_attention_stock" for r in out["top"])


def test_feature_shift_reference_std_zero_and_thresholds():
    ref_full = {"features": {"const": {"q": {q: 1.0 for q in ("0.01", "0.05", "0.25", "0.5", "0.75", "0.95", "0.99")}, "mean": 1.0, "std": 0.0, "day_level": False}}}
    snap = pd.DataFrame({"const": np.ones(50)})
    out = dg.feature_shift(snap, ref_full, {"const": "continuous"}, "explicit", FS_CFG)
    row = out["top"][0]
    assert row["mean_z"] is None and row["std_ratio"] is None and row["reason"] == "REFERENCE_STD_ZERO"
    assert dg.feature_shift(snap, ref_full, {"const": "continuous"}, "explicit", {})["reason"] == "THRESHOLD_NOT_CONFIGURED"


def test_feature_shift_attention_uses_ge_count():
    rng = np.random.default_rng(2)
    base = rng.normal(0, 1, 20000)
    names = [f"f{i}" for i in range(8)]
    ref_full = {"features": {n: _ref_from(base) for n in names}}
    snap = pd.DataFrame({n: rng.normal(4, 1, 500) for n in names})       # 恰 8 個位移 → >= 8 亮
    out = dg.feature_shift(snap, ref_full, {n: "continuous" for n in names}, "explicit", FS_CFG)
    assert out["attention"] and out["n_gap_gt"] == 8


REC_CFG = {"min_recommendations_for_concentration": 3, "max_sector_share": 0.60, "min_sector_overweight": 2.0}


def _policy_df(n=100, rec_ids=(), qual_ids=()):
    ids = [f"S{i:03d}" for i in range(n)]
    return pd.DataFrame({"stock_id": ids, "gate_pass": [i in qual_ids or i in rec_ids for i in ids],
                         "recommended": [i in rec_ids for i in ids],
                         "recommendation_score": np.linspace(0, 1, n)})


def test_recommendation_distribution_sector_overweight_needs_all_three_conditions():
    df = _policy_df(100, rec_ids=("S000", "S001", "S002", "S003"), qual_ids=("S010", "S011"))
    sector = pd.Series([1.0] * 5 + [2.0] * 95, index=df["stock_id"])          # 類股 1 佔 universe 5%
    mcap = pd.Series(np.arange(100, dtype=float), index=df["stock_id"]); liq = mcap.copy()
    out = dg.recommendation_distribution(df, sector, mcap, liq, REC_CFG)
    assert out["evaluated"] and out["attention"]                            # 4 檔全在類股 1：share 1.0、overweight 20
    assert out["sector"]["max_sector"]["sector_id"] == "1" and out["sector"]["max_sector"]["overweight"] == pytest.approx(20.0)
    assert out["qualified_count"] == 6 and out["recommendation_count"] == 4
    assert out["score_q"]["p50"] == pytest.approx(np.median(df.loc[df["gate_pass"], "recommendation_score"]), abs=1e-4)
    assert out["mcap_tercile"]["mcap_basis"] == "current_company_profile_at_run_time"
    assert out["mcap_tercile"] == {**out["mcap_tercile"], "low": 4, "mid": 0, "high": 0, "missing_count": 0}
    sector_big = pd.Series([1.0] * 60 + [2.0] * 40, index=df["stock_id"])   # 類股 1 佔 60%：share 1.0 但 overweight 1.67 → 不亮
    assert not dg.recommendation_distribution(df, sector_big, mcap, liq, REC_CFG)["attention"]
    df2 = _policy_df(100, rec_ids=("S000", "S001"))                          # 只有 2 檔 < 3 → 不亮
    assert not dg.recommendation_distribution(df2, sector, mcap, liq, REC_CFG)["attention"]


def test_recommendation_distribution_edge_cases():
    df = _policy_df(10)                                                      # 無 qualified、無推薦
    sector = pd.Series(1.0, index=df["stock_id"]); mcap = pd.Series(np.nan, index=df["stock_id"])
    out = dg.recommendation_distribution(df, sector, mcap, mcap, REC_CFG)
    assert out["evaluated"] and not out["attention"]
    assert out["score_q"] is None and out["sector"]["recommended"] == {} and out["sector"]["max_sector"] is None
    assert out["mcap_tercile"]["missing_count"] == 10 and out["mcap_tercile"]["issued_shares_missing_count"] == 10
    assert dg.recommendation_distribution(df, sector, mcap, mcap, {})["reason"] == "THRESHOLD_NOT_CONFIGURED"
