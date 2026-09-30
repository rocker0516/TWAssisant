"""Parquet 資料集儲存（§10、§11）：三張表 + manifest.json，目錄 = dataset_version。

data/mlentry/<dataset_version>/
  manifest.json
  sample_index.parquet
  features/year=YYYY/*.parquet
  outcomes/year=YYYY/*.parquet
  splits.json            （validation.walk_forward 產出）
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

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
                  features: pd.DataFrame, outcomes: pd.DataFrame) -> Path:
    d = root / manifest.dataset_version
    d.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(sample_index, preserve_index=False),
                   d / "sample_index.parquet", compression="zstd")
    _write_partitioned(features, d / "features")
    _write_partitioned(outcomes, d / "outcomes")
    (d / "manifest.json").write_text(manifest.to_json(), encoding="utf-8")
    return d


def read_manifest(d: Path) -> dict:
    return json.loads((d / "manifest.json").read_text(encoding="utf-8"))


def read_table(d: Path, name: str, columns: list[str] | None = None,
               years: list[str] | None = None) -> pd.DataFrame:
    p = d / name
    if p.is_dir():
        filters = [("year", "in", years)] if years else None
        return pq.read_table(str(p), columns=columns, filters=filters).to_pandas().drop(
            columns=["year"], errors="ignore")
    return pq.read_table(str(d / f"{name}.parquet"), columns=columns).to_pandas()
