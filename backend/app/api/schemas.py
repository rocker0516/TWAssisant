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


# ─────────── 持股（P2）───────────


class HoldingCreate(BaseModel):
    stock_id: str
    track: str  # wave / long
    date: date
    price: float
    shares: int
    fee: float | None = None
    stop_loss_override: float | None = None
    trail_trigger_override: float | None = None
    trail_pullback_override: float | None = None
    note: str | None = None


class TransactionCreate(BaseModel):
    type: str  # add / sell（buy 由建立持股時自動產生）
    date: date
    price: float
    shares: int
    fee: float | None = None
    tax: float | None = None
    note: str | None = None


class HoldingPatch(BaseModel):
    stop_loss_override: float | None = None
    trail_trigger_override: float | None = None
    trail_pullback_override: float | None = None
    note: str | None = None


class TransactionDTO(BaseModel):
    id: int
    type: str
    date: date
    price: float
    shares: int
    fee: float | None
    tax: float | None
    note: str | None


class HoldingItem(BaseModel):
    id: int
    stock_id: str
    name: str
    track: str
    status: str
    opened_date: date | None
    closed_date: date | None
    shares: int
    avg_cost: float | None
    close: float | None
    change_pct: float | None  # 當日漲跌
    market_value: float | None
    unrealized_pnl: float | None
    return_pct: float | None
    realized_pnl: float | None
    # 出場狀態（ExitEngine）
    light: str
    level: str
    signals: list[str]
    hard_stop: float | None
    highest: float | None
    drawdown_pct: float | None
    trail_active: bool
    stop_loss_override: float | None
    trail_trigger_override: float | None
    trail_pullback_override: float | None
    note: str | None
    transactions: list[TransactionDTO]


class HoldingsSummary(BaseModel):
    count: int
    total_market_value: float
    total_unrealized_pnl: float
    total_return_pct: float | None
    total_realized_pnl: float


class HoldingsResponse(BaseModel):
    status: str
    items: list[HoldingItem]
    summary: HoldingsSummary


# ─────────── 類股（P3）───────────


class SectorItem(BaseModel):
    id: int
    name: str
    strength_score: float | None
    dim_momentum: float | None
    dim_fund: float | None
    dim_tech: float | None
    trend_short: str | None
    trend_long: str | None
    rotation_stage: str | None
    momentum_5: float | None
    momentum_20: float | None
    foreign_net: int | None
    turnover_share: float | None
    above_ma20: float | None
    constituents: int | None


class SectorList(BaseModel):
    date: date | None
    items: list[SectorItem]


class SectorConstituent(BaseModel):
    stock_id: str
    name: str
    close: float | None
    change_pct: float | None
    wave_score: float | None
    long_score: float | None
    recommended: bool


class SectorDetail(BaseModel):
    sector: SectorItem
    constituents: list[SectorConstituent]
