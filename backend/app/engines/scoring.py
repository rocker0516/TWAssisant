"""ScoringEngine（架構③）：批次載入 → 每檔建 StockContext → 跑雙軌 → 落 scores。

規則不各自查 DB：此處一次把 price/indicator/法人 載進記憶體、依股號切片建 context。
每檔每軌產一列（passed 標記是否進推薦）。配分/門檻由 settings 'scoring' 覆寫。
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from ..storage import models
from ..storage.repositories import BaseRepository
from .base import BaseEngine
from .context import StockContext
from .rules.base import clamp
from .tracks import LongTrack, WaveTrack

_STABILITY_LOOKBACK = 5  # 取近 5 個評分日算分數穩定度
_STABILITY_MIN_POINTS = 3  # 含今日至少 3 點才談穩定度，否則中性不扣


def _stability_factor(prior_totals: list[float | None], today: float | None) -> float:
    """分數穩定度係數 0.8~1.0（L3）：近期總分波動越大越不可信。

    刻意做成「輕推」——技術分天生隨行情起伏，過重會把整條軌壓平、失去鑑別度
    （鑑別交給共識度）。標準差以 60 分正規化、下限 0.8（最多扣 20%）；史料不足回
    1.0 中性。穩定度另存欄位、tooltip 透明顯示。
    """
    vals = [v for v in [*prior_totals, today] if v is not None]
    if len(vals) < _STABILITY_MIN_POINTS:
        return 1.0
    mean = sum(vals) / len(vals)
    std = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
    return clamp(1.0 - std / 60.0, 0.8, 1.0)


# 評分只需近窗（規則最長用到 ma60 + 前低 + 斜率）；MA240 等長均線已在 indicators
# 表預先算好、只讀最新列。下界避免回補長歷史後把全市場×多年 ORM 全載進記憶體（OOM）。
_SCORING_LOOKBACK_DAYS = 400


def _load_groups(
    session: Session, model, cols: list[str], td: date,
    lookback_days: int = _SCORING_LOOKBACK_DAYS,
) -> dict[str, pd.DataFrame]:
    """載入某表 [td-lookback, td] 的資料，依 stock_id 分組（升冪）。"""
    stmt = (
        select(model)
        .where(model.date <= td, model.date >= td - timedelta(days=lookback_days))
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
        margin_cols = ["stock_id", "date", "margin_balance", "margin_change", "short_balance", "short_change"]
        hold_cols = ["stock_id", "date", "big_pct", "over1000_pct", "small_pct", "holders", "avg_lots"]

        prices = _load_groups(session, models.DailyPrice, price_cols, td)
        inds = _load_groups(session, models.Indicator, ind_cols, td)
        inst = _load_groups(session, models.Institutional, inst_cols, td)
        margin = _load_groups(session, models.Margin, margin_cols, td)
        holding = _load_groups(session, models.ShareholdingDistribution, hold_cols, td)
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
        # 處置警示（近15日）→ 共用硬篩排除（不推薦處置股）。一次撈，規則不各自查 DB。
        from datetime import timedelta

        disposed: dict[str, list] = {}
        for ev in session.execute(
            select(models.Event).where(
                models.Event.category == "處置警示", models.Event.date >= td - timedelta(days=15)
            )
        ).scalars().all():
            disposed.setdefault(ev.stock_id, []).append(ev)

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
                margin=margin.get(sid),
                holding=holding.get(sid),
                valuation=valuation.get(sid),
                revenue=revenue.get(sid),
                financials=financials.get(sid),
                sector=sector_daily.get(stock.sector_id),
                events=disposed.get(sid),
            )
            for track in self.tracks:
                rows.append(track.evaluate(ctx, config.get(track.track_key, {})))
            scored += 1

        self._apply_stability(session, td, rows)

        n = BaseRepository(models.Score).upsert_many(session, rows)
        session.flush()
        passed = {t.track_key: sum(1 for r in rows if r["track"] == t.track_key and r["passed"]) for t in self.tracks}
        return {"status": "ok", "scored_stocks": scored, "rows": n, "passed": passed}

    def _apply_stability(self, session: Session, td: date, rows: list[dict]) -> None:
        """L3：用近期歷史總分算穩定度，折進 confidence（confidence = 完整度×共識度×穩定度）。

        史料不足時穩定度=1.0，confidence 不變。重跑當日冪等（只看 date<td 的歷史）。
        """
        recent_dates = session.execute(
            select(distinct(models.Score.date))
            .where(models.Score.date < td)
            .order_by(models.Score.date.desc())
            .limit(_STABILITY_LOOKBACK)
        ).scalars().all()
        prior: dict[tuple[str, str], list[float | None]] = {}
        if recent_dates:
            for sid, track, total in session.execute(
                select(models.Score.stock_id, models.Score.track, models.Score.total_score)
                .where(models.Score.date.in_(recent_dates))
            ):
                prior.setdefault((sid, track), []).append(total)
        for r in rows:
            st = _stability_factor(prior.get((r["stock_id"], r["track"]), []), r["total_score"])
            r["stability"] = round(st, 3)
            r["confidence"] = round((r.get("confidence") or 0.0) * st, 1)
