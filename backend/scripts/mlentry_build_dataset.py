"""建立 ML 進場推薦 FRS v1 資料集（子專案 A 的 CLI）。

用法（backend/ 下）：
    python -m scripts.mlentry_build_dataset [--as-of YYYY-MM-DD] [--root DIR]

產出 data/mlentry/<dataset_version>/{manifest.json, sample_index.parquet, features/, outcomes/, splits.json}。
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.mlentry.config import load_config  # noqa: E402
from app.mlentry.datasets.builder import build_and_write  # noqa: E402
from app.mlentry.datasets.store import DEFAULT_ROOT  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=None, help="signal_date 上限（預設 calendar 最後一天）")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--db", default=None, help="SQLite 路徑（預設 settings.db_path）")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db = args.db or str(get_settings().db_path)
    t0 = time.time()
    con = sqlite3.connect(db)
    try:
        d = build_and_write(con, load_config(), args.as_of, Path(args.root))
    finally:
        con.close()
    print(f"dataset: {d}  ({time.time() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
