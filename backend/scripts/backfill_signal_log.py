"""回填 signal_log：從既有 scores 逐日重建上榜／掉榜事件。

為什麼可以回填
  scores 每天一列 (stock_id, date, track, passed)，歷史都在。日對日比 passed
  就能重建當時的名單進出，不需要當時就有這張表。

為什麼回填的列要標 backfilled=True
  它們是用**今天的 scores** 回推的。scores 是 upsert 覆寫，期間若有資料修訂或
  評分邏輯調整，回推出來的名單不必然等於當天真正顯示過的名單。公開戰績只有
  backfilled=False 的部分能宣稱「我們事前就說了」；回填段落只能當背景參考。
  這個界線必須在資料裡分得開，不能只寫在文件上。

與已存在的即時事件不衝突
  走 insert-ignore：同一天同一檔若已有即時寫下的事件，回填不會覆蓋它，
  backfilled=False 的標記也就不會被抹掉。可以放心重跑。

用法：
  .venv/Scripts/python scripts/backfill_signal_log.py              # 全部歷史
  .venv/Scripts/python scripts/backfill_signal_log.py --from 2026-01-01
  .venv/Scripts/python scripts/backfill_signal_log.py --dry-run    # 只看會寫幾筆
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

sys.path.insert(0, __file__.replace("\\", "/").rsplit("/scripts/", 1)[0])

from sqlalchemy import distinct, func, select  # noqa: E402

from app.engines.signal_log import SignalLogEngine  # noqa: E402
from app.storage import models  # noqa: E402
from app.storage.database import init_db, session_scope  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="回填 signal_log（上榜／掉榜事件）")
    ap.add_argument("--from", dest="since", help="起始交易日 YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true", help="不寫入，只報告每日筆數")
    args = ap.parse_args(argv)

    init_db()
    since = datetime.strptime(args.since, "%Y-%m-%d").date() if args.since else None

    with session_scope() as s:
        dates = [d for (d,) in s.execute(
            select(distinct(models.Score.date)).order_by(models.Score.date)
        ).all()]
        if not dates:
            print("[skip] scores 無資料，沒有東西可回填。")
            return 0

        # 第一個評分日永遠沒有「前一日」可比，引擎會回 0 筆。明講而不是靜默跳過。
        targets = dates[1:]
        if since:
            targets = [d for d in targets if d >= since]
        if not targets:
            print(f"[skip] {since} 之後沒有可回填的交易日（scores 最新 = {dates[-1]}）。")
            return 0

        before = s.execute(
            select(func.count()).select_from(models.SignalLog)).scalar_one()
        print(f"[run] 回填 {targets[0]} ~ {targets[-1]}，共 {len(targets)} 個交易日"
              f"（signal_log 現有 {before} 筆）")

        total_listed = total_delisted = total_inserted = 0
        for i, d in enumerate(targets, 1):
            r = SignalLogEngine().run(s, d, backfilled=True)
            total_listed += r.get("listed", 0)
            total_delisted += r.get("delisted", 0)
            total_inserted += r.get("inserted", 0)
            if args.dry_run:
                s.rollback()
            elif i % 20 == 0:
                s.commit()  # 分段 commit：釋放 SQLite writer 鎖，長回填不擋住其他寫入
            if r.get("listed") or r.get("delisted"):
                print(f"   {d}  上榜 {r.get('listed', 0):>3}  掉榜 {r.get('delisted', 0):>3}"
                      f"  寫入 {r.get('inserted', 0):>3}"
                      + (f"  （{r['already_logged']} 筆已存在）"
                         if r.get("already_logged") else ""))

        if args.dry_run:
            s.rollback()
            print(f"[dry-run] 未寫入。預計 上榜 {total_listed} / 掉榜 {total_delisted}")
            return 0

        s.commit()
        after = s.execute(
            select(func.count()).select_from(models.SignalLog)).scalar_one()
        print(f"[done] 上榜 {total_listed} / 掉榜 {total_delisted} / "
              f"實際新增 {total_inserted} 筆（signal_log {before} → {after}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
