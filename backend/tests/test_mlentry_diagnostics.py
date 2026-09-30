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
