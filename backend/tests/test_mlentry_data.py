"""mlentry 資料層：config、calendar、quality、PIT、universe。"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
import pytest

from app.mlentry.config import LabelConfig, UniverseConfig, load_config
from app.mlentry.data import pit, quality
from app.mlentry.data.calendar import TradingCalendar
from app.mlentry.data.universe import EligFlag, build_universe, history_flags, primary_reason


def _mats(n: int, cols=("A", "B"), base=100.0) -> dict[str, pd.DataFrame]:
    dates = pd.Index([f"2025-01-{i + 1:02d}" for i in range(n)], name="date")
    c = pd.DataFrame(base, index=dates, columns=pd.Index(cols, name="stock_id"))
    return {"open": c.copy(), "high": c + 1, "low": c - 1, "close": c.copy(),
            "volume": c * 0 + 1000, "turnover": c * 1000}


# ── config ──

def test_load_config_versions_are_stable_hashes():
    cfg = load_config()
    v1 = cfg.versions
    assert set(v1) == {"universe_version", "feature_config_version", "label_version", "split_version"}
    assert all(len(v) == 8 for v in v1.values())
    assert cfg.universe.min_liquidity_twd is None
    assert cfg.labels.target_pct == 0.10 and cfg.labels.stop_pct == 0.05
    assert UniverseConfig(history_lookback=60).version != cfg.universe.version


def test_config_rejects_unknown_key_and_bad_horizon():
    with pytest.raises(ValueError):
        UniverseConfig.from_dict({"adv_floor": 1})
    with pytest.raises(ValueError):
        LabelConfig(horizons=(3, 5, 20))


# ── calendar ──

def test_calendar_positions_and_bounds():
    cal = TradingCalendar(["2025-01-03", "2025-01-02", "2025-01-06"])
    assert list(cal.dates) == ["2025-01-02", "2025-01-03", "2025-01-06"]
    assert cal.pos("2025-01-06") == 2
    assert cal.next("2025-01-03") == "2025-01-06"       # 跨週末靠位置，不靠曆日
    assert cal.next("2025-01-06") is None
    assert cal.shift("2025-01-06", -2) == "2025-01-02"
    assert list(cal.window("2025-01-06", 2)) == ["2025-01-03", "2025-01-06"]
    assert list(cal.truncate("2025-01-03")) == ["2025-01-02", "2025-01-03"]
    with pytest.raises(KeyError):
        cal.pos("2025-01-04")


def test_calendar_matches_price_dates_in_db():
    """calendar 唯一來源 = market_index；daily_prices 的日期必須是其子集。"""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE market_index(date TEXT, close REAL)")
    con.executemany("INSERT INTO market_index VALUES (?,?)", [("2025-01-02", 1.0), ("2025-01-03", 1.0)])
    from app.mlentry.data.calendar import load_calendar
    cal = load_calendar(con)
    assert len(cal) == 2 and "2025-01-03" in cal


# ── quality ──

def test_hard_flags_structural_rules_only():
    m = _mats(3)
    m["high"].iloc[1, 0] = 50.0          # high < low / open / close
    m["volume"].iloc[2, 1] = -5
    m["open"].iloc[0, 1] = np.nan
    f = quality.hard_flags(m)
    a1 = int(f.iloc[1, 0])
    assert a1 & quality.HardFlag.HIGH_LT_LOW and a1 & quality.HardFlag.HIGH_LT_OPEN \
        and a1 & quality.HardFlag.HIGH_LT_CLOSE
    assert int(f.iloc[2, 1]) == quality.HardFlag.NEGATIVE_VOLUME
    assert int(f.iloc[0, 1]) == quality.HardFlag.OPEN_MISSING
    assert int(f.iloc[0, 0]) == 0


def test_hard_flags_zero_when_no_row_at_all():
    m = _mats(2)
    for k in m:
        m[k].iloc[1, 0] = np.nan
    assert int(quality.hard_flags(m).iloc[1, 0]) == 0    # NO_PRICE 歸 universe


def test_soft_flags_use_costs_limits_not_pm10():
    m = _mats(3)
    # 前收 100 → 漲停 110（tick 0.5 貼齊仍 110）；high 110 合法、110.5 違規
    m["high"].iloc[1, 0] = 110.0
    m["high"].iloc[2, 0] = 110.5
    m["close"].iloc[2, 0] = 100.0
    f = quality.soft_flags(m)
    assert int(f.iloc[1, 0]) & quality.SoftFlag.PRICE_LIMIT_VIOLATION == 0
    assert int(f.iloc[2, 0]) & quality.SoftFlag.PRICE_LIMIT_VIOLATION
    # 除權息型：open 與 close 都低於跌停
    m2 = _mats(2)
    m2["open"].iloc[1, 1] = 80.0
    m2["close"].iloc[1, 1] = 81.0
    m2["low"].iloc[1, 1] = 79.0
    f2 = quality.soft_flags(m2)
    assert int(f2.iloc[1, 1]) & quality.SoftFlag.CORPORATE_ACTION_SUSPECT
    assert int(f2.iloc[1, 1]) & quality.SoftFlag.ABNORMAL_RETURN
    # soft 旗標不進 universe
    elig, _ = build_universe(m2, UniverseConfig(history_lookback=1))
    assert bool(elig.iloc[1, 1])


# ── PIT ──

def test_event_mask_respects_announcement_date():
    cal = TradingCalendar([f"2025-01-{d:02d}" for d in (2, 3, 6, 7, 8)])
    cols = pd.Index(["A", "B"])
    w = pd.DataFrame([
        {"stock_id": "A", "kind": "punish", "date": "2025-01-06", "begin_date": "2025-01-03", "end_date": "2025-01-07"},
        {"stock_id": "B", "kind": "notice", "date": "2025-01-02", "begin_date": "2025-01-02", "end_date": "2025-01-02"},
        {"stock_id": "Z", "kind": "punish", "date": "2025-01-02", "begin_date": "2025-01-02", "end_date": "2025-01-08"},
    ])
    pm = pit.event_mask(w, cal, cols, "punish")
    assert list(pm["A"]) == [False, False, True, True, False]   # 公告日 01-06 前不可知
    nm = pit.event_mask(w, cal, cols, "notice")
    assert list(nm["B"]) == [True, False, False, False, False]
    assert not pm["B"].any()


def test_fundamental_available_at_max_rule_and_backfill():
    st = pd.Series(["2026-05-15", "2026-05-15", "2026-08-15"])
    fs = pd.Series(["2026-08-27", "2026-09-02", None])       # 回補 / 真觀測晚於期限 / 無側表
    avail, assumed = pit.fundamental_available_at(st, fs)
    assert avail.iloc[0] == pd.Timestamp("2026-05-15") and bool(assumed.iloc[0])
    assert avail.iloc[1] == pd.Timestamp("2026-09-02") and not bool(assumed.iloc[1])
    assert avail.iloc[2] == pd.Timestamp("2026-08-15") and bool(assumed.iloc[2])


def test_truncate_hides_future_rows():
    m = _mats(5)
    cal = TradingCalendar(m["close"].index)
    t = pit.truncate(m, cal, "2025-01-03")
    assert len(t["close"]) == 3 and t["close"].index[-1] == "2025-01-03"


# ── universe ──

def test_history_flags_calendar_based_not_row_count():
    dates = pd.Index([f"d{i:02d}" for i in range(10)])
    valid = pd.DataFrame(True, index=dates, columns=["full", "gapped", "late"])
    valid.loc[dates[1:3], "gapped"] = False          # 早期缺 2 天，累積列數仍多
    valid.loc[dates[:6], "late"] = False             # 第 7 天（d06）才有資料
    f = history_flags(valid, lookback=5, min_coverage=0.9)
    assert int(f.loc[dates[4], "full"]) == EligFlag.HISTORY_TOO_SHORT  # calendar 不足 5 個前日
    assert int(f.loc[dates[5], "full"]) == 0
    assert int(f.loc[dates[5], "gapped"]) == EligFlag.HISTORY_GAPPED   # 窗 d00..d04 缺 2
    assert int(f.loc[dates[9], "gapped"]) == 0                          # 窗 d04..d08 完整
    assert int(f.loc[dates[8], "late"]) == EligFlag.HISTORY_TOO_SHORT  # 窗 d03..d07，首日 d06
    assert int(f.loc[dates[9], "late"]) == EligFlag.HISTORY_TOO_SHORT  # 窗 d04..d08，首日 d06


def test_build_universe_bitmask_and_primary_reason():
    m = _mats(6, cols=("ok", "bad", "none"))
    for k in m:
        m[k]["none"] = np.nan                    # 從未有列
    m["high"].iloc[5, 1] = 1.0                   # 最後一天品質壞
    elig, flags = build_universe(m, UniverseConfig(history_lookback=3, min_history_coverage=0.5))
    assert bool(elig.iloc[5, 0]) and not bool(elig.iloc[5, 1]) and not bool(elig.iloc[5, 2])
    assert int(flags.iloc[5, 1]) == EligFlag.DATA_QUALITY
    assert int(flags.iloc[5, 2]) & EligFlag.NO_PRICE and int(flags.iloc[5, 2]) & EligFlag.HISTORY_TOO_SHORT
    pr = primary_reason(flags)
    assert pr.iloc[5, 2] == "NO_PRICE" and pr.iloc[5, 1] == "DATA_QUALITY" and pr.iloc[5, 0] is None


def test_build_universe_liquidity_only_when_configured():
    m = _mats(25)
    m["turnover"]["B"] = 10.0
    elig_off, _ = build_universe(m, UniverseConfig(history_lookback=5))
    elig_on, flags = build_universe(m, UniverseConfig(history_lookback=5, min_liquidity_twd=1e5))
    assert bool(elig_off.iloc[-1, 1]) and not bool(elig_on.iloc[-1, 1])
    assert int(flags.iloc[-1, 1]) == EligFlag.LOW_LIQUIDITY
