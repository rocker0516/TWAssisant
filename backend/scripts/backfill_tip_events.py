"""回補 TIP 指數定審成分異動事件 ＋ ETF 成分效應事件研究。

用法：
    python -m scripts.backfill_tip_events              # 爬全部歷史 + 分析
    python -m scripts.backfill_tip_events analyze      # 只跑分析

分析（事件研究，皆以「公告次一交易日高點」進場錨、forward_labels 口徑）：
  納入 vs 刪除 vs 全市場基率：10 日碰 +10% 率、mfe10/mae10 均值；
  另算「公告日→生效日」的宣告期報酬（指數基金被迫買入的 front-run 窗口）。
"""

from __future__ import annotations

import sys
import time
from datetime import date

from sqlalchemy import select

from app.sources import tip_index
from app.storage import models, repositories as repo
from app.storage.database import SessionLocal, init_db


def _log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def backfill() -> None:
    with SessionLocal() as s:
        known_files = set(s.execute(select(models.EtfIndexEvent.file_id).distinct()).scalars().all())
        valid_ids = set(s.execute(select(models.Stock.id)).scalars().all())
    repository = repo.EtfIndexEventRepository()
    total = 0
    page = 1
    while True:
        rows = tip_index.list_notices(page)
        if not rows:
            break
        reviews = [r for r in rows if r["category"] == "定審結果"]
        _log(f"page {page}: {len(rows)} 則（定審結果 {len(reviews)} 則）")
        for r in reviews:
            if r["file_id"] in known_files:
                continue
            pdf = tip_index.fetch_pdf(r["file_id"])
            if pdf is None:
                _log(f"  file {r['file_id']} 非 PDF，略過")
                continue
            parsed = tip_index.parse_review(pdf)
            if parsed is None:
                _log(f"  file {r['file_id']} 格式不符，略過（{r['title'][:30]}）")
                continue
            recs = []
            for action, lst in (("add", parsed["adds"]), ("remove", parsed["dels"])):
                for code, nm in lst:
                    if code not in valid_ids:
                        continue  # 非現股（債券/外國成分等）
                    recs.append({
                        "file_id": r["file_id"], "stock_id": code, "action": action,
                        "index_name": parsed["index_name"] or r["title"][:80],
                        "stock_name": nm[:30],
                        "announce_date": r["file_date"],
                        "effective_date": parsed["effective_date"],
                    })
            if recs:
                with SessionLocal() as s:
                    n = repository.upsert_many(s, recs)
                    s.commit()
                total += n
                _log(f"  file {r['file_id']} → {n} 筆（{(parsed['index_name'] or '')[:24]}）")
            time.sleep(2.0)  # backend host 限流嚴，放慢
        page += 1
        time.sleep(0.3)
    _log(f"回補完成，共 {total} 筆事件列。")


def analyze() -> None:
    import numpy as np
    import pandas as pd
    import sqlite3

    from app.config import settings

    con = sqlite3.connect(str(settings.db_path))
    ev = pd.read_sql_query(
        "SELECT stock_id, action, announce_date, effective_date, index_name FROM etf_index_events", con)
    if ev.empty:
        print("無事件，先跑回補。")
        return
    lab = pd.read_sql_query(
        "SELECT stock_id, date, mfe10, mae10, mfe30 FROM forward_labels", con)
    px = pd.read_sql_query(
        "SELECT stock_id, date, close FROM daily_prices WHERE close IS NOT NULL", con)
    con.close()

    print(f"\n事件：{len(ev)} 列（add {len(ev[ev.action=='add'])} / remove {len(ev[ev.action=='remove'])}）"
          f"，{ev['announce_date'].min()} ~ {ev['announce_date'].max()}，"
          f"指數 {ev['index_name'].nunique()} 檔")

    lab_k = lab.set_index(["stock_id", "date"])
    px = px.sort_values(["stock_id", "date"])
    closes = {sid: (g["date"].tolist(), g["close"].to_numpy()) for sid, g in px.groupby("stock_id")}

    def anchor_metrics(sub: pd.DataFrame, on: str) -> None:
        got = sub.join(lab_k, on=["stock_id", on])
        got = got[got["mfe10"].notna()]
        if len(got) < 20:
            print(f"    {on}: 樣本不足（{len(got)}）")
            return
        print(f"    以{('公告日' if on=='announce_date' else '生效日')}為錨 n={len(got)}: "
              f"10日碰+10%={np.mean(got['mfe10']>=10)*100:.1f}%  "
              f"mfe10均={got['mfe10'].mean():+.2f}%  mae10均={got['mae10'].mean():+.2f}%  "
              f"30日碰+10%={np.mean(got['mfe30']>=10)*100:.1f}%")

    base = lab[lab["date"] >= "2024-01-01"]
    print(f"  全市場基率（2024+）：10日 {np.mean(base['mfe10']>=10)*100:.1f}% / 30日 {np.mean(base['mfe30']>=10)*100:.1f}%\n")
    for action in ("add", "remove"):
        sub = ev[ev.action == action]
        print(f"  ── {'納入' if action=='add' else '剔除'} ──")
        anchor_metrics(sub, "announce_date")
        anchor_metrics(sub, "effective_date")

    # 宣告期報酬：公告收盤 → 生效日收盤（指數基金被迫交易窗）
    def window_ret(row) -> float | None:
        c = closes.get(row.stock_id)
        if not c:
            return None
        dates, arr = c
        try:
            import bisect
            i0 = bisect.bisect_left(dates, row.announce_date)
            i1 = bisect.bisect_left(dates, row.effective_date)
            if i0 >= len(dates) or i1 >= len(dates) or i1 <= i0:
                return None
            return (arr[i1] / arr[i0] - 1) * 100
        except Exception:
            return None

    ev2 = ev.dropna(subset=["effective_date"])
    for action in ("add", "remove"):
        rets = [r for r in (window_ret(row) for row in ev2[ev2.action == action].itertuples()) if r is not None]
        if rets:
            print(f"\n  宣告期（公告→生效）{'納入' if action=='add' else '剔除'}: n={len(rets)} "
                  f"平均 {np.mean(rets):+.2f}%  中位 {np.median(rets):+.2f}%  勝率 {np.mean(np.array(rets)>0)*100:.0f}%")


def main() -> None:
    init_db()
    if "analyze" not in sys.argv[1:]:
        backfill()
    analyze()


if __name__ == "__main__":
    main()
