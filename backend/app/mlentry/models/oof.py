"""B3 OOF Prediction Store：依 Split 的 dev fold 逐一 fit(train) → predict(validation)，存 Parquet + meta。

data/mlentry/<dataset_version>/oof/<task>__<model>/
  predictions.parquet   (sample_id, stock_id, signal_date, fold, y, w, pred)
  meta.json             (task, model, params, feature_version, code_commit, per-fold n_train/n_val/cap…)

硬規則：只讀 development 分區；MFE winsorization cap 由該 fold 的 training 樣本估（附錄 B-4）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..config import load_yaml
from ..datasets.api import DatasetSlice
from ..datasets.store import code_commit
from ..validation.walk_forward import Fold, Split
from .estimators import make_model
from .tasks import TaskSpec, day_weights, task_frame

log = logging.getLogger(__name__)


@dataclass
class FoldMeta:
    fold: str
    n_train: int
    n_val: int
    train_positive_rate: float | None
    winsor_cap: float | None
    fit_seconds: float


@dataclass
class RunMeta:
    task: str
    kind: str
    model: str
    dataset_version: str
    feature_version: str
    label_version: str
    split_version: str
    params: dict
    feature_names: list[str]
    code_commit: str
    folds: list[FoldMeta] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def _split_from_dict(d: dict) -> Split:
    folds = tuple(Fold(**f) for f in d["dev_folds"])
    hold = Fold(**d["holdout"]) if d.get("holdout") else None
    return Split(d["split_version"], d["as_of"], d["max_horizon"], folds, hold,
                 d["dev_start"], d["dev_end"], d["holdout_start"], d["holdout_end"])


def _mask(sd: pd.Series, start: str, end: str) -> np.ndarray:
    s = sd.to_numpy().astype(str)
    return (s >= start) & (s <= end)


def run_oof(dev: DatasetSlice, task: TaskSpec, model_name: str, feature_names: list[str],
            models_cfg: dict | None = None, out_root: Path | None = None) -> tuple[pd.DataFrame, RunMeta]:
    if dev.split != "development":
        raise ValueError("OOF must run on the development slice only")
    models_cfg = models_cfg or load_yaml("models")
    split = _split_from_dict(dev.splits)
    tf = task_frame(task, dev.outcomes)
    X_all = dev.features.set_index("sample_id")
    tf = tf[tf["sample_id"].isin(X_all.index)].reset_index(drop=True)
    stock = X_all["stock_id"]
    Xf = X_all[feature_names].to_numpy(dtype="float32")
    pos = pd.Series(np.arange(len(X_all)), index=X_all.index)
    tf["row"] = pos.loc[tf["sample_id"]].to_numpy()
    tf["w"] = (tf["w_task"].to_numpy() * day_weights(tf["signal_date"])).astype("float32")

    meta = RunMeta(task.name, task.kind, model_name, dev.dataset_version,
                   dev.manifest["versions"]["feature_version"], dev.manifest["versions"]["label_version"],
                   split.split_version,
                   {k: v for k, v in models_cfg.items() if k in ("lgbm_binary", "lgbm_regression", "logreg", "mfe_winsor_quantile")},
                   list(feature_names), code_commit())
    preds = []
    import time
    for f in split.dev_folds:
        tr = _mask(tf["signal_date"], f.train_start, f.train_end)
        va = _mask(tf["signal_date"], f.val_start, f.val_end)
        if tr.sum() == 0 or va.sum() == 0:
            log.warning("%s/%s %s: empty fold", task.name, model_name, f.name)
            continue
        ytr = tf.loc[tr, "y"].to_numpy(dtype="float32")
        cap = None
        if task.kind == "regression":
            q = float(models_cfg.get("mfe_winsor_quantile", 0.99))
            cap = float(np.quantile(ytr, q))               # 只看 training fold
            ytr = np.minimum(ytr, cap)
        t0 = time.time()
        model = make_model(model_name, task.kind, models_cfg)
        model.fit(Xf[tf.loc[tr, "row"].to_numpy()], ytr, tf.loc[tr, "w"].to_numpy())
        p = model.predict(Xf[tf.loc[va, "row"].to_numpy()])
        dt = time.time() - t0
        sub = tf.loc[va, ["sample_id", "signal_date", "y", "w"]].copy()
        sub["stock_id"] = stock.loc[sub["sample_id"]].to_numpy()
        sub["fold"] = f.name
        sub["pred"] = p.astype("float32")
        preds.append(sub)
        meta.folds.append(FoldMeta(f.name, int(tr.sum()), int(va.sum()),
                                   float(np.average(ytr, weights=tf.loc[tr, "w"])) if task.kind == "binary" else None,
                                   cap, round(dt, 1)))
        log.info("%s/%s %s: n_train=%d n_val=%d %.0fs", task.name, model_name, f.name, tr.sum(), va.sum(), dt)
    out = pd.concat(preds, ignore_index=True) if preds else pd.DataFrame(
        columns=["sample_id", "signal_date", "y", "w", "stock_id", "fold", "pred"])
    if out_root is not None:
        d = out_root / "oof" / f"{task.name}__{model_name}"
        d.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pandas(out, preserve_index=False), d / "predictions.parquet",
                       compression="zstd")
        (d / "meta.json").write_text(json.dumps(asdict(meta), ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    return out, meta


def load_oof(ds_dir: Path, task: str, model: str) -> tuple[pd.DataFrame, dict]:
    d = ds_dir / "oof" / f"{task}__{model}"
    return (pq.read_table(str(d / "predictions.parquet")).to_pandas(),
            json.loads((d / "meta.json").read_text(encoding="utf-8")))
