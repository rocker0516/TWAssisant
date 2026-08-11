"""資料存取層（架構⑥：Repository = 純 CRUD）。

BaseRepository 提供冪等 upsert（結果覆寫）+ 增量查詢用的 max_date，
是「整條 pipeline 冪等 → 可重跑/補跑」的關鍵。子類只綁 model。
"""

from __future__ import annotations

from datetime import date as date_
from typing import Generic, TypeVar

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from . import models
from .database import Base

M = TypeVar("M", bound=Base)

# SQLite 單一語句綁定變數上限（≥3.32 為 32766）。留安全邊際，分批 upsert。
_MAX_SQL_VARS = 20000


class BaseRepository(Generic[M]):
    model: type[M]

    def __init__(self, model: type[M] | None = None) -> None:
        if model is not None:
            self.model = model
        self._pk_cols = [c.name for c in self.model.__table__.primary_key.columns]

    def upsert_many(self, session: Session, rows: list[dict]) -> int:
        """以 PK 衝突覆寫；非 PK 欄位全部更新。自動分批避開 SQLite 變數上限。"""
        if not rows:
            return 0
        table = self.model.__table__
        ncols = len(table.columns)
        chunk = max(1, _MAX_SQL_VARS // ncols)
        update_cols_names = [c.name for c in table.columns if c.name not in self._pk_cols]

        total = 0
        for i in range(0, len(rows), chunk):
            batch = rows[i : i + chunk]
            stmt = sqlite_insert(table).values(batch)
            if update_cols_names:
                stmt = stmt.on_conflict_do_update(
                    index_elements=self._pk_cols,
                    set_={name: getattr(stmt.excluded, name) for name in update_cols_names},
                )
            else:
                stmt = stmt.on_conflict_do_nothing(index_elements=self._pk_cols)
            session.execute(stmt)
            total += len(batch)
        return total

    def max_date(self, session: Session, stock_id: str | None = None) -> date_ | None:
        """此表已有的最新日期（增量抓的起點）。需 model 有 date 欄位。"""
        if not hasattr(self.model, "date"):
            raise AttributeError(f"{self.model.__name__} 無 date 欄位，不支援 max_date")
        stmt = select(func.max(self.model.date))
        if stock_id is not None and hasattr(self.model, "stock_id"):
            stmt = stmt.where(self.model.stock_id == stock_id)
        return session.execute(stmt).scalar_one_or_none()

    def count(self, session: Session) -> int:
        return session.execute(select(func.count()).select_from(self.model)).scalar_one()


# ── 具體 repo（P0 FetchStep 用到的）──

class StockRepository(BaseRepository[models.Stock]):
    model = models.Stock


class SectorRepository(BaseRepository[models.Sector]):
    model = models.Sector

    def name_to_id(self, session: Session) -> dict[str, int]:
        rows = session.execute(select(models.Sector.name, models.Sector.id)).all()
        return {name: sid for name, sid in rows}

    def ensure(self, session: Session, names: list[str]) -> dict[str, int]:
        """確保類股存在，回 name→id。"""
        existing = self.name_to_id(session)
        new = [{"name": n} for n in names if n and n not in existing]
        if new:
            self.upsert_many(session, new)
            session.flush()
            existing = self.name_to_id(session)
        return existing


class DailyPriceRepository(BaseRepository[models.DailyPrice]):
    model = models.DailyPrice


class InstitutionalRepository(BaseRepository[models.Institutional]):
    model = models.Institutional


class MarginRepository(BaseRepository[models.Margin]):
    model = models.Margin


class ShareholdingRepository(BaseRepository[models.ShareholdingDistribution]):
    model = models.ShareholdingDistribution


class ShortLendingRepository(BaseRepository[models.ShortLending]):
    model = models.ShortLending


class DayTradingRepository(BaseRepository[models.DayTrading]):
    model = models.DayTrading


class InsiderHoldingRepository(BaseRepository[models.InsiderHolding]):
    model = models.InsiderHolding


class MarketDerivativesRepository(BaseRepository[models.MarketDerivatives]):
    model = models.MarketDerivatives


class InstitutionalMarketTotalRepository(BaseRepository[models.InstitutionalMarketTotal]):
    model = models.InstitutionalMarketTotal


class MarketIndexRepository(BaseRepository[models.MarketIndex]):
    model = models.MarketIndex


class RevenueMonthlyRepository(BaseRepository[models.RevenueMonthly]):
    model = models.RevenueMonthly


class FinancialQuarterRepository(BaseRepository[models.FinancialQuarter]):
    model = models.FinancialQuarter


class ValuationRepository(BaseRepository[models.Valuation]):
    model = models.Valuation


class TargetPriceRepository(BaseRepository[models.TargetPrice]):
    model = models.TargetPrice


class EtfProfileRepository(BaseRepository[models.EtfProfile]):
    model = models.EtfProfile


class PipelineRunRepository(BaseRepository[models.PipelineRun]):
    model = models.PipelineRun

    def has_success(self, session: Session, trading_date: date_) -> bool:
        stmt = select(func.count()).select_from(models.PipelineRun).where(
            models.PipelineRun.trading_date == trading_date,
            models.PipelineRun.status == "success",
        )
        return session.execute(stmt).scalar_one() > 0
