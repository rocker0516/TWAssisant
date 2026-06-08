"""API DTO（Pydantic）。→ OpenAPI → 前端 openapi-typescript 生 TS 型別。

P1：推薦頁 + 詳情頁所需。後續階段再擴充。
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class ScoreDTO(BaseModel):
    track: str
    passed: bool
    total_score: float | None
    sub_scores: dict[str, float] | None
    buy_low: float | None
    buy_high: float | None
    stop_loss: float | None
    loss_pct: float | None
    reasons: list[str] | None


class RecommendationItem(BaseModel):
    stock_id: str
    name: str
    sector_name: str | None
    track: str
    total_score: float | None
    sub_scores: dict[str, float] | None
    close: float | None
    change_pct: float | None
    buy_low: float | None
    buy_high: float | None
    stop_loss: float | None
    loss_pct: float | None
    reasons: list[str] | None


class RecommendationList(BaseModel):
    track: str
    date: date | None
    threshold: float
    items: list[RecommendationItem]  # 達門檻
    near: list[RecommendationItem]  # 接近門檻（65~70，折疊觀察區）


class ChipSummary(BaseModel):
    date: date | None
    foreign_net: int | None
    trust_net: int | None
    dealer_net: int | None
    total_net: int | None
    margin_balance: int | None
    short_balance: int | None


class FundamentalSummary(BaseModel):
    pe: float | None = None
    pb: float | None = None
    dividend_yield: float | None = None
    eps: float | None = None
    revenue_yoy: float | None = None


class StockDetail(BaseModel):
    stock_id: str
    name: str
    sector_name: str | None
    market: str | None
    date: date | None
    close: float | None
    change: float | None
    change_pct: float | None
    scores: dict[str, ScoreDTO | None]  # {"wave": ..., "long": ...}
    chip: ChipSummary | None
    fundamental: FundamentalSummary | None


class Candle(BaseModel):
    date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None
    ma5: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    kd_k: float | None = None
    kd_d: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None


class OhlcvResponse(BaseModel):
    stock_id: str
    candles: list[Candle]
