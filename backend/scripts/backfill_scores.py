"""回補歷史評分（Score）→ 讓回看月曆往前有資料，且全程現行制度。

Score 只從每日盤後排程開始累積（2026-05-21 起），更早的日子回看月曆全空。
ScoringEngine 本身是 point-in-time（各表切 ≤ td、遲滯/穩定度只讀 date<td、
營收財報依公布時點切片、估值已改 PIT），由舊往新逐日重跑即可回補，
分數/風格/遲滯與線上同一套現行制度。

誠實限制（歷史日資料缺口，只能接受並知情）：
  - sector_daily 僅 2026-06 起 → 歷史日 sector_adjust=0（不進 wave rank 總分，影響小）
  - event 僅 2026-05 起 → 歷史日「處置股排除」失效、長線 OutlookScore 無事件
  - 集保僅 2025-06 起 → 更早 ChipScore 大戶趨勢 nudge=0（設計上本就降級）

冪等可續跑：已有 Score 列的日期自動跳過（--force 覆寫重算）。
逐日 commit，中斷再跑會接續。請避開夜間排程時段（預設 21:30）跑，SQLite 寫鎖互撞。

用法：.venv/bin/python scripts/backfill_scores.py [--start 2025-08-06] [--end 2026-05-20] [--force]
"""

from __future__ import annotations

import sys
import time
from datetime import date

from sqlalchemy import select

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])

from app.engines.scoring import ScoringEngine  # noqa: E402
from app.storage import models  # noqa: E402
from app.storage.database import SessionLocal  # noqa: E402


def _arg(name: str, default: str | None) -> str | None:
    if name in sys.argv:
        return sys.argv[sys.argv.index(name) + 1]
    return default


def main() -> None:
    force = "--force" in sys.argv
    start = date.fromisoformat(_arg("--start", "2025-08-06"))
    end_s = _arg("--end", None)

    session = SessionLocal()
    engine = ScoringEngine()
    try:
        # 預設補到今天；已有 Score 列的日期本來就會跳過，中斷續跑/補漏洞都靠這條
        end = date.fromisoformat(end_s) if end_s else date.today()
        axis = session.execute(
            select(models.DailyPrice.date).distinct()
            .where(models.DailyPrice.date >= start, models.DailyPrice.date < end)
            .order_by(models.DailyPrice.date)
        ).scalars().all()
        done_dates = set(
            session.execute(
                select(models.Score.date).distinct().where(models.Score.date >= start)
            ).scalars().all()
        )
        todo = axis if force else [d for d in axis if d not in done_dates]
        print(f"回補範圍 {start} → {end}（不含）：交易日 {len(axis)}、待跑 {len(todo)}"
              + ("（--force 覆寫）" if force else "（已存在自動跳過）"))
        if not todo:
            print("沒有要補的日期。")
            return

        t_all = time.time()
        for i, td in enumerate(todo, 1):
            t0 = time.time()
            res = engine.run(session, td)
            session.commit()
            passed = res.get("passed", {})
            eta = (time.time() - t_all) / i * (len(todo) - i)
            print(f"[{i}/{len(todo)}] {td}  scored={res.get('scored_stocks')} "
                  f"wave過={passed.get('wave')} long過={passed.get('long')} "
                  f"{time.time()-t0:.1f}s  ETA {eta/60:.0f}m", flush=True)
        print(f"完成，共 {len(todo)} 日、{(time.time()-t_all)/60:.1f} 分鐘。")
        print("回看月曆命中率為即時計算，重整前端即可看到新日期。")
    finally:
        session.close()


if __name__ == "__main__":
    main()
