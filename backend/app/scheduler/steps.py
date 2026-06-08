"""Pipeline steps。P0 只有 FetchStep（抓資料落庫）。

後續階段在此新增 IndicatorStep / SectorStep / NewsStep / ScoringStep /
ExitStep / LLMBatchStep / NotifyStep，再加進 run.py 的 step 清單。
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select

from ..engines.indicators import IndicatorEngine
from ..engines.scoring import ScoringEngine
from ..sources import registry
from ..sources.base import SourceError
from ..storage import models, repositories as repo
from .pipeline import PipelineContext, PipelineStep


def _records(df: pd.DataFrame) -> list[dict]:
    """DataFrame → upsert 用 dicts，pandas NA/NaN 轉成 None（SQLite 友善）。"""
    if df.empty:
        return []
    clean = df.astype(object).where(pd.notna(df), None)
    return clean.to_dict("records")


class FetchStep(PipelineStep):
    """抓全市場盤後資料落庫。各來源各自 try，單一來源失敗不中斷整步。"""

    name = "fetch"
    required = True

    # 增量抓（用各表 max_date 當起點）；冷啟動回補天數。
    # P0 範圍：全市場日K + 三大法人 + 融資券（皆 TWSE 官方免費全市場）。
    # 估值/月營收/季財報屬長線軌（P1）才需要，屆時加進此清單即可。
    _INCREMENTAL = [
        ("price", "price", "DailyPriceRepository", "fetch_prices", 150),
        ("institutional", "chip", "InstitutionalRepository", "fetch_institutional", 90),
        ("margin", "chip", "MarginRepository", "fetch_margin", 90),
        ("valuation", "fundamental", "ValuationRepository", "fetch_valuation", 90),
    ]
    # 快照來源（openapi 回最新月/季，日期參數忽略，靠 upsert 去重）
    _WINDOW = [
        ("revenue", "fundamental", "RevenueMonthlyRepository", "fetch_revenue_monthly", 1),
        ("financials", "fundamental", "FinancialQuarterRepository", "fetch_financials", 1),
    ]

    def run(self, ctx: PipelineContext) -> dict:
        session = ctx.session
        td = ctx.trading_date
        results: dict[str, dict] = {}

        # 1) 主檔（universe）— 先抓，後續落庫要靠它過濾未知股號 + FK
        results["universe"] = self._fetch_universe(session)

        known_ids = self._known_ids(session)
        ctx.shared["stock_ids"] = sorted(known_ids)

        # 2) 增量抓
        for key, capability, repo_cls, method, lookback in self._INCREMENTAL:
            results[key] = self._fetch_dataset(
                session, key, capability, repo_cls, method, td, known_ids,
                incremental=True, lookback=lookback,
            )

        # 3) 固定視窗抓
        for key, capability, repo_cls, method, lookback in self._WINDOW:
            results[key] = self._fetch_dataset(
                session, key, capability, repo_cls, method, td, known_ids,
                incremental=False, lookback=lookback,
            )

        ok = sum(1 for r in results.values() if r.get("status") == "ok")
        return {"datasets": results, "ok_count": ok, "total": len(results)}

    # ── 主檔 ──

    def _fetch_universe(self, session) -> dict:
        try:
            src = registry.provider("universe")
            df = src.fetch_universe()
        except SourceError as exc:
            return {"status": "error", "reason": exc.reason}
        if df.empty:
            return {"status": "empty"}

        sector_repo = repo.SectorRepository()
        names = sorted({n for n in df["sector_name"].dropna().tolist() if n})
        name_to_id = sector_repo.ensure(session, names)

        rows = []
        for rec in _records(df):
            rows.append(
                {
                    "id": rec["id"],
                    "name": rec["name"],
                    "sector_id": name_to_id.get(rec.get("sector_name")),
                    "market": rec.get("market"),
                    "industry_category": rec.get("industry_category"),
                    "is_etf": bool(rec.get("is_etf")),
                    "listed_date": rec.get("listed_date"),
                }
            )
        n = repo.StockRepository().upsert_many(session, rows)
        session.flush()
        return {"status": "ok", "rows": n, "sectors": len(name_to_id)}

    def _known_ids(self, session) -> set[str]:
        return set(session.execute(select(models.Stock.id)).scalars().all())

    # ── 通用資料集抓取 ──

    def _fetch_dataset(
        self, session, key, capability, repo_cls_name, method, td, known_ids,
        *, incremental: bool, lookback: int,
    ) -> dict:
        try:
            src = registry.provider(capability)
            repository = getattr(repo, repo_cls_name)()

            if incremental:
                last = repository.max_date(session)
                start = (last + timedelta(days=1)) if last else (td - timedelta(days=lookback))
                if start > td:
                    return {"status": "ok", "rows": 0, "note": "up_to_date"}
            else:
                start = td - timedelta(days=lookback)

            df = getattr(src, method)(start, td, None)
            if df.empty:
                return {"status": "empty", "from": start.isoformat(), "to": td.isoformat()}

            df = df[df["stock_id"].astype(str).isin(known_ids)]
            n = repository.upsert_many(session, _records(df))
            session.flush()
            return {"status": "ok", "rows": n, "from": start.isoformat(), "to": td.isoformat()}
        except SourceError as exc:
            return {"status": "error", "reason": exc.reason}


class IndicatorStep(PipelineStep):
    """daily_prices → indicators（P1）。"""

    name = "indicator"
    required = True

    def run(self, ctx: PipelineContext) -> dict:
        return IndicatorEngine().run(ctx.session, ctx.trading_date)


class ScoringStep(PipelineStep):
    """雙軌評分 → scores（P1）。"""

    name = "scoring"
    required = True

    def run(self, ctx: PipelineContext) -> dict:
        return ScoringEngine().run(ctx.session, ctx.trading_date)
