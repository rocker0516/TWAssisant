"""回補注意/處置名單（2020 起）＋ 動能假設驗證。

用法：
    cd backend
    python -m scripts.backfill_attention              # 回補 + 驗證報告
    python -m scripts.backfill_attention 2022         # 指定起年
    python -m scripts.backfill_attention analyze      # 只跑驗證（不抓）

驗證：對每筆「公告事件」，以公告日次一交易日收盤進場，計算 10/30 日
報酬與相對大盤超額報酬、勝率——檢驗「被列注意/處置高機率有上漲動能」。
"""

from __future__ import annotations

import sys
import time
from datetime import date, timedelta

from sqlalchemy import select

from app.sources import attention
from app.storage import models, repositories as repo
from app.storage.database import SessionLocal, init_db


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def backfill(start_year: int) -> None:
    with SessionLocal() as s:
        ids = set(s.execute(select(models.Stock.id)).scalars().all())
    repository = repo.AttentionRepository()
    today = date.today()

    cur = date(start_year, 1, 1)
    total = 0
    while cur <= today:
        nxt = (cur.replace(day=1) + timedelta(days=40)).replace(day=1)  # 下月 1 日
        end = min(nxt - timedelta(days=1), today)
        try:
            rows = attention.fetch_range(cur, end)
        except Exception as exc:  # 官方端點偶發 5xx，跳過該月可重跑
            _log(f"{cur:%Y-%m}：抓取失敗（{exc}），跳過")
            cur = nxt
            continue
        rows = [r for r in rows if r["stock_id"] in ids]  # 過濾權證/ETN
        n = 0
        if rows:
            with SessionLocal() as s:
                n = repository.upsert_many(s, rows)
                s.commit()
        total += n
        _log(f"{cur:%Y-%m} → {n} 筆")
        cur = nxt
        time.sleep(0.3)
    _log(f"回補完成，共 {total} 筆。")


def analyze() -> None:
    """列入後 10/30 日報酬 vs 大盤；分 notice/punish 統計。"""
    import pandas as pd

    with SessionLocal() as s:
        ev = pd.DataFrame(
            s.execute(select(models.AttentionListing.stock_id, models.AttentionListing.date,
                             models.AttentionListing.kind)).all(),
            columns=["stock_id", "date", "kind"],
        )
        px = pd.DataFrame(
            s.execute(select(models.DailyPrice.stock_id, models.DailyPrice.date,
                             models.DailyPrice.close)
                      .where(models.DailyPrice.close.is_not(None))).all(),
            columns=["stock_id", "date", "close"],
        )
        idx = pd.DataFrame(
            s.execute(select(models.MarketIndex.date, models.MarketIndex.close)
                      .where(models.MarketIndex.close.is_not(None))).all(),
            columns=["date", "close"],
        )
    if ev.empty:
        print("無事件資料，先跑回補。")
        return

    px = px.sort_values(["stock_id", "date"])
    # 每檔股票的日期序列 → 位置索引，向量化算 fwd return
    px["pos"] = px.groupby("stock_id").cumcount()
    key = px.set_index(["stock_id", "date"])["pos"]
    closes = {sid: g["close"].to_numpy() for sid, g in px.groupby("stock_id")}

    idx = idx.sort_values("date").reset_index(drop=True)
    idx_pos = {d: i for i, d in enumerate(idx["date"])}
    idx_close = idx["close"].to_numpy()

    def fwd(sid: str, d: date, horizon: int) -> tuple[float | None, float | None]:
        """(個股報酬%, 超額報酬% vs 大盤)。進場＝公告日次一交易日收盤。"""
        pos = key.get((sid, d))
        arr = closes.get(sid)
        if pos is None or arr is None:
            return None, None
        entry_i, exit_i = pos + 1, pos + 1 + horizon
        if exit_i >= len(arr):
            return None, None
        r = (arr[exit_i] / arr[entry_i] - 1) * 100
        # 大盤同窗（大盤歷史較短，缺資料就不算超額）
        ip = idx_pos.get(d)
        ex = None
        if ip is not None and ip + 1 + horizon < len(idx_close):
            ex = r - (idx_close[ip + 1 + horizon] / idx_close[ip + 1] - 1) * 100
        return round(r, 2), round(ex, 2) if ex is not None else None

    print(f"\n事件數：{len(ev)}（notice {len(ev[ev.kind=='notice'])} / punish {len(ev[ev.kind=='punish'])}）")
    print(f"事件期間：{ev['date'].min()} ~ {ev['date'].max()}\n")
    for kind in ("notice", "punish"):
        sub = ev[ev.kind == kind]
        for hz in (10, 30):
            rets, exs = [], []
            for _, r in sub.iterrows():
                ret, ex = fwd(r.stock_id, r.date, hz)
                if ret is not None:
                    rets.append(ret)
                if ex is not None:
                    exs.append(ex)
            if not rets:
                continue
            sr = pd.Series(rets)
            se = pd.Series(exs) if exs else None
            line = (f"{kind:<7} +{hz:>2}日  n={len(sr):>5}  勝率 {(sr>0).mean()*100:5.1f}%  "
                    f"平均 {sr.mean():+6.2f}%  中位 {sr.median():+6.2f}%")
            if se is not None and len(se) > 30:
                line += f"  | 超額(vs大盤) 平均 {se.mean():+6.2f}% 勝率 {(se>0).mean()*100:5.1f}% (n={len(se)})"
            print(line)
    print("\n備註：進場＝公告次日收盤；超額報酬僅在大盤指數歷史涵蓋時計算。")


def main(argv: list[str]) -> None:
    start_year = 2020
    do_fetch = True
    for a in argv[1:]:
        if a.isdigit():
            start_year = int(a)
        elif a == "analyze":
            do_fetch = False
    init_db()
    if do_fetch:
        backfill(start_year)
    analyze()


if __name__ == "__main__":
    main(sys.argv)
