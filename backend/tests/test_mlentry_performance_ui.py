"""體檢頁：Live 指標擴充、收斂判定、逐日成熟紀錄。"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from app.mlentry.monitoring.performance import convergence, daily_matured, rolling_live_metrics


def _df(n_days: int) -> pd.DataFrame:
    """每日 10 檔：rank 1–5 推薦；rank1 必中 target、rank2 必中 stop；其餘未推薦列全 0。"""
    rows = []
    d0 = date(2026, 1, 1)
    for i in range(n_days):
        sd = d0 + timedelta(days=i)
        for j in range(10):
            rec = j < 5
            rows.append({"run_id": f"r{i}", "signal_date": sd, "stock_id": f"S{j}", "recommended": rec,
                         "rank": j + 1 if rec else None, "p_target_10d": 0.2, "p_stop_10d": 0.3,
                         "target_hit_10d": 1 if j in (0, 5) else 0, "stop_hit_10d": 1 if j in (1, 6, 7) else 0,
                         "return_10d": 0.05 if j == 0 else 0.0, "mfe_10d": 0.1, "mae_10d": -0.02,
                         "event_type": 1 if j in (0, 5) else (2 if j in (1, 6, 7) else 4),
                         "status": "OK", "no_trade": False, "qualified_count": 8})
    return pd.DataFrame(rows)


def test_live_lift_at_k():
    out = rolling_live_metrics(_df(20), windows=(20,), k=5)
    w = out["windows"]["20"]
    # 市場 target 基率 = 2/10；rank≤1 命中率 1 → lift 5；rank≤3 命中率 1/3 → 1.667；rank≤5 → 1/5 → 1.0
    assert w["lift"]["1"] == pytest.approx(5.0)
    assert w["lift"]["3"] == pytest.approx(5 / 3)
    assert w["lift"]["5"] == pytest.approx(1.0)
    assert w["coverage"] == 1.0 and w["candidates_median"] == 8 and w["no_trade_rate"] == 0.0


def test_daily_matured_newest_first():
    days = daily_matured(_df(3), k=5)
    assert [d["signal_date"] for d in days] == ["2026-01-03", "2026-01-02", "2026-01-01"]
    assert days[0] == {"signal_date": "2026-01-03", "n_rec": 5, "target": 1, "stop": 1, "timeout": 3,
                       "lift": pytest.approx(1.0), "net10": pytest.approx(0.01 - 0.00585)}


FROZEN = {"target_lift_at_5": 1.23, "ci_target_lift": [1.07, 1.40], "stop_ratio_at_5": 0.75, "ci_stop_ratio": [0.68, 0.81],
          "mean_net10": 0.0066, "ci_net10": [-0.003, 0.0154], "coverage": 0.992, "lift_at_1": 1.4, "lift_at_3": 1.3,
          "candidates_median": 9.5, "candidates_p05": 3.0, "candidates_p95": 28.6, "no_trade_rate": 0.008,
          "ece_target_10d": 0.012, "median_mfe_10d": 0.034, "median_mae_10d": -0.027}


def _row(rows, key):
    return next(r for r in rows if r["key"] == key)


def test_convergence_accumulating_when_short():
    live = {"matured_days": 5, "windows": {"20": {"days": 5, "lift": {"5": 1.2}}}}
    rows = convergence(FROZEN, live)
    r = _row(rows, "lift_at_5")
    assert r["verdict"] == {"20": "累積中", "60": "累積中"} and r["live"]["20"] == 1.2 and r["live"]["60"] is None


def test_convergence_ci_and_dist():
    live = {"matured_days": 20, "windows": {"20": {"days": 20, "lift": {"1": 2.0, "3": 1.5, "5": 1.15}, "stop_ratio": 0.92,
                                                   "mean_net10": 0.002, "coverage": 0.95, "candidates_median": 40.0,
                                                   "no_trade_rate": 0.05, "ece_target_10d": 0.03,
                                                   "median_mfe": 0.03, "median_mae": -0.03}}}
    rows = convergence(FROZEN, live)
    assert [r["key"] for r in rows] == ["lift_at_1", "lift_at_3", "lift_at_5", "stop_ratio_at_5", "net10", "coverage",
                                        "candidates_per_day", "no_trade_rate", "ece_target_10d", "median_mfe_10d", "median_mae_10d"]
    assert _row(rows, "lift_at_5")["verdict"]["20"] == "CI 內"
    assert _row(rows, "stop_ratio_at_5")["verdict"]["20"] == "CI 外"
    assert _row(rows, "net10")["verdict"]["20"] == "CI 內"
    assert _row(rows, "candidates_per_day")["verdict"]["20"] == "分布外"
    assert _row(rows, "candidates_per_day")["band"] == [3.0, 28.6] and _row(rows, "candidates_per_day")["band_kind"] == "dist"
    assert _row(rows, "coverage")["verdict"]["20"] == "參考"
    assert _row(rows, "lift_at_5")["verdict"]["60"] == "累積中"


def test_convergence_missing_frozen_stats_is_none():
    rows = convergence({"target_lift_at_5": 1.23, "ci_target_lift": [1.07, 1.40]}, {"matured_days": 0, "windows": {}})
    assert _row(rows, "lift_at_1")["frozen"] is None
    assert _row(rows, "lift_at_5")["frozen"] == 1.23


def test_live_no_trade_day_reflected():
    df = _df(4)
    m = df["signal_date"] == df["signal_date"].max()
    df.loc[m, ["status", "no_trade", "recommended"]] = ["SYSTEM_NO_TRADE", True, False]
    w = rolling_live_metrics(df, windows=(20,), k=5)["windows"]["20"]
    assert w["no_trade_rate"] == pytest.approx(0.25)
    assert w["coverage"] == pytest.approx(0.75)
    # lift 分母仍為整窗市場基率 (2/10)；rank1 每個有推薦日命中 → 5
    assert w["lift"]["1"] == pytest.approx(5.0)


def test_live_qualified_count_null_gives_none():
    df = _df(3)
    df["qualified_count"] = None
    w = rolling_live_metrics(df, windows=(20,), k=5)["windows"]["20"]
    assert w["candidates_median"] is None


def test_live_no_trade_null_treated_false():
    df = _df(3)
    df["no_trade"] = None
    w = rolling_live_metrics(df, windows=(20,), k=5)["windows"]["20"]
    assert w["no_trade_rate"] == 0.0
