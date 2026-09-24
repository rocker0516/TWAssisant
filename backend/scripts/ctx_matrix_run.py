"""情境路由矩陣挖掘管線 CLI（context-routing-matrix task-7）。

用法：PYTHONIOENCODING=utf-8 python scripts/ctx_matrix_run.py build|audit|mine
  build : 組 chip+other+labels+context 特徵 → backend/data/ctx_features.pkl
  audit : 污染稽核閘（覆蓋率/ts_share/控ATR挖掘窗vs holdout） → backend/data/ctx_contamination.json
  mine  : 群×情境×訊號格子挖掘 → backend/data/ctx_matrix.json
"""
from __future__ import annotations

import argparse
import sys

_BASE = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0]
sys.path.insert(0, _BASE)

from app.config import get_settings           # noqa: E402
from app.research.ctx_matrix import pipeline   # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="情境路由矩陣挖掘管線")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="組裝特徵 → ctx_features.pkl")
    sub.add_parser("audit", help="污染稽核閘 → ctx_contamination.json")
    sub.add_parser("mine", help="格子挖掘 → ctx_matrix.json")
    args = parser.parse_args()

    db_path = str(get_settings().db_path)

    if args.cmd == "build":
        pipeline.build(db_path)
    elif args.cmd == "audit":
        pipeline.audit()
    elif args.cmd == "mine":
        pipeline.mine(db_path=db_path)


if __name__ == "__main__":
    main()
