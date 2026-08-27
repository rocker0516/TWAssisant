"""一次性回補：全池集保股權分散近一年週歷史（TDCC 智慧網逐檔爬）。

情境路由矩陣污染稽核判定 `retail_cnt_chg`（散戶人數週變化）覆蓋率 0%：
shareholding 表只有開放資料「最新一週」快照累積 ＋ 少數個股頁觸發的單檔回補。

⚠ 已知限制（docs/ctx-matrix-findings.md 已記載）：TDCC 智慧網歷史只保留
**約一年**（官方歸檔保存期限一年），挖掘窗 2021-01~2024-12 的散戶人數免費管道
不存在（FinMind TaiwanStockHoldingSharesPer 為贊助等級）。本腳本補的是
holdout/前瞻期（約 2025-08 起），用途：
  1. 讓每週 opendata 快照累積之外，先一次補滿近 52 週；
  2. 之後挖掘窗滾動或改用近期資料驗證時即有存量。

- 逐檔爬（每檔 1 GET + ≤52 POST），已有 ≥50 週的檔自動略過（可續跑）。
- 落庫沿用 services.shareholding_backfill 的寫鎖重試邏輯。
- 全池 ~2,500 檔 ≈ 13 萬請求，tdcc 限流下 ≈ 1.5~2 天長跑背景工作。

用法：
    cd backend
    python -m scripts.backfill_holders_history            # 全池
    python -m scripts.backfill_holders_history 2330 2317  # 指定檔
"""

from __future__ import annotations

import re
import sys
import time

from sqlalchemy import func, select

from app.sources import registry
from app.sources.base import SourceError
from app.storage import models
from app.storage.database import SessionLocal, init_db
from app.services.shareholding_backfill import _write  # 寫鎖重試＋FK 防護

_MIN_WEEKS = 50  # 已有這麼多週視為補過


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _todo_ids() -> list[str]:
    """非 ETF 池限 4 碼普通股（濾掉受益證券/特別股等非公司代號，同
    backfill_insider_history），扣掉已有 ≥_MIN_WEEKS 週的檔。股號由小到大。"""
    with SessionLocal() as s:
        stocks = s.execute(select(models.Stock.id, models.Stock.is_etf)).all()
        weeks = dict(
            s.execute(
                select(models.ShareholdingDistribution.stock_id, func.count())
                .group_by(models.ShareholdingDistribution.stock_id)
            ).all()
        )
    return sorted(
        sid
        for sid, is_etf in stocks
        if not is_etf
        and re.fullmatch(r"[1-9]\d{3}", str(sid))
        and weeks.get(sid, 0) < _MIN_WEEKS
    )


def main() -> None:
    init_db()
    ids = sys.argv[1:] or _todo_ids()
    _log(f"集保週歷史回補：待補 {len(ids)} 檔（每檔 ≤52 週）")

    src = registry.provider("holding")
    done = failed = 0
    t0 = time.monotonic()
    for i, sid in enumerate(ids, 1):
        try:
            df = src.fetch_holding_history(sid, 52)
        except SourceError as exc:
            failed += 1
            _log(f"  {sid} 失敗：{exc.reason}")
            continue
        except Exception as exc:  # noqa: BLE001 — 長跑不可炸，記錄後續跑
            failed += 1
            _log(f"  {sid} 異常：{exc.__class__.__name__}: {exc}")
            continue
        if df.empty:
            _log(f"  {sid} 無資料（下市/無集保？）")
        else:
            recs = df.astype(object).where(df.notna(), None).to_dict("records")
            _write(sid, recs)
        done += 1
        if i % 20 == 0:
            rate = i / (time.monotonic() - t0)
            _log(
                f"  進度 {i}/{len(ids)}（{i / len(ids):.1%}）失敗 {failed}，"
                f"ETA {(len(ids) - i) / rate / 3600:.1f}h"
            )
    _log(f"全部完成：成功 {done}、失敗 {failed}／共 {len(ids)} 檔")


if __name__ == "__main__":
    main()
