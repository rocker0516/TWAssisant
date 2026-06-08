"""ScoringEngine（架構③）：批次載入 → 每檔建 StockContext → 跑雙軌 → 落 scores。

規則不各自查 DB：此處一次把 price/indicator/法人 載進記憶體、依股號切片建 context。
每檔每軌產一列（passed 標記是否進推薦）。配分/門檻由 settings 'scoring' 覆寫。
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..storage import models
from ..storage.repositories import BaseRepository
from .base import BaseEngine
from .context import StockContext
from .tracks import LongTrack, WaveTrack


def _load_groups(session: Session, model, cols: list[str], td: date) -> dict[str, pd.DataFrame]:
    """載入某表 date<=td 的資料，依 stock_id 分組（升冪）。"""
    stmt = (
        select(model)
        .where(model.date <= td)
        .order_by(model.stock_id, model.date)
    )
    rows = session.execute(stmt).scalars().all()
    if not rows:
        return {}
    df = pd.DataFrame([{c: getattr(r, c) for c in cols} for r in rows])
    return {sid: g.reset_index(drop=True) for sid, g in df.groupby("stock_id", sort=False)}


def _load_latest(session: Session, model, cols: list[str], order_cols: list) -> dict[str, pd.Series]:
    """每檔最新一筆（基本面：valuation/revenue/financials），回 stock_id→Series。"""
    rows = session.execute(select(model).order_by(*order_cols)).scalars().all()
    out: dict[str, pd.Series] = {}
    for r in rows:  # 升冪 → 後者覆寫，最終留最新
        out[r.stock_id] = pd.Series({c: getattr(r, c) for c in cols})
    return out


class ScoringEngine(BaseEngine):
    name = "scoring"

    def __init__(self) -> None:
        self.tracks = [WaveTrack(), LongTrack()]

    def _config(self, session: Session) -> dict:
        row = session.get(models.Setting, "scoring")
        return row.value if row and isinstance(row.value, dict) else {}

    def run(self, session: Session, trading_date: date) -> dict:
        td = trading_date
        config = self._config(session)

        price_cols = ["stock_id", "date", "open", "high", "low", "close", "volume"]
        ind_cols = [
            "stock_id", "date", "ma5", "ma10", "ma20", "ma60", "vol_ma5", "vol_ma20",
            "kd_k", "kd_d", "macd", "macd_signal", "macd_hist", "atr14", "bias_20", "bias_60",
        ]
        inst_cols = ["stock_id", "date", "foreign_net", "trust_net", "dealer_net", "total_net"]

        prices = _load_groups(session, models.DailyPrice, price_cols, td)
        inds = _load_groups(session, models.Indicator, ind_cols, td)
        inst = _load_groups(session, models.Institutional, inst_cols, td)
        stock_map = {s.id: s for s in session.execute(select(models.Stock)).scalars().all()}

        # 基本面（長線軌）：每檔最新一筆
        valuation = _load_latest(
            session, models.Valuation, ["pe", "pb", "dividend_yield"],
            [models.Valuation.stock_id, models.Valuation.date],
        )
        revenue = _load_latest(
            session, models.RevenueMonthly, ["revenue", "yoy", "mom"],
            [models.RevenueMonthly.stock_id, models.RevenueMonthly.year, models.RevenueMonthly.month],
        )
        financials = _load_latest(
            session, models.FinancialQuarter,
            ["eps", "gross_margin", "op_margin", "net_margin", "roe"],
            [models.FinancialQuarter.stock_id, models.FinancialQuarter.year, models.FinancialQuarter.quarter],
        )
        # 類股方向（P3）→ Track 算 sector_adjust
        sector_daily = {
            sd.sector_id: sd
            for sd in session.execute(
                select(models.SectorDaily).where(models.SectorDaily.date == td)
            ).scalars().all()
        }

        rows: list[dict] = []
        scored = 0
        for sid, ind_g in inds.items():
            if ind_g["date"].iloc[-1] != td:  # 當日無指標 = 當日未交易，跳過
                continue
            stock = stock_map.get(sid)
            price_g = prices.get(sid)
            if stock is None or price_g is None or price_g["date"].iloc[-1] != td:
                continue
            ctx = StockContext(
                stock=stock,
                date=td,
                prices=price_g,
                inds=ind_g,
                inst=inst.get(sid, pd.DataFrame(columns=inst_cols)),
                valuation=valuation.get(sid),
                revenue=revenue.get(sid),
                financials=financials.get(sid),
                sector=sector_daily.get(stock.sector_id),
            )
            for track in self.tracks:
                rows.append(track.evaluate(ctx, config.get(track.track_key, {})))
            scored += 1

        n = BaseRepository(models.Score).upsert_many(session, rows)
        session.flush()
        passed = {t.track_key: sum(1 for r in rows if r["track"] == t.track_key and r["passed"]) for t in self.tracks}
        return {"status": "ok", "scored_stocks": scored, "rows": n, "passed": passed}
