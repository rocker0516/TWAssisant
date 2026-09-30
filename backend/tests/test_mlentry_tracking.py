"""追蹤中：以 barrier 引擎計算近 10 日推薦的即時路徑；與成熟引擎語意一致。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.mlentry.config import LabelConfig
from app.mlentry.labels.barriers import run_barriers
from app.mlentry.serving.tracking import summarize, track_paths

CFG = LabelConfig()


def _mats(paths: dict[str, list[tuple[float, float, float, float]]], n_days: int) -> dict[str, pd.DataFrame]:
    """paths：stock → [(open, high, low, close), ...] 從第 0 列（signal_date）起；不足列補 NaN。"""
    idx = pd.Index([f"2026-09-{d:02d}" for d in range(1, n_days + 1)], name="date")
    out = {}
    for j, col in enumerate(("open", "high", "low", "close")):
        data = {}
        for sid, rows in paths.items():
            vals = [r[j] for r in rows] + [np.nan] * (n_days - len(rows))
            data[sid] = vals
        out[col] = pd.DataFrame(data, index=idx, dtype=float)
    return out


FLAT = (100.0, 101.0, 99.0, 100.0)


def test_pending_entry_on_last_day():
    m = _mats({"A": [FLAT]}, 1)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    assert r["status"] == "PENDING_ENTRY" and r["day_index"] == 0 and r["ret_now"] is None


def test_live_path_reports_ret_mfe_mae():
    # 第 0 列 signal；第 1 列開 100 進場，高 104 低 98 收 103；第 2 列高 105 低 101 收 102
    m = _mats({"A": [FLAT, (100, 104, 98, 103), (102, 105, 101, 102)]}, 3)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    assert r["status"] == "LIVE" and r["day_index"] == 2 and r["hit_day"] is None
    assert r["ret_now"] == pytest.approx(0.02, abs=1e-6)
    assert r["mfe"] == pytest.approx(0.05, abs=1e-6)
    assert r["mae"] == pytest.approx(-0.02, abs=1e-6)


def test_target_hit_day():
    m = _mats({"A": [FLAT, (100, 103, 99, 102), (102, 111, 101, 110)]}, 3)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    assert r["status"] == "TARGET" and r["hit_day"] == 2


def test_stop_hit_day():
    m = _mats({"A": [FLAT, (100, 101, 94.9, 95)]}, 2)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    assert r["status"] == "STOP" and r["hit_day"] == 1


def test_same_day_double_touch_is_stop_ambiguous():
    m = _mats({"A": [FLAT, (100, 110.5, 94.0, 100)]}, 2)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    assert r["status"] == "STOP_AMBIGUOUS" and r["hit_day"] == 1


def test_timeout_after_ten_days():
    rows = [FLAT] + [(100, 102, 98, 100)] * 10
    m = _mats({"A": rows}, 11)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    assert r["status"] == "TIMEOUT" and r["day_index"] == 10


def test_limit_up_open_is_not_entered():
    # 前收 100 → 漲停 110；隔日開在 110 → PRICE_LIMIT_CONSTRAINT
    m = _mats({"A": [FLAT, (110, 110, 110, 110)]}, 2)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    assert r["status"] == "NOT_ENTERED" and r["ret_now"] is None


def test_missing_stock_is_data_missing():
    m = _mats({"A": [FLAT, FLAT]}, 2)
    (r,) = track_paths(m, [("2026-09-01", "ZZZZ")], CFG)
    assert r["status"] == "DATA_MISSING"


def test_ledger_event_overrides():
    m = _mats({"A": [FLAT, (100, 101, 99, 100)]}, 2)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG, ledger={("2026-09-01", "A"): 4})
    assert r["status"] == "TIMEOUT"


_EV_STATUS = {1: "TARGET", 2: "STOP", 3: "STOP_AMBIGUOUS", 4: "TIMEOUT"}


def _random_path(seed: int, vol: float = 0.03) -> list[tuple[float, float, float, float]]:
    rng = np.random.default_rng(seed)
    rows = [FLAT]
    px = 100.0
    for _ in range(10):
        o = px * (1 + rng.normal(0, 0.01)); c = o * (1 + rng.normal(0, vol))
        rows.append((o, max(o, c) * 1.01, min(o, c) * 0.99, c)); px = c
    rows.append(FLAT)                                      # 第 11 列讓第 0 列在原生引擎中成熟
    return rows


def _direct_expect(rows, d: int) -> dict:
    """只保留第 0..d 列的真實資料（其後 NaN、但列數足夠讓第 0 列成熟），直接跑 run_barriers。"""
    cut = [r if i <= d else (np.nan,) * 4 for i, r in enumerate(rows)]
    direct = run_barriers(_mats({"A": cut}, len(rows)), CFG)
    g = lambda k: direct[k].iloc[0, 0]
    ev = int(g("event_type"))
    status = _EV_STATUS[ev]
    if status == "TIMEOUT" and d < CFG.max_horizon:
        status = "LIVE"
    hit = {"TARGET": g("target_first_hit_day"), "STOP_AMBIGUOUS": g("target_first_hit_day"),
           "STOP": g("stop_first_hit_day")}.get(status)
    return {"status": status, "hit_day": None if hit is None else int(hit),
            "ret": float(g("return_10d")), "mfe": float(g("mfe_10d")), "mae": float(g("mae_10d"))}


def _assert_parity(rows, d: int):
    m = _mats({"A": rows[: d + 1]}, d + 1)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    e = _direct_expect(rows, d)
    assert r["day_index"] == d
    assert r["status"] == e["status"]
    assert r["hit_day"] == e["hit_day"]
    assert r["ret_now"] == pytest.approx(e["ret"], abs=1e-6)
    assert r["mfe"] == pytest.approx(e["mfe"], abs=1e-6)
    assert r["mae"] == pytest.approx(e["mae"], abs=1e-6)
    return e


def test_parity_with_matured_engine():
    """完整 10 日路徑：status／hit_day／ret_now／mfe／mae 與 run_barriers 直接成熟結果一致。"""
    seen = set()
    for seed in range(12):
        e = _assert_parity(_random_path(seed), 10)
        seen.add(e["status"])
    assert seen & {"TARGET", "STOP", "STOP_AMBIGUOUS"} and "TIMEOUT" in seen   # 兩類結局皆被驗到


def test_parity_truncated_prefixes_exercise_padding():
    """同一條路徑截到第 d 日（d=1..9）：補 NaN 後的結果 = 該日之後全 NaN 的直接引擎結果；
    已含命中的前綴須回報與完整路徑相同的 hit_day 與 status。"""
    hits = 0
    for seed in range(20):
        rows = _random_path(seed, vol=0.04)
        full = _direct_expect(rows, 10)
        for d in range(1, 10):
            e = _assert_parity(rows, d)
            if full["hit_day"] is not None and d >= full["hit_day"]:
                assert e["status"] == full["status"] and e["hit_day"] == full["hit_day"]
                hits += 1
            elif full["hit_day"] is None:
                assert e["status"] == "LIVE" and e["hit_day"] is None
    assert hits > 0


def test_ledger_override_keeps_hit_day_consistent():
    m = _mats({"A": [FLAT, (100, 103, 99, 102), (102, 111, 101, 110)]}, 3)   # 引擎：TARGET@2
    key = ("2026-09-01", "A")
    (t,) = track_paths(m, [key], CFG)
    assert t["status"] == "TARGET" and t["hit_day"] == 2
    (r,) = track_paths(m, [key], CFG, ledger={key: 4})                     # ledger：TIMEOUT
    assert r["status"] == "TIMEOUT" and r["hit_day"] is None
    assert r["ret_now"] == t["ret_now"] and r["mfe"] == t["mfe"] and r["mae"] == t["mae"]
    (r,) = track_paths(m, [key], CFG, ledger={key: 2})                     # ledger：STOP → 用引擎 stop 日（無 → None）
    assert r["status"] == "STOP" and r["hit_day"] is None
    (r,) = track_paths(m, [key], CFG, ledger={key: 3})                     # ledger：STOP_AMBIGUOUS → target 日
    assert r["status"] == "STOP_AMBIGUOUS" and r["hit_day"] == 2
    (r,) = track_paths(m, [key], CFG, ledger={key: 0})
    assert r["status"] == "NOT_ENTERED" and r["hit_day"] is None


def test_summarize_counts():
    rows = [{"status": s} for s in ("TARGET", "STOP", "STOP_AMBIGUOUS", "LIVE", "LIVE", "PENDING_ENTRY", "TIMEOUT", "NOT_ENTERED")]
    assert summarize(rows) == {"n": 8, "target": 1, "stop": 2, "timeout": 1, "live": 2, "pending": 1}


def test_load_tracking_shape_on_real_db():
    import sqlite3
    from app.config import get_settings
    from app.mlentry.serving.tracking import load_tracking
    from app.storage.database import init_db, session_scope

    init_db()
    con = sqlite3.connect(str(get_settings().db_path))
    try:
        with session_scope() as s:
            res = load_tracking(con, s, days=10)
    finally:
        con.close()
    assert set(res) == {"as_of", "summary", "items"}
    assert set(res["summary"]) == {"n", "target", "stop", "timeout", "live", "pending"}
    assert res["summary"]["n"] == len(res["items"])
    dates = [it["signal_date"] for it in res["items"]]
    assert dates == sorted(dates, reverse=True)
    for it in res["items"]:
        assert {"signal_date", "stock_id", "name", "day_index", "status", "ret_now", "mfe", "mae", "hit_day"} <= set(it)


def test_load_tracking_in_memory(monkeypatch):
    import sqlite3
    from datetime import date, datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.mlentry.data.calendar import TradingCalendar
    from app.mlentry.labels.barriers import Event
    from app.mlentry.serving import tracking
    from app.storage import models

    dates = [f"2026-09-{d:02d}" for d in range(1, 11)]
    monkeypatch.setattr(tracking, "load_calendar", lambda con: TradingCalendar(dates))

    def fake_matrices(con, cal, cols):
        return {k: pd.DataFrame(100.0, index=cal.dates, columns=cols) for k in ("open", "high", "low", "close")}
    monkeypatch.setattr(tracking, "_load_window_matrices", fake_matrices)

    eng = create_engine("sqlite://")
    models.Base.metadata.create_all(eng)

    def run(rid, sd, ts, mv):
        return models.MLEntryRun(
            run_id=rid, signal_date=sd, as_of_timestamp=ts, dataset_version="d", universe_version="u",
            feature_version="f", label_version="l", model_version=mv, calibration_version="c",
            policy_version="p", policy_name="pn", model_status="RESEARCH_SHADOW", deployment_mode="SHADOW",
            code_commit="x", status="OK")

    def pred(rid, sd, sid, rank, rec=True, **kw):
        return models.MLEntryPrediction(run_id=rid, stock_id=sid, signal_date=sd, rank=rank, recommended=rec, **kw)

    d2, d3 = date(2026, 9, 2), date(2026, 9, 3)
    with Session(eng) as s:
        s.add_all([models.Stock(id=i, name=f"N{i}") for i in ("1101", "1102", "1103", "1104")])
        s.add_all([
            run("2026-09-02_zzz_100000", d2, datetime(2026, 9, 2, 10), "zzz"),   # 較早、run_id 字典序較大
            run("2026-09-02_aaa_150000", d2, datetime(2026, 9, 2, 15), "aaa"),   # 較晚 -> 應被選
            run("2026-09-03_m_150000", d3, datetime(2026, 9, 3, 15), "m"),
        ])
        s.flush()
        s.add_all([
            pred("2026-09-02_zzz_100000", d2, "1101", 1),                          # 舊 run，不應出現
            pred("2026-09-02_aaa_150000", d2, "1102", 2),
            pred("2026-09-02_aaa_150000", d2, "1103", 1, event_type=int(Event.TARGET), matured_at=date(2026, 9, 12)),
            pred("2026-09-02_aaa_150000", d2, "1104", 3, rec=False),               # 未推薦
            pred("2026-09-03_m_150000", d3, "1101", 1),
        ])
        s.commit()
        res = tracking.load_tracking(sqlite3.connect(":memory:"), s, days=10)
        got = [(it["signal_date"], it["stock_id"]) for it in res["items"]]
        assert got == [("2026-09-03", "1101"), ("2026-09-02", "1103"), ("2026-09-02", "1102")]
        by = {(it["signal_date"], it["stock_id"]): it for it in res["items"]}
        assert by[("2026-09-02", "1103")]["status"] == "TARGET"          # ledger 覆蓋
        assert by[("2026-09-02", "1103")]["name"] == "N1103"
        assert res["as_of"] == "2026-09-10" and res["summary"]["n"] == 3

        s.query(models.MLEntryPrediction).delete()
        s.query(models.MLEntryRun).delete()
        s.commit()
        assert tracking.load_tracking(sqlite3.connect(":memory:"), s, days=10) == {
            "as_of": "2026-09-10", "summary": summarize([]), "items": []}
        # signal_date 不在日曆且晚於最後交易日：不得拋例外，回空結構
        d11 = date(2026, 9, 11)
        s.add(run("2026-09-11_m_150000", d11, datetime(2026, 9, 11, 15), "m"))
        s.flush()
        s.add(pred("2026-09-11_m_150000", d11, "1101", 1))
        s.commit()
        assert tracking.load_tracking(sqlite3.connect(":memory:"), s, days=10)["items"] == []


def test_load_window_matrices_bounded():
    import sqlite3

    from app.mlentry.data.calendar import TradingCalendar
    from app.mlentry.serving import tracking

    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE daily_prices (stock_id TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)")
    rows = []
    for sid in ("1101", "1102", "9999"):
        for d in ("2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03"):
            rows.append((sid, d, 1.0, 2.0, 0.5, 1.5, 10))
    con.executemany("INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?)", rows)
    cal = TradingCalendar(["2026-09-01", "2026-09-02", "2026-09-03"])
    cols = pd.Index(["1101", "1102", "1103"], name="stock_id")      # 1103 無資料
    m = tracking._load_window_matrices(con, cal, cols)
    assert set(m) == {"open", "high", "low", "close"}
    for k, df in m.items():
        assert df.shape == (3, 3)
        assert list(df.index) == ["2026-09-01", "2026-09-02", "2026-09-03"] and list(df.columns) == ["1101", "1102", "1103"]
        assert df["1103"].isna().all()
        assert df[["1101", "1102"]].notna().all().all()
    assert m["high"].iloc[0, 0] == 2.0
