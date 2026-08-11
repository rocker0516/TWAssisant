"""Pipeline steps。P0 只有 FetchStep（抓資料落庫）。

後續階段在此新增 IndicatorStep / SectorStep / NewsStep / ScoringStep /
ExitStep / NotifyStep，再加進 run.py 的 step 清單。
（LLM 翻白話已改端點首讀懶生成 llm/lazy.py + news_digest.py，不再是 pipeline step。）
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select

from ..engines.corners import CornerEngine
from ..engines.exit_engine import ExitEngine
from ..engines.indicators import IndicatorEngine
from ..engines.news_engine import NewsEngine
from ..engines.poppability import PoppabilityEfficacyEngine
from ..engines.scoring import ScoringEngine
from ..engines.sector_engine import SectorEngine
from ..notify import build_daily_message, send_discord
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
        ("short_lending", "chip", "ShortLendingRepository", "fetch_short_lending", 90),
        ("day_trading", "chip", "DayTradingRepository", "fetch_day_trading", 90),
        ("valuation", "fundamental", "ValuationRepository", "fetch_valuation", 90),
    ]
    # 快照來源（回最新期，日期參數忽略，靠 upsert 去重）
    # holding：TDCC 集保股權分散僅回最新一週，靠每週 upsert 累積歷史。
    # financials：已改走 MOPS 累計制差分（回最近 2 個已結束季度的「單季」值），
    #             歷史由 scripts.backfill_fundamentals 回補。
    _WINDOW = [
        ("revenue", "fundamental", "RevenueMonthlyRepository", "fetch_revenue_monthly", 1),
        ("financials", "fundamental", "FinancialQuarterRepository", "fetch_financials", 1),
        ("holding", "holding", "ShareholdingRepository", "fetch_holding_distribution", 1),
        # insider：董監持股月快照（t187ap11），PK=(stock_id,year,month) 靠 upsert 累積
        ("insider", "fundamental", "InsiderHoldingRepository", "fetch_insider_holdings", 1),
    ]
    # 市場級資料（無 stock_id，PK=date，不過濾股號）：全市場三大法人總表 + 加權指數。
    # (key, source_name, repo_cls, method, lookback)
    _MARKET = [
        ("inst_market", "twse", "InstitutionalMarketTotalRepository", "fetch_institutional_market_total", 90),
        ("market_index", "twse", "MarketIndexRepository", "fetch_index", 150),
        ("derivatives", "taifex", "MarketDerivativesRepository", "fetch_market_derivatives", 90),
    ]

    def run(self, ctx: PipelineContext) -> dict:
        session = ctx.session
        td = ctx.trading_date
        results: dict[str, dict] = {}

        # 1) 主檔（universe）— 先抓，後續落庫要靠它過濾未知股號 + FK
        results["universe"] = self._fetch_universe(session)

        known_ids = self._known_ids(session)
        ctx.shared["stock_ids"] = sorted(known_ids)

        # 1b) ETF 身分資料（追蹤指數/類型/含國外/發行單位數，TWSE 全快照）
        results["etf_profile"] = self._fetch_etf_profiles(session, known_ids)

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

        # 4) 市場級增量抓（無 stock_id，不過濾股號）
        for key, source_name, repo_cls, method, lookback in self._MARKET:
            results[key] = self._fetch_market_dataset(
                session, source_name, repo_cls, method, td, lookback,
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

    # ── ETF 身分資料（TWSE 全快照，僅落已知股號）──

    def _fetch_etf_profiles(self, session, known_ids: set[str]) -> dict:
        try:
            src = registry.get_source("twse")
            df = src.fetch_etf_profiles()
        except SourceError as exc:
            return {"status": "error", "reason": exc.reason}
        if df.empty:
            return {"status": "empty"}
        df = df[df["stock_id"].astype(str).isin(known_ids)]
        n = repo.EtfProfileRepository().upsert_many(session, _records(df))
        session.flush()
        return {"status": "ok", "rows": n}

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

    def _fetch_market_dataset(
        self, session, source_name, repo_cls_name, method, td, lookback: int,
    ) -> dict:
        """市場級資料集（PK=date，無 stock_id）增量抓。起點＝max_date+1（冷啟回補 lookback）。"""
        try:
            src = registry.get_source(source_name)
            repository = getattr(repo, repo_cls_name)()
            last = repository.max_date(session)
            start = (last + timedelta(days=1)) if last else (td - timedelta(days=lookback))
            if start > td:
                return {"status": "ok", "rows": 0, "note": "up_to_date"}
            df = getattr(src, method)(start, td, None)
            if df.empty:
                return {"status": "empty", "from": start.isoformat(), "to": td.isoformat()}
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


class SectorStep(PipelineStep):
    """類股強弱/方向/輪動 → sector_daily（P3）。需在 Scoring 前。"""

    name = "sector"
    required = True

    def run(self, ctx: PipelineContext) -> dict:
        return SectorEngine().run(ctx.session, ctx.trading_date)


class NewsStep(PipelineStep):
    """重訊/事件分類 → events（P4，非必要）。掛了用既有事件不影響選股。"""

    name = "news"
    required = False

    def run(self, ctx: PipelineContext) -> dict:
        return NewsEngine().run(ctx.session, ctx.trading_date)


class TargetPriceStep(PipelineStep):
    """FactSet 共識目標價（鉅亨 tw_forecast）。首次自動回補 180 天，之後增量。非必要。"""

    name = "target_price"
    required = False

    def run(self, ctx: PipelineContext) -> dict:
        from ..sources.cnyes_forecast import CnyesForecastSource

        session = ctx.session
        known = set(
            session.execute(
                select(models.TargetPrice.news_id).where(models.TargetPrice.news_id.isnot(None))
            ).scalars().all()
        )
        min_date = ctx.trading_date - timedelta(days=180)
        src = CnyesForecastSource()
        try:
            rows = src.fetch_target_prices(known, min_date)
        except SourceError as exc:
            return {"ok": False, "reason": exc.reason}
        finally:
            src.close()
        # 只留 universe 內股票（FK 保護）；同日同股取 news_id 較大者
        valid_ids = set(session.execute(select(models.Stock.id)).scalars().all())
        best: dict[tuple[str, object], dict] = {}
        for r in rows:
            if r["stock_id"] not in valid_ids:
                continue
            key = (r["stock_id"], r["date"])
            if key not in best or (r.get("news_id") or 0) > (best[key].get("news_id") or 0):
                best[key] = r
        n = repo.TargetPriceRepository().upsert_many(session, list(best.values()))
        return {"ok": True, "rows": n}


class ScoringStep(PipelineStep):
    """雙軌評分 → scores（P1，含類股修正）。"""

    name = "scoring"
    required = True

    def run(self, ctx: PipelineContext) -> dict:
        return ScoringEngine().run(ctx.session, ctx.trading_date)


class ExitStep(PipelineStep):
    """持股出場評估：日更持有最高價（P2）。"""

    name = "exit"
    required = True

    def run(self, ctx: PipelineContext) -> dict:
        return ExitEngine().run(ctx.session, ctx.trading_date)


class NotifyStep(PipelineStep):
    """Discord 推播持股提醒 + 推薦檔數（P2，非必要）。"""

    name = "notify"
    required = False

    def run(self, ctx: PipelineContext) -> dict:
        msg = build_daily_message(ctx.session, ctx.trading_date)
        if msg is None:
            return {"status": "ok", "sent": False, "note": "無可報內容"}
        sent = send_discord(msg)
        return {"status": "ok", "sent": sent, "note": None if sent else "未設定 webhook"}


class CornerStep(PipelineStep):
    """高確信角落影子軌（實驗）→ corner_signals。純標籤層，掛了不影響主流程。"""

    name = "corners"
    required = False

    def run(self, ctx: PipelineContext) -> dict:
        return CornerEngine().run(ctx.session, ctx.trading_date)


class PoppableEfficacyStep(PipelineStep):
    """會噴清單成效回測 → Setting['poppable_efficacy']（非必要、較重 ~分鐘級）。

    放在最後：純歷史回測、不影響當日推薦/通知，掛了不擾動主流程（白天讀舊快取）。
    """

    name = "poppable_efficacy"
    required = False

    def run(self, ctx: PipelineContext) -> dict:
        return PoppabilityEfficacyEngine().run(ctx.session, ctx.trading_date)
