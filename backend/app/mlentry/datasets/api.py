"""資料集讀取 API（§19.5 Final Holdout 程式層隔離）。

- load_development()：唯一的日常入口，只回 dev 分區。
- load_final_holdout()：另一個明確入口，每次呼叫都寫 holdout_access.log（誰、何時、為什麼）。
  B 階段的超參、calibration、Gate、ranking weights、K_max 一律不得讀它。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .store import DEFAULT_ROOT, read_manifest, read_table


@dataclass(frozen=True)
class DatasetSlice:
    dataset_version: str
    split: str
    features: pd.DataFrame
    outcomes: pd.DataFrame
    manifest: dict
    splits: dict


def latest_dataset_dir(root: Path = DEFAULT_ROOT) -> Path:
    ds = sorted(p for p in root.glob("ds_*") if (p / "manifest.json").exists())
    if not ds:
        raise FileNotFoundError(f"no dataset under {root}; run scripts.mlentry_build_dataset")
    return ds[-1]


def _load(d: Path, split: str, feature_columns, outcome_columns) -> DatasetSlice:
    m = read_manifest(d)
    sp = json.loads((d / "splits.json").read_text(encoding="utf-8"))
    fcols = None if feature_columns is None else ["sample_id", "stock_id", "signal_date", *feature_columns]
    ocols = None if outcome_columns is None else ["sample_id", "signal_date", *outcome_columns]
    ft = read_table(d, "features", fcols, split=split)
    oc = read_table(d, "outcomes", ocols, split=split)
    return DatasetSlice(m["dataset_version"], split, ft, oc, m, sp)


def load_development(d: Path | None = None, feature_columns=None, outcome_columns=None) -> DatasetSlice:
    return _load(d or latest_dataset_dir(), "development", feature_columns, outcome_columns)


def load_final_holdout(d: Path | None, *, reason: str, actor: str,
                       feature_columns=None, outcome_columns=None) -> DatasetSlice:
    """讀 Final Holdout。reason / actor 必填並落地稽核；讀過即視為已消耗（§19.5）。"""
    if not reason or not actor:
        raise ValueError("holdout access requires explicit reason and actor")
    d = d or latest_dataset_dir()
    with open(d / "holdout_access.log", "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "actor": actor,
                            "reason": reason}, ensure_ascii=False) + "\n")
    return _load(d, "holdout", feature_columns, outcome_columns)
