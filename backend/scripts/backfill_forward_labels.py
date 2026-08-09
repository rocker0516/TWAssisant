"""全市場前瞻標籤回填：每檔每日「後 30 交易日」實現結果 → forward_labels 表。

口徑與所有會噴回測一致（docs/poppability-finding.md + 進場錨改隔天定版）：
  進場錨 entry = 隔天最高價 high[T+1]（盤後看到、隔日追高保守錨）
  mfe30 = 之後 30 交易日（T+2‥T+31）max(high)/entry − 1，%   ← 「漲了最高幾%」
  mae30 = 同窗 min(low)/entry − 1，%
  ret30 = 第 30 根收盤/entry − 1，%
只標「未來 30 根完整」的日子（近端不足 30 根不標，誠實截尾）；ETF(00 開頭)不標。
可重跑（INSERT OR REPLACE），新資料進來後重跑只會補尾端。

用法：python scripts/backfill_forward_labels.py [since]   # 預設 2021-01-01
"""
from __future__ import annotations

import sqlite3
import sys
import time

import numpy as np

_DB = __file__.rsplit("/scripts/", 1)[0] + "/data/twa.db"
_H = 30


def main() -> None:
    since = sys.argv[1] if len(sys.argv) > 1 else "2021-01-01"
    con = sqlite3.connect(_DB, timeout=60)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS forward_labels (
            stock_id TEXT NOT NULL,
            date DATE NOT NULL,
            entry_high REAL NOT NULL,   -- 進場錨=隔日最高
            mfe30 REAL NOT NULL,        -- 後30交易日最高漲幅 %
            mae30 REAL,                 -- 後30交易日最深回落 %
            ret30 REAL,                 -- 第30交易日收盤報酬 %
            PRIMARY KEY (stock_id, date)
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_forward_labels_date ON forward_labels(date)")

    sids = [r[0] for r in cur.execute(
        "SELECT DISTINCT stock_id FROM daily_prices WHERE stock_id NOT LIKE '00%' ORDER BY stock_id")]
    print(f"標記 {len(sids)} 檔，date ≥ {since}（需未來 {_H} 根完整）")

    t0 = time.time()
    total = 0
    batch: list[tuple] = []
    for si, sid in enumerate(sids):
        rows = cur.execute(
            "SELECT date, high, low, close FROM daily_prices WHERE stock_id=? ORDER BY date",
            (sid,)).fetchall()
        n = len(rows)
        if n < _H + 2:
            continue
        dates = [r[0] for r in rows]
        highs = np.array([r[1] if r[1] is not None else np.nan for r in rows])
        lows = np.array([r[2] if r[2] is not None else np.nan for r in rows])
        closes = np.array([r[3] if r[3] is not None else np.nan for r in rows])
        # p 可標範圍：p+1+_H <= n-1
        for p in range(n - _H - 1):
            if dates[p] < since:
                continue
            entry = highs[p + 1]
            if np.isnan(entry) or entry <= 0:
                continue
            fhi = highs[p + 2: p + 2 + _H]
            flo = lows[p + 2: p + 2 + _H]
            if np.isnan(fhi).all():
                continue
            mfe = (np.nanmax(fhi) / entry - 1.0) * 100.0
            mae = (np.nanmin(flo) / entry - 1.0) * 100.0 if not np.isnan(flo).all() else None
            c30 = closes[p + 1 + _H]
            ret = (c30 / entry - 1.0) * 100.0 if not np.isnan(c30) else None
            batch.append((sid, dates[p], round(float(entry), 4), round(float(mfe), 2),
                          round(float(mae), 2) if mae is not None else None,
                          round(float(ret), 2) if ret is not None else None))
        if len(batch) >= 50_000:
            cur.executemany("INSERT OR REPLACE INTO forward_labels VALUES (?,?,?,?,?,?)", batch)
            con.commit()
            total += len(batch)
            batch = []
            print(f"  [{si+1}/{len(sids)}] 已寫 {total:,} 列  ({time.time()-t0:.0f}s)")
    if batch:
        cur.executemany("INSERT OR REPLACE INTO forward_labels VALUES (?,?,?,?,?,?)", batch)
        con.commit()
        total += len(batch)
    print(f"完成：寫入 {total:,} 列，{time.time()-t0:.0f}s")

    for row in cur.execute("""
        SELECT min(date), max(date), count(*), count(DISTINCT stock_id),
               round(avg(mfe30),2), round(avg(mae30),2),
               sum(mfe30 >= 10.0) * 100.0 / count(*)
        FROM forward_labels"""):
        print(f"檢核：{row[0]} ~ {row[1]}，{row[2]:,} 列 / {row[3]} 檔，"
              f"avg mfe30={row[4]}% avg mae30={row[5]}%，全市場摸+10%基率={row[6]:.1f}%")
    con.close()


if __name__ == "__main__":
    main()
