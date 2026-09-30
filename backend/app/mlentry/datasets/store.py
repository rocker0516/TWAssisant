"""Parquet 資料集儲存（§10、§11）：三張表 + manifest.json，目錄 = dataset_version。

data/mlentry/<dataset_version>/
  manifest.json
  sample_index.parquet              （全 coverage 列，含 is_holdout）
  development/{features,outcomes}/year=YYYY/*.parquet
  holdout/{features,outcomes}/year=YYYY/*.parquet   （Final Holdout 物理隔離，§19.5）
  splits.json                       （validation.walk_forward 產出）

讀取一律走 datasets/api.py：load_development() / load_final_holdout()。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "data" / "mlentry"


@dataclass
class Manifest:
    dataset_version: str
    as_of: str
    db_max_date: str
    calendar_start: str
    calendar_end: str
    versions: dict[str, str]
    feature_names: list[str]
    label_columns: list[str]
    row_counts: dict[str, int]
    feature_missing_rate: dict[str, float]
    universe_daily_median: float
    code_commit: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, sort_keys=True)


def code_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "unknown"


def _write_partitioned(df: pd.DataFrame, path: Path) -> None:
    tbl = pa.Table.from_pandas(df.assign(year=df["signal_date"].str[:4]), preserve_index=False)
    pq.write_to_dataset(tbl, root_path=str(path), partition_cols=["year"],
                        compression="zstd", existing_data_behavior="delete_matching")


def write_dataset(root: Path, manifest: Manifest, sample_index: pd.DataFrame,
                  features: pd.DataFrame, outcomes: pd.DataFrame,
                  is_holdout: pd.Series | None = None) -> Path:
    """is_holdout：與 features/outcomes 同長度的 bool（列順序一致）。None = 全部 development。"""
    d = root / manifest.dataset_version
    if (d / "manifest.json").exists():
        raise FileExistsError(f"{d} already exists: frozen datasets are never overwritten (bump a version)")
    d.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(sample_index, preserve_index=False),
                   d / "sample_index.parquet", compression="zstd")
    hold = (is_holdout.to_numpy(dtype=bool) if is_holdout is not None
            else np.zeros(len(features), dtype=bool))
    for name, df in (("features", features), ("outcomes", outcomes)):
        _write_partitioned(df.loc[~hold], d / "development" / name)
        if hold.any():
            _write_partitioned(df.loc[hold], d / "holdout" / name)
    (d / "manifest.json").write_text(manifest.to_json(), encoding="utf-8")
    return d


def read_manifest(d: Path) -> dict:
    return json.loads((d / "manifest.json").read_text(encoding="utf-8"))


def read_table(d: Path, name: str, columns: list[str] | None = None,
               years: list[str] | None = None, split: str = "development") -> pd.DataFrame:
    """name ∈ {sample_index, features, outcomes}；features/outcomes 需指定 split。

    直接呼叫此函式讀 holdout 是繞過 api.load_final_holdout 的稽核——請勿這麼做。
    """
    if name == "sample_index":
        return pq.read_table(str(d / "sample_index.parquet"), columns=columns).to_pandas()
    p = d / split / name
    if not p.is_dir():
        return pd.DataFrame(columns=columns or [])
    filters = [("year", "in", years)] if years else None
    return pq.read_table(str(p), columns=columns, filters=filters).to_pandas().drop(
        columns=["year"], errors="ignore")
