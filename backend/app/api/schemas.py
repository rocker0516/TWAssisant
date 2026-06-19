"""API DTO（Pydantic）。→ OpenAPI → 前端 openapi-typescript 生 TS 型別。

P1：推薦頁 + 詳情頁所需。後續階段再擴充。
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class RecommendationDetail(BaseModel):
    """展開區單一面向：分數 + 帶數字的客觀證據（波段軌目前有 evidence；長線軌暫無）。"""

    category: str  # trend / momentum / volume / chip / pattern / position ...
    score: float
    evidence: str | None = None


class ScoreDTO(BaseModel):
    track: str
    passed: bool
    total_score: float | None
    sub_scores: dict[str, float] | None
    coverage: float | None  # 有資料維度占比 0~1
    confidence: float | None  # 分數可信度 0~100（完整度×共識度×穩定度）
    stability: float | None  # 近期總分穩定度 0.6~1
    buy_low: float | None
    buy_high: float | None
    stop_loss: float | None
    loss_pct: float | None
    reasons: list[str] | None
    details: list[RecommendationDetail] | None = None


class RecommendationItem(BaseModel):
    stock_id: str
    name: str
    sector_name: str | None
    track: str
    total_score: float | None
    sub_scores: dict[str, float] | None
    coverage: float | None
    confidence: float | None
    stability: float | None
    close: float | None
    change_pct: float | None
    buy_low: float | None
    buy_high: float | None
    stop_loss: float | None
    loss_pct: float | None
    reasons: list[str] | None
    details: list[RecommendationDetail] | None = None  # 展開區：各面向分數+證據
    spark: list[float] | None = None  # 近期收盤序列（約近 20 個交易日，由舊到新）


class RecommendationList(BaseModel):
    track: str
    style: str | None = None  # 波段軌進場風格 breakout/pullback（長線軌為 None）
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
    # 集保股權分散（TDCC，週快照）。big_trend = 大戶占比近月變化（無歷史時 None）
    holding_date: date | None = None
    big_pct: float | None = None        # 大戶（≥400 張）占比
    over1000_pct: float | None = None   # 千張大戶（≥1000 張）占比
    small_pct: float | None = None      # 散戶（<10 張）占比
    holders: int | None = None          # 總股東人數
    big_trend: float | None = None      # 大戶占比近月變化（個百分點，+=集中）


class HoldingPoint(BaseModel):
    """集保週資料單點（曲線用）。"""

    date: date
    big_pct: float | None = None        # 大戶（≥400 張）占比
    over1000_pct: float | None = None   # 千張大戶（≥1000 張）占比
    small_pct: float | None = None      # 散戶（<10 張）占比
    holders: int | None = None          # 總股東人數


class HoldingHistoryResponse(BaseModel):
    stock_id: str
    points: list[HoldingPoint]  # 升冪（舊→新）
    backfilling: bool           # 是否正在背景回補歷史（前端可顯示「回補中」並稍後重整）


class FundamentalSummary(BaseModel):
    pe: float | None = None
    pb: float | None = None
    dividend_yield: float | None = None
    eps: float | None = None
    revenue_yoy: float | None = None


class StockSearchItem(BaseModel):
    """查詢框結果項（股號/股名跳轉用）。"""

    stock_id: str
    name: str
    market: str | None = None
    is_etf: bool = False


class EtfInfo(BaseModel):
    """ETF 身分資料（個股無月營收/本益比時改顯示這塊）。"""

    kind: str | None = None           # 指數型 / 債券型 / 主動式
    fund_type: str | None = None      # 原始基金類型字串
    track_index: str | None = None    # 追蹤指數（主動式為 None）
    has_foreign: bool | None = None   # 是否含國外成分股
    scale_label: str | None = None    # 大型 / 中型 / 小型
    scale_billion: float | None = None  # 規模估算（億元）= 發行單位數 × 收盤價
    listed_date: date | None = None


class EventDTO(BaseModel):
    date: date
    category: str | None
    title: str
    summary: str | None
    is_risk: bool
    source: str | None
    url: str | None


class StockDetail(BaseModel):
    stock_id: str
    name: str
    sector_name: str | None
    market: str | None
    date: date | None
    close: float | None
    change: float | None
    change_pct: float | None
    is_etf: bool = False
    scores: dict[str, ScoreDTO | None]  # {"wave": ..., "long": ...}
    chip: ChipSummary | None
    fundamental: FundamentalSummary | None
    etf: EtfInfo | None = None
    events: list[EventDTO]
    news_digest: str | None = None  # AI 近期消息重點（盤後批次快取）


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


class LevelDTO(BaseModel):
    price: float
    kind: str  # "support" | "resistance"
    strength: int  # 0~100
    methods: list[str]
    distance_pct: float  # 相對現價（負=下方支撐、正=上方壓力）


class LevelsResponse(BaseModel):
    stock_id: str
    close: float | None
    supports: list[LevelDTO]
    resistances: list[LevelDTO]


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
    interpretation: str | None = None  # AI 類股方向解讀（盤後批次快取）


# ─────────── 觀察清單（P6）───────────


class WatchlistItemDTO(BaseModel):
    id: int
    stock_id: str
    name: str
    added_price: float | None
    target_price: float | None
    added_date: date | None
    reason: str | None
    note: str | None
    close: float | None
    change_pct: float | None
    wave_score: float | None
    long_score: float | None
    light: str  # green / yellow / white
    reminders: list[str]


class WatchlistDTO(BaseModel):
    id: int
    name: str
    items: list[WatchlistItemDTO]


class WatchlistsResponse(BaseModel):
    watchlists: list[WatchlistDTO]


class WatchlistCreate(BaseModel):
    name: str


class WatchlistItemCreate(BaseModel):
    stock_id: str
    target_price: float | None = None
    added_price: float | None = None
    added_date: date | None = None
    reason: str | None = None
    note: str | None = None


class ToHolding(BaseModel):
    track: str
    date: date
    price: float
    shares: int


# ─────────── 首頁總覽（P6）───────────


class MarketSummary(BaseModel):
    date: date | None
    turnover_billion: float | None  # 成交額（億）
    advancers: int
    decliners: int
    unchanged: int
    foreign_net: int | None
    trust_net: int | None
    dealer_net: int | None
    # 廣度 / 分化（量化）
    pct_above_ma20: float | None = None  # 站上月線占比
    pct_above_ma60: float | None = None  # 站上季線占比
    foreign_buy_count: int | None = None
    foreign_sell_count: int | None = None
    trust_buy_count: int | None = None
    trust_sell_count: int | None = None
    trust_top10_concentration: float | None = None  # 投信買超前10檔占比


class AlertBrief(BaseModel):
    stock_id: str
    name: str
    light: str
    return_pct: float | None
    signals: list[str]


class RecoBrief(BaseModel):
    stock_id: str
    name: str
    track: str
    total_score: float | None


class SectorBrief(BaseModel):
    id: int
    name: str
    strength_score: float | None
    trend_short: str | None
    rotation_stage: str | None


class EventBrief(BaseModel):
    stock_id: str
    name: str
    date: date
    category: str | None
    title: str
    is_risk: bool


class OverviewResponse(BaseModel):
    market: MarketSummary
    market_note: str | None = None  # AI 盤勢總結（盤後批次快取）
    holdings_alerts: list[AlertBrief]
    reco_wave_count: int
    reco_long_count: int
    reco_top: list[RecoBrief]
    sectors_top: list[SectorBrief]
    recent_events: list[EventBrief]


# ─────────────── 情報頁（近期消息總結）───────────────


class IntelEvent(BaseModel):
    stock_id: str
    name: str
    date: date
    category: str | None
    title: str
    is_risk: bool
    source: str | None
    url: str | None


class ThemeDigest(BaseModel):
    sector_id: int
    sector_name: str
    digest: str
    event_count: int
    risk_count: int


class IntelResponse(BaseModel):
    date: date | None
    market_digest: str | None = None  # AI 全市場消息重點（盤後批次快取）
    focus_digest: str | None = None   # AI 持股+觀察清單焦點
    themes: list[ThemeDigest]
    events: list[IntelEvent]
    total: int          # 窗口內事件總數（未受篩選影響）
    risk_count: int     # 窗口內重大利空數
    has_digest: bool    # 是否已有任何 LLM digest（無 API key/未跑批次時為 False）
