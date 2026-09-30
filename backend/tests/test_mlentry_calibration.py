"""B4/B5：calibration 只用前置 fold OOF、方法選擇不破壞 ranking、horizon 單調投影。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.mlentry.models import calibration as cal


def _preds(seed=0, n=4000):
    rng = np.random.default_rng(seed)
    rows = []
    for k in range(3):
        raw = rng.uniform(0.02, 0.6, n)
        y = (rng.uniform(size=n) < raw ** 1.5).astype(float)       # raw 系統性高估
        rows.append(pd.DataFrame({"fold": f"dev_{k:02d}", "signal_date": [f"d{k}{i:04d}" for i in range(n)],
                                  "y": y, "w": 1.0, "pred": raw.astype("float32")}))
    return pd.concat(rows, ignore_index=True)


def test_compare_methods_uses_only_prior_folds():
    p = _preds()
    table, out, recs = cal.compare_methods_by_fold(p, "target_10d", "m1")
    assert set(table.loc[table["fold"] == "dev_00", "method"]) == {"none"}      # 第一 fold 無前置
    assert set(table.loc[table["fold"] == "dev_02", "method"]) == {"none", "platt", "isotonic"}
    assert out.loc[out["fold"] == "dev_00", "pred_isotonic"].isna().all()
    r = [r for r in recs if r.method == "isotonic" and r.fit_period[0].startswith("d0")]
    assert r and r[0].fit_sample_count == 4000 and r[0].calibration_version.startswith("c_")
    assert r[0].metrics_after["ece"] < r[0].metrics_before["ece"]
    ece = table.pivot(index="fold", columns="method", values="ece").loc["dev_02"]
    assert ece["isotonic"] < ece["none"] and ece["platt"] < ece["none"]


def test_choose_method_respects_ranking_guard():
    t = pd.DataFrame([
        {"fold": "dev_01", "method": "none", "ece": 0.10, "brier": 0.2, "auc": 0.70},
        {"fold": "dev_01", "method": "platt", "ece": 0.03, "brier": 0.19, "auc": 0.70},
        {"fold": "dev_01", "method": "isotonic", "ece": 0.01, "brier": 0.18, "auc": 0.65},   # 破壞 ranking
    ])
    assert cal.choose_method(t) == "platt"
    t.loc[2, "auc"] = 0.699
    assert cal.choose_method(t) == "isotonic"
    assert cal.choose_method(t.iloc[:1]) == "none"


def test_project_monotone_horizons():
    p = {3: np.array([0.30, 0.10, 0.20]), 5: np.array([0.20, 0.15, 0.20]), 10: np.array([0.10, 0.40, 0.20])}
    q = cal.project_monotone_horizons(p)
    assert np.allclose(q[3], [0.20, 0.10, 0.20]) and np.allclose(q[5], [0.20, 0.15, 0.20]) \
        and np.allclose(q[10], [0.20, 0.40, 0.20])
    for i in range(3):
        assert q[3][i] <= q[5][i] <= q[10][i]
