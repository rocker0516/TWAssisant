"""ML 進場推薦（Production Shadow）每日執行：成熟回填 + 當日 run（§22）。

    python -m scripts.mlentry_daily [--date YYYY-MM-DD] [--mature-only] [--no-mature]

由 scheduler.steps.MLEntryDailyStep 以子行程呼叫（21:30 盤後、Level1PredictStep 之後）。
不掛 backfill 管線：缺日就是缺日（回補的預測不是前瞻證據）。
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.mlentry.serving.daily_run import mature_outcomes, run_daily  # noqa: E402
from app.storage.database import init_db, session_scope  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--mature-only", action="store_true")
    ap.add_argument("--no-mature", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_db()
    con = sqlite3.connect(str(get_settings().db_path))
    try:
        with session_scope() as session:
            if not args.no_mature:
                n = mature_outcomes(con, session)
                print(f"matured/updated rows: {n}")
            if not args.mature_only:
                r = run_daily(con, session, args.date)
                print(json.dumps({"run_id": r.run_id, "signal_date": r.signal_date, "status": r.status,
                                  "no_trade_reason": r.no_trade_reason, "universe": r.universe_count,
                                  "qualified": r.qualified_count, "recommended": r.recommendation_count,
                                  "top": r.recommendations[:5]}, ensure_ascii=False, default=str))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
