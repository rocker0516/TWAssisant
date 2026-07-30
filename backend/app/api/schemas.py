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


class LookbackReview(BaseModel):
    """回看：那天推薦至今的實際表現。買在當日收盤（與 PoppableEfficacy 同錨點）。"""

    entry_close: float | None          # 推薦日收盤（進場參考價）
    current_close: float | None        # 至今收盤
    return_pct: float | None           # 至今報酬 %（buy-and-hold 到今天）
    mfe_pct: float | None              # 期間內最大有利偏移 %（=最高 high / entry_close − 1）
    mae_pct: float | None              # 期間內最大不利偏移 %（=最低 low / entry_close − 1）
    hit_pop: bool = False              # 期間內是否摸到 +10%
    hit_pop_date: date | None = None   # 首次摸到 +10% 的交易日
    days_to_pop: int | None = None     # 從進場日到摸到 +10% 用了幾個交易日
    days_elapsed: int = 0              # 進場日後已過幾個交易日


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
    review: LookbackReview | None = None  # 回看模式才有：那天到今天的實際表現
    passed_styles: list[str] | None = None  # 通過的純門檻風格（explosive/strong/story/crash）
    passed_filter: bool | None = None  # 會噴硬篩(含遲滯)是否通過（前端判「會噴」標籤用）


class MarketRegime(BaseModel):
    """大盤 regime 燈（MA60 遲滯）：defense 期會噴命中率實證較低，前端預設收起清單。"""

    state: str  # hold=持有 | defense=防禦(收盤跌破季線MA60逾2%、尚未站回)
    date: date  # 判斷所用的最新指數日
    since: date  # 本狀態起始日
    close: float
    ma60: float
    gap_pct: float  # 收盤相對 MA60 %
    hold_hit_rate: float  # 驗證常數：持有期清單摸+10% 機率
    defense_hit_rate: float  # 驗證常數：防禦期清單摸+10% 機率


class RecommendationList(BaseModel):
    track: str
    style: str = "pop"  # 波段風格：pop=會噴；explosive=爆發（長線軌恆為 pop）
    top_pct: float | None = None  # 波段(會噴)軌：前 N% 為推薦（長線軌/爆發風格為 None）
    date: date | None
    threshold: float  # 門檻分數（波段軌 = 100 − top_pct）
    items: list[RecommendationItem]  # 波段軌=全部過硬篩(前端橫桿切)；長線軌=達門檻
    near: list[RecommendationItem]  # 接近門檻（長線軌用；波段軌為空）
    regime: MarketRegime | None = None  # 波段軌限定的大盤閘門；長線軌/資料不足為 None


class LookbackSummary(BaseModel):
    """回看清單摘要（命中率/平均報酬）。"""

    n: int                              # 清單檔數
    hit_count: int                      # 已摸 +10% 檔數
    hit_rate: float | None              # 命中率 0~1
    avg_return_pct: float | None        # 至今平均報酬 %
    avg_mfe_pct: float | None           # 至今平均最大有利偏移 %
    avg_mae_pct: float | None           # 至今平均最大不利偏移 %


class RecommendationLookbackResponse(BaseModel):
    """回看：N 個交易日前波段軌推薦的至今實況。"""

    track: str                          # 固定 wave
    lookback_date: date | None          # 推薦日（N 個交易日前）
    today_date: date | None             # 最新交易日（資料截止）
    days_back: int                      # 回看了幾個交易日（=入參 days）
    top_pct: float                      # 套用的嚴格度（前 N%）
    cutoff: float                       # 對應的分數門檻
    items: list[RecommendationItem]     # 已含 review；依當日分數降序
    summary: LookbackSummary            # 整批摘要


class LookbackDatePoint(BaseModel):
    """月曆單日：那天的會噴清單至今命中率。"""

    date: date                          # 推薦日
    n: int                              # 清單檔數（過硬篩且分數≥cutoff）
    hit_count: int                      # 已摸 +10% 檔數
    hit_rate: float | None              # 命中率 0~1


class LookbackCalendar(BaseModel):
    """回看月曆：每個過去的 Score 日一筆命中率。"""

    today_date: date | None             # 最新交易日（也是回看的資料截止）
    top_pct: float                      # 套用的嚴格度
    cutoff: float                       # 分數門檻
    dates: list[LookbackDatePoint]      # 由舊到新


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
    # 借券賣出 / 當沖 / 董監持股
    sbl_balance: int | None = None      # 借券賣出餘額（張，最新）
    sbl_chg20: int | None = None        # 借券餘額近 20 個資料日增減（張）
    dt_ratio5: float | None = None      # 近 5 日當沖占成交量比 %（上市限定）
    insider_pct_chg: float | None = None  # 董監持股股數最近一月 vs 前月變化 %
    insider_pledge_pct: float | None = None  # 董監設質比率 %（最新月）


class ChipPoint(BaseModel):
    """籌碼每日一點（法人買賣超 + 融資融券餘額）。"""

    date: date
    foreign_net: int | None = None
    trust_net: int | None = None
    dealer_net: int | None = None
    total_net: int | None = None
    margin_balance: int | None = None
    short_balance: int | None = None


class ChipHistoryResponse(BaseModel):
    stock_id: str
    points: list[ChipPoint]  # 升冪（舊→新）


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


# ─────────── 籌碼動向（法人 + 大戶散戶）───────────


class ActorRelation(BaseModel):
    """單一 actor：法人 20 日累計 vs 指數未來報酬的量化關係。"""

    h: int
    samples: int
    avg_ret_pos: float | None = None   # 累積買超時指數未來平均報酬 %
    avg_ret_neg: float | None = None   # 累積賣超時指數未來平均報酬 %
    winrate_pos: float | None = None   # 累積買超時上漲勝率 0~1
    corr: float | None = None          # 累積 vs 未來報酬相關係數


class MarketFlowActor(BaseModel):
    """單一 actor（合計/外資/投信/自營）的市場資金流向（億元）。"""

    daily: list[float | None]          # 每日淨買超
    cum: list[float | None]            # 累積淨買超曲線（主視角）
    cum20: float | None = None
    cum60: float | None = None
    cum120: float | None = None
    consec_days: int = 0               # 連買(+)/連賣(-)天數
    phase: str | None = None           # 週期段
    relation: ActorRelation | None = None


class MarketDerivativesBlock(BaseModel):
    """期貨籌碼（TAIFEX）：台指期法人未平倉淨口數曲線 + P/C ratio。觀察儀表定位。

    divergence：外資「現貨 20 日累計買賣超」與「期貨淨未平倉 20 日變化」方向相反時
    標記（現貨買+期貨空單增＝對沖非看多；反向亦然）。純描述現況、無方向宣稱。
    """

    dates: list[str]
    tx_foreign_oi_net: list[int | None]   # 外資台指期未平倉淨口數（日）
    tx_trust_oi_net: list[int | None]
    tx_dealer_oi_net: list[int | None]
    pc_oi_ratio: list[float | None]       # 選擇權未平倉 P/C %
    latest_pc_vol_ratio: float | None = None
    foreign_oi_latest: int | None = None
    foreign_oi_chg20: int | None = None   # 外資淨 OI 近 20 日變化（口）
    spot_foreign_cum20: float | None = None  # 外資現貨近 20 日累計（億元，對照）
    divergence: str | None = None         # 同向/背離的白話描述


class MarketFlowResponse(BaseModel):
    from_date: str | None
    to_date: str | None
    dates: list[str]
    index: list[float | None]          # 加權指數收盤（疊圖對照）
    actors: dict[str, MarketFlowActor]  # total / foreign / trust / dealer
    derivatives: MarketDerivativesBlock | None = None  # 期貨籌碼（無資料時 None）


class SectorFlowItem(BaseModel):
    id: int
    name: str
    foreign_cum: int | None = None     # 近 lookback 日法人淨買超累計（張）
    trust_cum: int | None = None
    dealer_cum: int | None = None
    total_cum: int | None = None
    constituents: int | None = None


class SectorFlowList(BaseModel):
    date: str | None
    lookback: int
    items: list[SectorFlowItem]


class SectorRotationPoint(BaseModel):
    """類股輪動軌跡單點：原始量，前端依強度/絕對模式各自算 X/Y/size。"""

    date: str
    net20: int      # 近20日法人淨買超（張）
    net5: int       # 近5日法人淨買超（張）
    turnover20: int  # 近20日成交量（張）
    turnover5: int   # 近5日成交量（張）


class SectorRotationItem(BaseModel):
    id: int
    name: str
    points: list[SectorRotationPoint]  # 升冪（舊→新），最後一點為現況頭部


class SectorRotationResponse(BaseModel):
    actor: str
    weeks: int
    date: str | None
    sectors: list[SectorRotationItem]


class FlowStockItem(BaseModel):
    stock_id: str
    name: str
    sector_name: str | None = None
    foreign_cum20: int | None = None
    trust_cum20: int | None = None
    dealer_cum20: int | None = None
    total_cum20: int | None = None
    foreign_cum60: int | None = None
    trust_cum60: int | None = None
    dealer_cum60: int | None = None
    total_cum60: int | None = None
    consec_days: int = 0               # 三大法人合計連買/連賣天數
    big_pct: float | None = None       # 最新大戶占比
    big_trend: float | None = None     # 大戶占比近 ~8 週變化（個百分點）
    small_trend: float | None = None   # 散戶占比近 ~8 週變化
    holders_change: float | None = None  # 股東人數近 ~8 週變化 %
    sbl_balance: int | None = None       # 借券賣出餘額（張，最新）
    sbl_chg20: int | None = None         # 借券餘額近 20 日增減（張）
    dt_ratio5: float | None = None       # 近 5 日當沖占成交量比 %（上市限定）
    close: float | None = None
    change_pct: float | None = None


class FlowStockList(BaseModel):
    date: str | None
    sort: str
    items: list[FlowStockItem]


class ChipAlertItem(BaseModel):
    stock_id: str
    name: str
    sector_name: str | None = None
    kind: str                  # trust_first_buy / trust_streak / sbl_spike / big_up_weeks
    kind_label: str            # 投信首買 / 投信連買 / 借券暴增 / 大戶連增
    detail: str                # 白話一句（含數字）
    value: float               # 排序用強度


class ChipAlertList(BaseModel):
    date: str | None
    items: list[ChipAlertItem]


class InstActorIC(BaseModel):
    ic: float | None = None            # 法人累積 → 未來報酬 rank-IC
    winrate_pos: float | None = None
    avg_ret_pos: float | None = None
    samples: int = 0


class InstPriceRelation(BaseModel):
    generated_at: str | None
    horizon: int
    entry_dates: int
    actors: dict[str, InstActorIC]
    note: str | None = None


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
