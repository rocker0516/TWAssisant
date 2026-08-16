"""回補 TIP 指數定審成分事件 ＋ 事件研究（納入/剔除效應）。

用法：
    python -m scripts.backfill_etf_events            # 抓取+落庫+分析
    python -m scripts.backfill_etf_events analyze    # 只分析（不抓）

事件研究口徑（與判官一致）：
  錨1 公告日：公告次一交易日高點進場 → 10 日內碰 ±10%、第 10 日報酬（超前交易窗）
  錨2 生效日：生效前一日進場 → 生效後 5/10 日報酬（ETF 被動買盤實現窗）
  對照＝同月全市場基率（forward_labels）。
"""

from __future__ import annotations

import sys
import time
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import select

from app.sources import tip_index
from app.storage import models, repositories as repo
from app.storage.database import SessionLocal, init_db


def _log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def backfill() -> None:
    """斷點續傳：每輪抓到撞牆即落庫；撞牆→睡 25 分鐘再續，最多 12 輪。"""
    with SessionLocal() as s:
        ids = set(s.execute(select(models.Stock.id)).scalars().all())
    for round_ in range(12):
        events, wall = tip_index.fetch_events_resumable(log=_log)
        recs = [e for e in events if e["stock_id"] in ids]
        if recs:
            with SessionLocal() as s:
                n = repo.IndexEventRepository().upsert_many(s, recs)
                s.commit()
            _log(f"第 {round_+1} 輪落庫 {n} 筆")
        if not wall:
            _log("全部通知處理完畢")
            return
        _log("配額牆：睡 25 分鐘後續跑…")
        time.sleep(25 * 60)


def analyze() -> None:
    with SessionLocal() as s:
        ev = pd.DataFrame(s.execute(
            select(models.IndexConstituentEvent.stock_id, models.IndexConstituentEvent.action,
                   models.IndexConstituentEvent.announce_date, models.IndexConstituentEvent.effective_date,
                   models.IndexConstituentEvent.index_name)
        ).all(), columns=["stock_id", "action", "announce", "effective", "index_name"])
        px = pd.DataFrame(s.execute(
            select(models.DailyPrice.stock_id, models.DailyPrice.date, models.DailyPrice.high,
                   models.DailyPrice.low, models.DailyPrice.close)
            .where(models.DailyPrice.close.is_not(None))
        ).all(), columns=["stock_id", "date", "high", "low", "close"])
        base = pd.DataFrame(s.execute(select(
            models.AttentionListing.stock_id).limit(0)).all())  # placeholder
        import sqlite3  # 全市場基率（10日碰+10%）逐月
        from app.config import settings
        con = sqlite3.connect(str(settings.db_path))
        baser = pd.read_sql_query(
            "SELECT substr(date,1,7) ym, AVG(mfe10 >= 10.0)*100 hit FROM forward_labels "
            "WHERE mfe10 IS NOT NULL GROUP BY ym", con)
        con.close()
    base_map = dict(zip(baser["ym"], baser["hit"]))

    if ev.empty:
        print("無事件")
        return
    print(f"\n事件：{len(ev)} 筆（add {len(ev[ev.action=='add'])} / remove {len(ev[ev.action=='remove'])}）"
          f"，{ev['announce'].min()} ~ {ev['announce'].max()}，涵蓋指數 {ev['index_name'].nunique()} 檔")

    px = px.sort_values(["stock_id", "date"])
    pos_map = {}
    arrs = {}
    for sid, g in px.groupby("stock_id"):
        arrs[sid] = (g["date"].tolist(), g["high"].to_numpy(float),
                     g["low"].to_numpy(float), g["close"].to_numpy(float))
        pos_map[sid] = {d: i for i, d in enumerate(g["date"])}

    from bisect import bisect_left

    def fwd_from(sid: str, d0: date, horizon: int, anchor: str):
        """anchor='next_high'（公告次日高）或 'close'（該日收盤）。回 (碰+10%, 碰-10%, ret, 超額基率鍵)。"""
        a = arrs.get(sid)
        if a is None:
            return None
        dates, hi, lo, cl = a
        i = bisect_left(dates, d0)
        if i >= len(dates):
            return None
        if anchor == "next_high":
            e = i + 1 if dates[i] == d0 else i
            if e >= len(dates) or not np.isfinite(hi[e]) or hi[e] <= 0:
                return None
            entry = hi[e]
            start = e + 1
        else:
            if dates[i] != d0:
                if i == 0:
                    return None
                i -= 1
            entry = cl[i]
            if not np.isfinite(entry) or entry <= 0:
                return None
            start = i + 1
        end = start + horizon
        if end > len(dates):
            return None
        seg_h, seg_l, seg_c = hi[start:end], lo[start:end], cl[start:end]
        if not np.isfinite(seg_h).any():
            return None
        up = np.nanmax(seg_h) / entry - 1 >= 0.10
        dn = np.nanmin(seg_l) / entry - 1 <= -0.10
        ret = (seg_c[-1] / entry - 1) * 100 if np.isfinite(seg_c[-1]) else None
        return up, dn, ret

    def report(label: str, sub: pd.DataFrame, date_col: str, anchor: str, horizon: int):
        ups, dns, rets, liftbase = [], [], [], []
        for _, r in sub.iterrows():
            d0 = r[date_col]
            if d0 is None:
                continue
            m = fwd_from(r.stock_id, d0, horizon, anchor)
            if m is None:
                continue
            ups.append(m[0]); dns.append(m[1])
            if m[2] is not None: rets.append(m[2])
            liftbase.append(base_map.get(str(d0)[:7]))
        n = len(ups)
        if n < 20:
            print(f"  {label:<34} n={n}（樣本太少）")
            return
        bases = [b for b in liftbase if b is not None]
        base_avg = sum(bases) / len(bases) if bases else None
        up_r = sum(ups) / n * 100
        line = (f"  {label:<34} n={n:>4}  碰+10%={up_r:5.1f}%"
                f"{f'（基率{base_avg:.1f}%, lift{up_r-base_avg:+.1f}pp）' if base_avg else ''}"
                f"  碰-10%={sum(dns)/n*100:5.1f}%  均報酬={np.mean(rets):+5.2f}%")
        print(line)

    for act, name in (("add", "納入"), ("remove", "剔除")):
        sub = ev[ev.action == act]
        print(f"\n### {name}事件")
        report(f"{name}｜公告日錨（次日高，10日窗）", sub, "announce", "next_high", 10)
        report(f"{name}｜生效日錨（前收，生效後5日）", sub, "effective", "close", 5)
        report(f"{name}｜生效日錨（前收，生效後10日）", sub, "effective", "close", 10)


def main(argv: list[str]) -> None:
    init_db()
    if "analyze" not in argv[1:]:
        backfill()
    analyze()


if __name__ == "__main__":
    main(sys.argv)
