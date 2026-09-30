"""mlentry 資料集與驗證：purge、walk-forward split 隔離、權重、end-to-end builder（in-memory DB）。"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
import pytest

from app.mlentry.config import (FeatureConfig, LabelConfig, MLEntryConfig, UniverseConfig,
                                ValidationConfig)
from app.mlentry.datasets import builder, store
from app.mlentry.datasets.weighting import day_normalized_weights
from app.mlentry.validation.purge import assert_no_label_leak, purged_train_positions
from app.mlentry.validation.walk_forward import build_split, check_split_integrity, fold_masks


# ── purge ──

def test_purged_train_positions_rolling_and_expanding():
    assert list(purged_train_positions(100, 10, None)) == list(range(0, 90))       # 89+10 < 100
    assert list(purged_train_positions(100, 10, 20)) == list(range(70, 90))
    assert len(purged_train_positions(5, 10, None)) == 0


def test_assert_no_label_leak():
    assert_no_label_leak(pd.Series(["2025-01-02", "2025-01-03"]), "2025-01-06")
    with pytest.raises(AssertionError):
        assert_no_label_leak(pd.Series(["2025-01-02", "2025-01-06"]), "2025-01-06")
    with pytest.raises(AssertionError):
        assert_no_label_leak(pd.Series(["2025-01-02", None]), "2025-01-06")


# ── walk-forward ──

def _dates(n):
    return pd.Index([f"2024-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}{i // 336}" for i in range(n)])


def test_build_split_isolates_holdout_and_purges():
    dates = pd.Index([f"d{i:04d}" for i in range(1000)])
    cfg = ValidationConfig(train_window_days=300, fold_days=100, holdout_days=200, min_train_days=100)
    sp = build_split(dates, cfg, max_horizon=10)
    check_split_integrity(sp, dates)
    assert sp.holdout.test_start == "d0790" and sp.holdout.test_end == "d0989"   # 最後 10 日未成熟
    assert sp.holdout.n_train_days == 300
    assert len(sp.dev_folds) == 5                       # 完整 300 日窗 + purge 後才開第一個 fold
    assert sp.dev_folds[0].test_start == "d0311"
    for f in sp.dev_folds:
        assert f.test_end < sp.holdout.test_start
        assert int(f.train_end[1:]) + 10 < int(f.test_start[1:])
        assert f.n_train_days == 300
    relaxed = build_split(dates, ValidationConfig(train_window_days=300, fold_days=100, holdout_days=200,
                                                  min_train_days=100, require_full_window=False), 10)
    assert len(relaxed.dev_folds) > 5 and relaxed.dev_folds[0].n_train_days < 300
    # 連續 fold 的 test 區塊接續、不重疊
    for a, b in zip(sp.dev_folds, sp.dev_folds[1:]):
        assert int(b.test_start[1:]) == int(a.test_end[1:]) + 1
    assert sp.split_version.startswith("s_")
    assert sp.is_holdout(pd.Series(["d0500", "d0800"])).tolist() == [False, True]


def test_build_split_expanding_window():
    dates = pd.Index([f"d{i:04d}" for i in range(600)])
    cfg = ValidationConfig(train_window_days=None, fold_days=100, holdout_days=100, min_train_days=50)
    sp = build_split(dates, cfg, max_horizon=5)
    assert sp.dev_folds[0].train_start == "d0000" and sp.dev_folds[-1].train_start == "d0000"
    assert sp.dev_folds[-1].n_train_days > sp.dev_folds[0].n_train_days


# ── weights ──

def test_day_normalized_weights_sum_to_one_per_day():
    si = pd.DataFrame({"signal_date": ["a", "a", "a", "b", "b"], "eligible": [True, True, False, True, True]})
    w = day_normalized_weights(si)
    assert w.iloc[0] == pytest.approx(0.5) and np.isnan(w.iloc[2]) and w.iloc[3] == pytest.approx(0.5)


# ── end-to-end builder ──

def _db(n_days=60, stocks=("1101", "2330", "0050", "9999")):
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE stocks(id TEXT, name TEXT, is_etf INT, market TEXT, industry_category TEXT, listed_date TEXT, sector_id INT)")
    con.executemany("INSERT INTO stocks VALUES (?,?,?,?,?,?,?)", [
        ("1101", "台泥", 0, "上市", "水泥", "1962-02-09", 1),
        ("2330", "台積電", 0, "上市", "半導體", "1994-09-05", 2),
        ("0050", "ETF", 1, "上市", "ETF", "2003-06-30", None),
        ("9999", "新股", 0, "上櫃", "電子", "2025-01-01", 2),
    ])
    con.execute("CREATE TABLE daily_prices(stock_id TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume INT, turnover REAL)")
    con.execute("CREATE TABLE market_index(date TEXT, close REAL)")
    con.execute("CREATE TABLE attention_listings(stock_id TEXT, date TEXT, kind TEXT, times INT, begin_date TEXT, end_date TEXT, reason TEXT)")
    rng = np.random.default_rng(1)
    dates = [f"2025-{i // 28 + 1:02d}-{i % 28 + 1:02d}" for i in range(n_days)]
    for j, s in enumerate(stocks):
        px = 100.0
        for i, d in enumerate(dates):
            if s == "9999" and i < n_days - 5:
                continue                         # 新股：歷史不足
            o = px * (1 + rng.normal(0, 0.005)); c = o * (1 + rng.normal(0, 0.02))
            h = max(o, c) * 1.01; l = min(o, c) * 0.99
            con.execute("INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?,?)", (s, d, o, h, l, c, 1000, 1000 * c))
            px = c
    con.executemany("INSERT INTO market_index VALUES (?,?)", [(d, 100.0 + i) for i, d in enumerate(dates)])
    con.execute("INSERT INTO attention_listings VALUES ('1101', ?, 'punish', 1, ?, ?, 'x')",
                (dates[30], dates[30], dates[35]))
    return con, dates


def _cfg():
    return MLEntryConfig(
        universe=UniverseConfig(history_lookback=25, research_start="2025-01-01"),
        features=FeatureConfig(),
        labels=LabelConfig(),
        validation=ValidationConfig(train_window_days=None, fold_days=5, holdout_days=5, min_train_days=5,
                                    require_full_window=False),
    )


def test_builder_end_to_end(tmp_path):
    con, dates = _db()
    ds = builder.build(con, _cfg())
    si, ft, oc = ds.sample_index, ds.features, ds.outcomes
    # coverage 排除 ETF；sample_index 含不合格列；features/outcomes 只有 U_t
    assert set(si["stock_id"]) == {"1101", "2330", "9999"}
    assert si["eligible"].sum() == len(ft) == len(oc)
    assert (ft["sample_id"].to_numpy() == oc["sample_id"].to_numpy()).all()
    assert set(ft["sample_id"]) <= set(si.loc[si["eligible"], "sample_id"])
    # 新股歷史不足 → 不合格；處置股仍合格（是 feature）
    assert not si.loc[si["stock_id"] == "9999", "eligible"].any()
    assert si.loc[(si["stock_id"] == "1101") & (si["signal_date"] == dates[32]), "eligible"].item()
    row = ft.set_index("sample_id").loc[f"1101_{dates[32]}"]
    assert row["is_disposition_stock"] == 1.0
    assert ft.set_index("sample_id").loc[f"2330_{dates[32]}", "is_disposition_stock"] == 0.0
    # 權重：每日合格列權重和 = 1
    w = si.loc[si["eligible"]].groupby("signal_date")["weight"].sum()
    assert np.allclose(w, 1.0)
    # 最後 10 日 PENDING、label_available_date 為 None；最後一日 entry_status 也 PENDING
    last = oc.loc[oc["signal_date"] == dates[-1]]
    assert (last["event_type"] == -1).all() and (last["entry_status"] == -1).all()
    assert last["label_available_date"].isna().all() and last["entry_date"].isna().all()
    d3 = oc.loc[oc["signal_date"] == dates[-3]]
    assert (d3["event_type"] == -1).all() and (d3["entry_status"] == 0).all()
    mat = oc.loc[oc["signal_date"] == dates[40]]
    assert mat["label_available_date"].iloc[0] == dates[50]
    # split 隔離
    check_split_integrity(ds.split, pd.Index(sorted(si["signal_date"].unique())))
    assert si["is_holdout"].any() and not si.loc[si["signal_date"] < ds.split.holdout_start, "is_holdout"].any()
    # manifest 內容
    m = ds.manifest
    assert m.row_counts["features"] == len(ft) and len(m.feature_names) == 57
    assert set(m.versions) == {"universe_version", "feature_config_version", "label_version", "split_version", "feature_version"}
    # 寫出再讀回
    d = store.write_dataset(tmp_path, m, si, ft, oc)
    (d / "splits.json").write_text(ds.split.to_json(), encoding="utf-8")
    back = store.read_table(d, "features", columns=["sample_id", "ret_5d"])
    assert len(back) == len(ft) and "year" not in back.columns
    assert store.read_manifest(d)["dataset_version"] == ds.dataset_version
    idx = store.read_table(d, "sample_index")
    assert idx["eligibility_flags"].dtype == "uint16"


def test_builder_as_of_truncation_matches_full_run():
    """--as-of 截斷後的特徵 == 全段計算在同日的值（生產／研究同一條路）。"""
    con, dates = _db(n_days=70)
    full = builder.build(con, _cfg())
    part = builder.build(con, _cfg(), as_of=dates[50])
    a = full.features.set_index("sample_id").drop(columns=["stock_id", "signal_date"])
    b = part.features.set_index("sample_id").drop(columns=["stock_id", "signal_date"])
    common = b.index[b.index.str.endswith(dates[50])]
    assert len(common) > 0
    pd.testing.assert_frame_equal(a.loc[common], b.loc[common])
