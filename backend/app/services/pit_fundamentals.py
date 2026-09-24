"""基本面 Point-in-Time 可得性 —— 單一事實來源（Level 1 推薦軌優先使用）。

背景：`revenue_monthly` / `financials_quarterly` 的 PK 只有「所屬期間」，沒有公告日。
歷史回補來自 MOPS 彙總表，拿不到逐公司實際公告時間，因此可得日只能用上界近似。
本模組把近似規則集中在一處，杜絕研究端/線上端各寫一套造成定義漂移
（同 `app/engines/corner_defs.py` 的精神）。

可得日規則（皆為「實際公告時間的上界」，寧可晚、不可早 → 不會偷看未來）：

1. 月營收：M 月數字自 M+1 月 10 日（法定申報截止）起視為可得。
2. 季財報：一般業 Q1→5/15、Q2→8/14、Q3→11/14、Q4(年報)→次年 3/31。
   金融業（industry_category ∈ 金融保險/金融業）申報期限較晚：
   Q1→5/30、Q2→8/31、Q3→11/29（取金控/銀行/保險各子業中最晚者，保守不搶跑）。
   既有 `app/engines/scoring.py` 對金融股用一般期限，Q2 有最多 ~17 天的搶跑，
   本模組修正之；線上長線軌是否改用本模組屬行為變更，另行決策。
3. 若 `fundamental_first_seen` 側表有「首次入庫日」，取
   avail = min(法定期限, 首次入庫日)。兩者都 ≥ 實際公告日，取 min 仍是安全上界，
   而每日排程當天抓到的新資料可以把可得日從法定期限提前到實際入庫日。
   歷史一次性回補的列 first_seen 是回補當天（遠晚於期限），min() 自動退回法定規則。

已知限制（文件化，不假裝沒有）：
- Revision leakage：主表 upsert 覆寫，存的是「最新版數字」；月營收自結數與
  後續修正無法區分。對排序模型影響小，但不可宣稱完全無 revision bias。
- `financials_quarterly.roe` 整欄 NULL（來源未提供），不可作為特徵。
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.storage import models, repositories as repo

# 季報法定申報期限：(年位移, 月, 日)
GENERAL_DEADLINES: dict[int, tuple[int, int, int]] = {
    1: (0, 5, 15), 2: (0, 8, 14), 3: (0, 11, 14), 4: (1, 3, 31),
}
FINANCIAL_DEADLINES: dict[int, tuple[int, int, int]] = {
    1: (0, 5, 30), 2: (0, 8, 31), 3: (0, 11, 29), 4: (1, 3, 31),
}
# stocks.industry_category 的金融類別值（上市「金融保險」、上櫃「金融業」）
FINANCIAL_CATEGORIES = frozenset({"金融保險", "金融業"})


def revenue_avail_date(year: int, month: int) -> date:
    """M 月營收的法定可得日：M+1 月 10 日。"""
    if month == 12:
        return date(year + 1, 1, 10)
    return date(year, month + 1, 10)


def financials_avail_date(year: int, quarter: int, *, financial: bool = False) -> date:
    """Q 季財報的法定可得日（金融業期限較晚）。"""
    dy, m, d = (FINANCIAL_DEADLINES if financial else GENERAL_DEADLINES)[quarter]
    return date(year + dy, m, d)


def financial_stock_ids(session: Session) -> set[str]:
    """金融業股號集合（季報適用較晚的申報期限）。"""
    rows = session.execute(
        select(models.Stock.id).where(
            models.Stock.industry_category.in_(FINANCIAL_CATEGORIES))
    ).scalars().all()
    return set(rows)


def _first_seen_map(session: Session, kind: str) -> dict[tuple[str, int, int], date]:
    rows = session.execute(
        select(
            models.FundamentalFirstSeen.stock_id,
            models.FundamentalFirstSeen.year,
            models.FundamentalFirstSeen.period,
            models.FundamentalFirstSeen.first_seen,
        ).where(models.FundamentalFirstSeen.kind == kind)
    ).all()
    return {(sid, y, p): fs for sid, y, p, fs in rows}


def _apply_first_seen(df: pd.DataFrame, fs: dict, period_col: str) -> pd.DataFrame:
    """avail = min(法定期限, 首次入庫日)。無側表紀錄者維持法定期限。"""
    if fs:
        keys = list(zip(df["stock_id"], df["year"].astype(int), df[period_col].astype(int)))
        seen = pd.Series([fs.get(k) for k in keys], index=df.index)
        seen = pd.to_datetime(seen, errors="coerce")
        df["avail"] = df["avail"].where(seen.isna() | (df["avail"] <= seen), seen)
    return df


def load_revenue_pit(session: Session) -> pd.DataFrame:
    """月營收全歷史＋`avail`（pd.Timestamp）欄。呼叫端以 avail <= T 篩選。"""
    rows = session.execute(
        select(
            models.RevenueMonthly.stock_id, models.RevenueMonthly.year,
            models.RevenueMonthly.month, models.RevenueMonthly.revenue,
            models.RevenueMonthly.yoy, models.RevenueMonthly.mom,
        ).order_by(models.RevenueMonthly.stock_id,
                   models.RevenueMonthly.year, models.RevenueMonthly.month)
    ).all()
    df = pd.DataFrame(rows, columns=["stock_id", "year", "month", "revenue", "yoy", "mom"])
    if df.empty:
        return df.assign(avail=pd.Series(dtype="datetime64[ns]"))
    next_m = df["month"] % 12 + 1
    next_y = df["year"] + (df["month"] == 12).astype(int)
    df["avail"] = pd.to_datetime(dict(year=next_y, month=next_m, day=10))
    return _apply_first_seen(df, _first_seen_map(session, "rev"), "month")


def load_financials_pit(session: Session) -> pd.DataFrame:
    """季財報（單季化）全歷史＋`avail` 欄，金融業用較晚期限。roe 不載入（整欄 NULL）。"""
    rows = session.execute(
        select(
            models.FinancialQuarter.stock_id, models.FinancialQuarter.year,
            models.FinancialQuarter.quarter, models.FinancialQuarter.eps,
            models.FinancialQuarter.revenue, models.FinancialQuarter.gross_margin,
            models.FinancialQuarter.op_margin, models.FinancialQuarter.net_margin,
        ).order_by(models.FinancialQuarter.stock_id,
                   models.FinancialQuarter.year, models.FinancialQuarter.quarter)
    ).all()
    cols = ["stock_id", "year", "quarter", "eps", "revenue",
            "gross_margin", "op_margin", "net_margin"]
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df.assign(avail=pd.Series(dtype="datetime64[ns]"))
    fin_ids = financial_stock_ids(session)
    dl = df.apply(
        lambda r: (FINANCIAL_DEADLINES if r["stock_id"] in fin_ids
                   else GENERAL_DEADLINES)[int(r["quarter"])], axis=1)
    df["avail"] = pd.to_datetime(dict(
        year=df["year"] + dl.str[0], month=dl.str[1], day=dl.str[2]))
    return _apply_first_seen(df, _first_seen_map(session, "fin"), "quarter")


def record_first_seen(session: Session, today: date | None = None) -> dict:
    """把主表出現、但側表還沒有的 (kind, stock, 期間) 補記 first_seen=today。

    append-only（insert-ignore）：已寫下的首次入庫日永不改寫，歷史回補重跑也一樣。
    每日排程在基本面抓取後呼叫；一次性 baseline 也走同一函式。
    """
    today = today or date.today()
    existing = {
        (k, s, y, p)
        for k, s, y, p in session.execute(
            select(
                models.FundamentalFirstSeen.kind,
                models.FundamentalFirstSeen.stock_id,
                models.FundamentalFirstSeen.year,
                models.FundamentalFirstSeen.period,
            )
        ).all()
    }
    rows: list[dict] = []
    specs = [
        ("rev", models.RevenueMonthly, models.RevenueMonthly.month),
        ("fin", models.FinancialQuarter, models.FinancialQuarter.quarter),
    ]
    for kind, model_, period_col in specs:
        keys = session.execute(
            select(model_.stock_id, model_.year, period_col)).all()
        for sid, y, p in keys:
            if (kind, sid, int(y), int(p)) not in existing:
                rows.append({
                    "kind": kind, "stock_id": sid, "year": int(y),
                    "period": int(p), "first_seen": today,
                })
    n = repo.FundamentalFirstSeenRepository().insert_ignore_many(session, rows)
    return {"new": n, "total_existing": len(existing)}
