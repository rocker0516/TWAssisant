"""mlentry B1–B3：任務樣本規則、估計器介面、OOF 只用 dev 且 purge、MFE cap 逐 fold、指標。"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from app.mlentry.datasets.api import DatasetSlice
from app.mlentry.evaluation import model_metrics as mm
from app.mlentry.labels.barriers import EntryStatus, Event
from app.mlentry.models import oof
from app.mlentry.models.estimators import make_model
from app.mlentry.models.tasks import all_tasks, day_weights, task_frame

CFG = {"lgbm_binary": {"n_estimators": 20, "num_leaves": 7, "min_child_samples": 5, "verbose": -1, "n_jobs": 1},
       "lgbm_regression": {"objective": "huber", "n_estimators": 20, "num_leaves": 7, "min_child_samples": 5,
                           "verbose": -1, "n_jobs": 1},
       "logreg": {"C": 1.0}, "mfe_winsor_quantile": 0.9}


def _outcomes():
    return pd.DataFrame({
        "sample_id": ["a", "b", "c", "d", "e", "f"],
        "signal_date": ["d1"] * 3 + ["d2"] * 3,
        "entry_status": [0, 0, 1, 0, 2, -1],
        "entry_executable": [1, 1, 0, 1, 0, 0],
        "matured": [1, 1, 1, 0, 1, 0],
        "event_type": [Event.TARGET, Event.STOP_AMBIGUOUS, Event.NOT_ENTERED, Event.PENDING, Event.NOT_ENTERED, Event.PENDING],
        "target_hit_10d": [1, 0, np.nan, np.nan, np.nan, np.nan],
        "stop_hit_10d": [0, 1, np.nan, np.nan, np.nan, np.nan],
        "mfe_10d": [0.12, 0.05, np.nan, np.nan, np.nan, np.nan],
    })


def test_task_frames_follow_hard_rules():
    t = all_tasks()
    ex = task_frame(t["execution"], _outcomes())
    assert list(ex["sample_id"]) == ["a", "b", "c", "d", "e"]          # PENDING 排除
    assert ex["y"].tolist() == [0, 0, 1, 0, 1]                          # 學 P(blocked)
    tg = task_frame(t["target_10d"], _outcomes())
    assert list(tg["sample_id"]) == ["a", "b"]                          # 只 executable & matured
    assert tg["w_task"].tolist() == [1.0, 0.0]                          # STOP_AMBIGUOUS weight 0
    st = task_frame(t["stop_10d"], _outcomes())
    assert st["w_task"].tolist() == [1.0, 0.0]
    mf = task_frame(t["mfe_10d"], _outcomes())
    assert mf["w_task"].tolist() == [1.0, 1.0]                          # 回歸不歸零
    assert day_weights(pd.Series(["d1", "d1", "d2"])).tolist() == pytest.approx([0.5, 0.5, 1.0])


def test_estimators_interface():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(300, 4)).astype("float32"); X[::7, 0] = np.nan
    y = (X[:, 1] + rng.normal(scale=0.5, size=300) > 0).astype("float32")
    for name in ("prevalence", "logreg", "lgbm"):
        m = make_model(name, "binary", CFG).fit(X, y, np.ones(300))
        p = m.predict(X)
        assert p.shape == (300,) and p.dtype == np.float32 and (0 <= p).all() and (p <= 1).all()
    assert np.allclose(make_model("prevalence", "binary", CFG).fit(X, y).predict(X[:3]), y.mean())
    r = make_model("lgbm", "regression", CFG).fit(X, X[:, 1], None).predict(X)
    assert r.shape == (300,)


def _dev_slice(n_days=400, n_stocks=30, seed=0):
    rng = np.random.default_rng(seed)
    dates = [f"d{i:04d}" for i in range(n_days)]
    rows = [(f"s{j}", d) for d in dates for j in range(n_stocks)]
    sid = [f"{s}_{d}" for s, d in rows]
    f1 = rng.normal(size=len(rows)).astype("float32")
    f2 = rng.normal(size=len(rows)).astype("float32")
    feats = pd.DataFrame({"sample_id": sid, "stock_id": [r[0] for r in rows], "signal_date": [r[1] for r in rows],
                          "f1": f1, "f2": f2})
    y = (f1 + rng.normal(scale=1.0, size=len(rows)) > 1.0).astype("float32")
    out = pd.DataFrame({"sample_id": sid, "signal_date": feats["signal_date"], "entry_status": 0,
                        "entry_executable": 1, "matured": 1, "event_type": np.where(y == 1, 1, 4),
                        "target_hit_10d": y, "mfe_10d": (f1 * 0.05 + rng.normal(scale=0.02, size=len(rows))).astype("float32")})
    out.loc[out.index[-n_stocks * 10:], ["matured"]] = 0
    folds = [{"name": "dev_00", "train_start": "d0000", "train_end": "d0189", "val_start": "d0200", "val_end": "d0299",
              "n_train_days": 190, "n_val_days": 100},
             {"name": "dev_01", "train_start": "d0100", "train_end": "d0289", "val_start": "d0300", "val_end": "d0389",
              "n_train_days": 190, "n_val_days": 90}]
    splits = {"split_version": "s_test", "as_of": dates[-1], "max_horizon": 10, "dev_folds": folds, "holdout": None,
              "dev_start": dates[0], "dev_end": dates[-1], "holdout_start": None, "holdout_end": None}
    manifest = {"versions": {"feature_version": "f_x", "label_version": "l_x"}, "feature_names": ["f1", "f2"]}
    return DatasetSlice("ds_test", "development", feats, out, manifest, splits)


def test_run_oof_binary_purged_and_stored(tmp_path):
    dev = _dev_slice()
    task = all_tasks()["target_10d"]
    preds, meta = oof.run_oof(dev, task, "lgbm", ["f1", "f2"], CFG, out_root=tmp_path)
    assert set(preds["fold"]) == {"dev_00", "dev_01"}
    assert preds["signal_date"].min() >= "d0200"                       # 只有 validation 段有預測
    assert meta.folds[0].n_train == 190 * 30 and meta.folds[0].train_positive_rate is not None
    m = mm.binary_metrics(preds["y"].to_numpy(), preds["pred"].to_numpy(), preds["w"].to_numpy())
    assert m["auc"] > 0.7 and m["top_decile_lift"] > 1.5
    bf = mm.by_fold(preds, "binary")
    assert list(bf.index) == ["dev_00", "dev_01"]
    fs = mm.fold_summary(bf, ("auc", "brier"))
    assert fs.loc["brier", "worst"] == bf["brier"].max() and fs.loc["auc", "worst"] == bf["auc"].min()
    back, meta_json = oof.load_oof(tmp_path, "target_10d", "lgbm")
    assert len(back) == len(preds) and meta_json["feature_names"] == ["f1", "f2"] and meta_json["code_commit"]


def test_run_oof_refuses_holdout_slice():
    dev = _dev_slice()
    bad = DatasetSlice(dev.dataset_version, "holdout", dev.features, dev.outcomes, dev.manifest, dev.splits)
    with pytest.raises(ValueError):
        oof.run_oof(bad, all_tasks()["target_10d"], "prevalence", ["f1", "f2"], CFG)


def test_mfe_cap_estimated_per_training_fold(tmp_path):
    dev = _dev_slice()
    preds, meta = oof.run_oof(dev, all_tasks()["mfe_10d"], "lgbm", ["f1", "f2"], CFG, out_root=tmp_path)
    caps = [f.winsor_cap for f in meta.folds]
    assert all(c is not None for c in caps) and caps[0] != caps[1]
    y_tr0 = dev.outcomes.loc[(dev.outcomes["signal_date"] <= "d0189"), "mfe_10d"]
    assert caps[0] == pytest.approx(float(np.quantile(y_tr0, 0.9)), rel=1e-5)
    r = mm.regression_metrics(preds["y"].to_numpy(), preds["pred"].to_numpy())
    assert r["spearman"] > 0.5


def test_metric_helpers():
    y = np.array([0, 0, 1, 1, 1, 0, 1, 0, 1, 1], dtype=float)
    p = np.linspace(0.05, 0.95, 10)
    assert 0 <= mm.ece(y, p) <= 1
    dec = mm.decile_table(y, p, q=5)
    assert len(dec) == 5 and dec["n"].sum() == 10
    assert mm.top_pct_rate(y, p, 0.2) == 1.0
    rel = mm.reliability(y, p, bins=5)
    assert rel["n"].sum() == 10


def test_challenger_task_frames_direction_and_multiclass():
    t = all_tasks()
    o = pd.DataFrame({
        "sample_id": list("abcdef"), "signal_date": ["d1"] * 6,
        "entry_status": [0, 0, 0, 0, 0, 1], "entry_executable": [1, 1, 1, 1, 1, 0], "matured": [1, 1, 1, 1, 0, 1],
        "event_type": [Event.TARGET, Event.STOP, Event.TIMEOUT, Event.STOP_AMBIGUOUS, Event.PENDING, Event.NOT_ENTERED],
        "target_first_hit_day": [3, np.nan, np.nan, 4, np.nan, np.nan],
        "stop_first_hit_day": [7, 2, np.nan, 4, np.nan, np.nan],
    })
    d = task_frame(t["direction_10d"], o)
    assert list(d["sample_id"]) == ["a", "b"] and d["y"].tolist() == [1.0, 0.0]     # 只用已解決列
    d5 = task_frame(t["direction_5d"], o)
    assert list(d5["sample_id"]) == ["a", "b"]                                       # 5 日內：a 第 3 日 TARGET、b 第 2 日 STOP
    e = task_frame(t["event_10d"], o)
    assert list(e["sample_id"]) == ["a", "b", "c", "d"] and e["y"].tolist() == [0.0, 1.0, 2.0, 2.0]
    assert e["w_task"].tolist() == [1.0, 1.0, 1.0, 0.0]                              # AMBIGUOUS weight 0
    e3 = task_frame(t["event_3d"], o)
    assert e3["y"].tolist() == [0.0, 1.0, 2.0, 2.0]                                  # a 第 3 日 TARGET 仍在 3 日內
    e2 = task_frame(all_tasks(horizons=(2,))["event_2d"], o)
    assert e2["y"].tolist() == [2.0, 1.0, 2.0, 2.0]                                  # 2 日內 a 尚未觸 → TIMEOUT
