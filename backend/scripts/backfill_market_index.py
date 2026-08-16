"""回補加權指數歷史（market_index）。

背景
----
`market_index` 只剩 2026-03-13 起 106 列，但 `data/corners.json` 的 per_year 顯示
2021 年 n=57~169 —— 當初 `pop_vol_corners.py` 挖角落時那張表是有完整歷史的，
後來被截斷。影響：
  * 30 個影子軌角落裡有 24 個依賴 mkt_ret20 / mkt_bias60（app/engines/corner_defs.py），
    現在無法回測或重新驗證
  * 研究快取的 mkt_bias60 / mkt_ret20 覆蓋率只有 1.3% / 4.5%

抓法
----
走 `TwseSource.fetch_index`（已改用 FMTQIK 月批次：一個請求回整月約 22 個交易日，
2020-01 起約 80 個請求；TWSE 限流 1 req/s）。數值與原 MI_INDEX 來源已對帳
（2026-04~05 重疊 40 日完全相同）。

用法：
  PYTHONIOENCODING=utf-8 python scripts/backfill_market_index.py [起始年月 預設202001]
"""
from __future__ import annotations

import sys
import time
from datetime import date

_BASE = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0]
sys.path.insert(0, _BASE)

from app.sources.twse import TwseSource            # noqa: E402
from app.storage.database import session_scope     # noqa: E402
from app.storage.repositories import MarketIndexRepository  # noqa: E402
from sqlalchemy import text                        # noqa: E402


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else "202001"
    start = date(int(arg[:4]), int(arg[4:6]), 1)
    end = date.today()

    with session_scope() as s:
        before = s.execute(text(
            "SELECT COUNT(*), MIN(date), MAX(date) FROM market_index")).one()
    _log(f"回補前：{before[0]} 列（{before[1]} ~ {before[2]}）")
    _log(f"抓取 {start} ~ {end}（FMTQIK 月批次，約 {(end.year-start.year)*12+end.month-start.month+1} 個請求）")

    t0 = time.time()
    src = TwseSource()
    df = src.fetch_index(start, end)
    _log(f"取得 {len(df):,} 個交易日（{time.time()-t0:.0f}s）")
    if df.empty:
        raise SystemExit("沒有取得任何資料，中止（不動 DB）")

    rows = df.to_dict("records")
    with session_scope() as s:
        n = MarketIndexRepository().upsert_many(s, rows)
    _log(f"upsert {n:,} 列")

    with session_scope() as s:
        after = s.execute(text(
            "SELECT COUNT(*), MIN(date), MAX(date) FROM market_index")).one()
        gaps = s.execute(text("""
            SELECT substr(date,1,4) y, COUNT(*) n FROM market_index
            GROUP BY y ORDER BY y""")).all()
    _log(f"回補後：{after[0]} 列（{after[1]} ~ {after[2]}）")
    print("\n逐年交易日數（正常年約 240~250）：")
    for y, n in gaps:
        flag = "" if n >= 220 or y == str(end.year) else "  ← 偏少，檢查缺漏"
        print(f"  {y}  {n:>4}{flag}")


if __name__ == "__main__":
    main()
