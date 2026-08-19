"""API DTO（Pydantic）。→ OpenAPI → 前端 openapi-typescript 生 TS 型別。

P1：推薦頁 + 詳情頁所需。後續階段再擴充。
"""

from __future__ import annotations

import datetime as _dt
from datetime import date
from typing import Literal

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


class LongTargetZone(BaseModel):
    """長線軌目標區間（參考期間 12 個月＝回測視窗）。

    基準錨優先用法人目標價中位（FactSet），無法人報告退 PE 河流中位帶（估值推算）；
    保守/樂觀恆為 PE 河流中位帶/上緣帶 × 隱含 EPS（長線硬篩②保證 EPS>0，缺的只會是 PE 史料）。
    """

    basis: Literal["analyst", "pe_river"]  # 基準錨來源：法人目標價 / 估值推算
    base: float                       # 基準目標價
    upside_pct: float | None          # 基準相對現價上漲空間 %
    low: float | None                 # 保守：PE 河流中位帶價
    high: float | None                # 樂觀：PE 河流上緣帶價
    analyst_target: float | None = None   # 法人目標價中位（有 FactSet 報告才有）
    analyst_date: date | None = None      # 該目標價發布日
    analyst_count: int | None = None      # 分析師家數
    hit: bool = False                 # 已達標：analyst=發布後最高價曾觸及；pe_river=現價已在基準上


class LongGraduation(BaseModel):
    """長線軌畢業條件（重新審視訊號，非停損）：達標 / 魚齡老化 / 已暴漲。"""

    hit_target: bool = False          # 現價已觸及基準目標
    streak_months: int | None = None  # 魚齡：連續營收 YoY>0 月數（>12 轉黃、>18 轉紅）
    mom12_pct: float | None = None    # 近 12 月漲幅 %（>80 轉黃、>200 轉紅）


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
    prob_hit: float | None = None   # 同條件歷史命中%（分數帶×波動帶×大盤狀態查五年表）
    prob_n: int | None = None       # 該條件格歷史樣本數
    prob_cond: str | None = None    # 條件描述（例：分數90-95×波動5-8%×大盤正常）
    prob_mae: float | None = None   # 同條件歷史平均最深回撤%（風險行顯示用）
    vol_ratio: float | None = None  # 量增比＝vol_ma5/vol_ma20（標籤共振徽章用：爆發×量增>1.5 實證加成）
    # 注意/處置動能徽章（2026-08 判官驗證：處置後10日 控波動+16pp、holdout命中71%；注意×上升 +5~7pp）
    attention: str | None = None    # "punish"（處置公告10日內/執行中）/ "notice"（近5日列注意）/ None
    attention_tags: list[str] = []  # 完整旗標集（可同時 punish+notice；精確組合篩選用）
    # ML 共識確認（2026-08 實證：四因子∩ML 交集 holdout 命中 ~34% vs 單獨 ~30-31%）
    ml_consensus: bool | None = None  # True=ML 模型也將其排入硬篩內前 20%（資料日對得上才附）
    target_zone: LongTargetZone | None = None   # 長線軌限定：目標區間（波段軌恆 None）
    graduation: LongGraduation | None = None    # 長線軌限定：畢業條件狀態


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
    # 月營收（最新月）
    revenue_ym: str | None = None          # 如 "2026/07"
    month_revenue: float | None = None     # 當月營收（億元）
    revenue_mom: float | None = None       # 月增 %
    # 季財報（最新單季）
    fin_quarter: str | None = None         # 如 "2026Q1"
    quarter_eps: float | None = None       # 單季 EPS
    gross_margin: float | None = None      # 毛利率 %
    op_margin: float | None = None         # 營益率 %
    net_margin: float | None = None        # 淨利率 %
    roe: float | None = None               # ROE %
    # 派生指標
    gross_margin_qoq: float | None = None  # 毛利率 vs 上季（個百分點）
    op_margin_qoq: float | None = None     # 營益率 vs 上季（個百分點）
    net_margin_qoq: float | None = None    # 淨利率 vs 上季（個百分點）
    eps_yoy: float | None = None           # 單季 EPS vs 去年同季 %
    rev_yoy_streak: int | None = None      # 月營收 YoY 連續正成長月數（0=最新月已轉負）


class RevenuePoint(BaseModel):
    """月營收一點。"""

    ym: str                              # "2026/07"
    revenue: float | None = None         # 億元
    yoy: float | None = None             # 年增 %
    mom: float | None = None             # 月增 %


class QuarterPoint(BaseModel):
    """季財報一點（單季）。"""

    label: str                           # "2026Q1"
    eps: float | None = None
    revenue: float | None = None         # 億元
    gross_margin: float | None = None
    op_margin: float | None = None
    net_margin: float | None = None
    roe: float | None = None


class FundamentalHistoryResponse(BaseModel):
    """基本面歷史序列（月營收 + 單季財報），供趨勢圖。"""

    stock_id: str
    revenues: list[RevenuePoint]
    quarters: list[QuarterPoint]
    backfilling: bool = False            # 歷史仍在回補中（資料太少時提示）


class DividendEntry(BaseModel):
    """一期股利（年度制一年一列、季配一年四列）。"""

    period: str                          # "114年" / "114年第4季"
    cash: float | None = None            # 現金股利（元/股）
    stock: float | None = None           # 股票股利（元/股）
    cash_ex_date: date | None = None     # 除息交易日
    pay_date: date | None = None         # 發放日
    fill_days: int | None = None         # 填息交易日數（未填/價格資料不足=None）
    filled: bool | None = None           # 是否已填息（價格資料不足=None）


class DividendsResponse(BaseModel):
    stock_id: str
    entries: list[DividendEntry]         # 除息日降冪
    cash_12m: float | None = None        # 近 12 個月現金股利合計（元/股）
    yield_12m: float | None = None       # 近 12 個月現金殖利率 %（÷現價）


class PeRiverPoint(BaseModel):
    date: date
    close: float | None = None
    bands: list[float | None]            # 對應 PeRiverResponse.pe_levels 的價格帶


class PeRiverResponse(BaseModel):
    """本益比河流圖：全期間 PE 分位數 × 隱含 EPS → 價格帶，疊收盤價。"""

    stock_id: str
    pe_levels: list[float]               # 分位數 PE（低→高）
    points: list[PeRiverPoint]
    current_pe: float | None = None
    pe_percentile: float | None = None   # 現在 PE 落在歷史第幾百分位（0~100）
    backfilling: bool = False            # 估值/價格歷史仍在回補


class TechSummaryResponse(BaseModel):
    """技術指標摘要：現值 KD/MACD/乖離 + Beta/52週位置/波動（皆由既有日線與指標計算）。"""

    stock_id: str
    # 欄位名 date 會遮蔽 datetime.date（預設值進 class namespace），需用模組限定名
    date: _dt.date | None = None
    kd_k: float | None = None
    kd_d: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    bias_20: float | None = None         # 20 日乖離 %
    bias_60: float | None = None
    beta: float | None = None            # 對加權指數，近一年日報酬迴歸
    high_52w: float | None = None
    low_52w: float | None = None
    dist_high_pct: float | None = None   # 現價距 52 週高 %（負值＝低於高點）
    dist_low_pct: float | None = None    # 現價距 52 週低 %
    volatility_pct: float | None = None  # 年化波動率 %（近 60 日日報酬標準差×√240）


class FearGreedComponent(BaseModel):
    """恐懼貪婪指數單一組件：score 0~100（高=貪婪）、value 為原始值。"""

    key: str
    label: str
    desc: str | None = None
    score: float
    value: float | None = None


class FearGreedPoint(BaseModel):
    date: date
    score: float


class UsFearGreed(BaseModel):
    """CNN 官方 Fear & Greed（美股）。直抓 CNN dataviz API。"""

    score: float
    rating: str                      # extreme fear / fear / neutral / greed / extreme greed
    label: str                       # 中文
    prev_close: float | None = None
    prev_week: float | None = None
    prev_month: float | None = None
    prev_year: float | None = None
    history: list[FearGreedPoint] = []


class FearGreedResponse(BaseModel):
    """台股恐懼貪婪指數（自算組件百分位）＋ CNN 官方美股指數並列。"""

    # 欄位名 date 有預設值會遮蔽 datetime.date，需模組限定名（同 TechSummaryResponse）
    date: _dt.date | None = None
    score: float | None = None
    label: str | None = None
    components: list[FearGreedComponent] = []
    history: list[FearGreedPoint] = []
    us: UsFearGreed | None = None    # CNN 抓失敗時為 None（前端隱藏該區塊）


class TagComboStat(BaseModel):
    """一種「精確標籤組合」的出現與成效統計（组合鍵如 explosive+notice）。"""

    key: str
    n: int                              # 出現樣本數（檔×日）
    share_pct: float                    # 占全部有標籤樣本 %
    hit_rate: float | None = None       # 10 交易日內碰到 +10% 比率（樣本夠熟才計）
    avg_ret_pct: float | None = None    # 30 日實際報酬平均
    avg_mfe_pct: float | None = None
    avg_mae_pct: float | None = None


class SignalDecayPoint(BaseModel):
    ym: str                          # 月份 YYYY-MM
    n: int
    hit: float | None = None         # 該月此訊號樣本 10 日碰 +10% 率 %
    lift: float | None = None        # hit − 該月全市場基率（pp）


class SignalDecaySeries(BaseModel):
    key: str                         # pop/explosive/strong/story/crash/punish/notice
    points: list[SignalDecayPoint] = []
    hit_all: float | None = None     # 全期命中 %
    lift_all: float | None = None
    hit_recent: float | None = None  # 近 3 個月
    lift_recent: float | None = None


class SignalDecayResponse(BaseModel):
    """訊號時變效力：各訊號逐月 10 日命中率與相對基率 lift（影響度隨時間變化）。"""

    today_date: date | None = None
    base: list[SignalDecayPoint] = []   # 全市場基率逐月（hit 欄）
    signals: list[SignalDecaySeries] = []


class ComboSample(BaseModel):
    """精確組合的單一樣本（某檔某日）＋10 日窗成效。"""

    date: date
    stock_id: str
    name: str
    hit: bool | None = None          # 10 日內碰 +10%；樣本齡不足＝None（評估中）
    ret_pct: float | None = None     # 第 10 日收盤報酬
    mfe_pct: float | None = None
    mae_pct: float | None = None


class ComboSamplesResponse(BaseModel):
    combo: str
    since: date | None = None
    samples: list[ComboSample] = []  # 新→舊


class CooccurrenceResponse(BaseModel):
    """風格標籤共存結構：1對1 條件機率矩陣 + 精確組合全枚舉（含成效）。

    matrix[i][j] = P(同時有 tags[j] | 已有 tags[i])，%；對角=100、列樣本<30 為 null。
    combos＝每種實際出現的標籤集合（含單標籤獨佔），n<30 不列。
    樣本＝波段軌每日評分列；注意/處置以徽章同窗判定
    （notice=公告後~5交易日、punish=公告後~10交易日或執行期間）。
    """

    since: date | None = None
    today_date: date | None = None
    tags: list[str] = []
    counts: dict[str, int] = {}
    matrix: list[list[float | None]] = []
    combos: list[TagComboStat] = []


class AttentionEntry(BaseModel):
    date: date
    kind: str                        # notice / punish
    times: int | None = None
    begin_date: date | None = None
    end_date: date | None = None
    reason: str | None = None


class AttentionResponse(BaseModel):
    """注意/處置狀態。實證上列入者常伴隨上漲動能（熱錢聚集），前端以動能徽章呈現。"""

    stock_id: str
    status: str | None = None        # punish=處置中 / notice=近 5 日曾列注意 / None
    punish_end: date | None = None   # 處置迄日（status=punish 時）
    notice_count_30d: int = 0        # 近 30 日列注意次數
    entries: list[AttentionEntry] = []  # 近 90 日明細（新→舊）


class FinStatementQuarter(BaseModel):
    """單季財務報表關鍵科目（金額單位：億元；比率 %；每股淨值 元）。"""

    label: str                                  # 2026Q1
    # 資產負債表（期末餘額）
    cash: float | None = None
    current_assets: float | None = None
    total_assets: float | None = None
    current_liab: float | None = None
    total_liab: float | None = None
    equity: float | None = None
    inventories: float | None = None
    receivables: float | None = None
    debt_ratio: float | None = None             # 負債總額/資產總額 %
    current_ratio: float | None = None          # 流動資產/流動負債 %
    bps: float | None = None                    # 每股淨值＝權益/發行股數
    # 現金流量表（單季化）
    op_cf: float | None = None
    inv_cf: float | None = None
    fin_cf: float | None = None
    capex: float | None = None                  # 取得不動產廠房設備（負＝流出）
    fcf: float | None = None                    # 自由現金流＝營業 + capex


class FinancialStatementsResponse(BaseModel):
    """資產負債表＋現金流量表摘要（FinMind 逐檔懶抓、30 天快取）。新→舊。"""

    stock_id: str
    quarters: list[FinStatementQuarter]


class ChainTagDTO(BaseModel):
    """個股產業鏈定位一筆（官方 ic.tpex.org.tw）。"""

    chain_id: str
    chain_name: str                      # 半導體
    stream: str | None = None            # 上游/中游/下游
    main_node: str | None = None         # IC/晶圓製造
    node_name: str | None = None         # 晶圓製造（最細分類＝業務標籤）


class SectorBriefDTO(BaseModel):
    """所屬類股健康度摘要（個股頁小卡，連到類股詳情）。"""

    sector_id: int
    name: str
    date: date | None  # 有預設值會遮蔽型別名 date，故設為必填
    strength_score: float | None = None
    trend_short: str | None = None
    trend_long: str | None = None
    rotation_stage: str | None = None
    momentum_5: float | None = None
    momentum_20: float | None = None
    foreign_net: int | None = None       # 類股法人近5日淨買超（張）


class ChainNode(BaseModel):
    name: str                            # 主節點名
    count: int                           # 本國掛牌公司數
    mine: bool                           # 個股是否位於此節點


class ChainStream(BaseModel):
    stream: str                          # 上游/中游/下游
    nodes: list[ChainNode]


class ChainStructure(BaseModel):
    chain_id: str
    chain_name: str
    my_nodes: list[str]                  # 個股所在細分節點名
    streams: list[ChainStream]


class IndustryChainResponse(BaseModel):
    """個股產業鏈上下游全景（官方價值鏈平台）。"""

    stock_id: str
    chains: list[ChainStructure]


class CompanyProfileDTO(BaseModel):
    """公司基本資料（ETF 無此塊）。"""

    industry: str | None = None            # 產業別（來源原始字串）
    listed_date: date | None = None        # 上市/上櫃日期
    established_date: date | None = None   # 成立日期
    chairman: str | None = None            # 董事長
    president: str | None = None           # 總經理
    capital_billion: float | None = None   # 股本（億元）＝實收資本額/1e8
    market_cap_billion: float | None = None  # 市值估算（億元）＝發行股數×收盤價/1e8
    website: str | None = None


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
    profile: CompanyProfileDTO | None = None
    chains: list[ChainTagDTO] = []       # 產業鏈定位（業務標籤）
    sector_brief: SectorBriefDTO | None = None  # 所屬類股健康度摘要
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


class RecommendationMark(BaseModel):
    """K 線上的推薦段落標記（起始日）。"""

    date: date
    status: str  # "hit"（10日內碰+10%）| "miss"（窗走完沒碰）| "pending"（窗未走完）
    hit_date: date | None = None  # 首次摸到 +10% 的交易日（僅 hit）
    ret_pct: float | None = None  # 期間 MFE %（僅 hit）


class RecommendationMarksResponse(BaseModel):
    stock_id: str
    marks: list[RecommendationMark]


class TargetPriceEntry(BaseModel):
    """一筆 FactSet 共識目標價（含達標實況）。"""

    date: date
    target_price: float
    prev_target: float | None = None
    direction: str = "new"  # up/down/flat/new
    target_high: float | None = None
    target_low: float | None = None
    analyst_count: int | None = None
    rating_bull: int | None = None
    rating_neutral: int | None = None
    rating_bear: int | None = None
    eps_est: float | None = None
    hit: bool = False          # 有效期間內盤中高點是否觸及目標價
    hit_date: date | None = None
    upside_pct: float | None = None  # 僅 latest：目標價/最新收盤 − 1


class TargetPriceResponse(BaseModel):
    stock_id: str
    latest: TargetPriceEntry | None = None
    history: list[TargetPriceEntry] = []


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


class EntrySnapshot(BaseModel):
    """建倉當下 Score 的凍結副本。

    欄位固定，故宣告成模型而非 dict：宣告成 dict 時 openapi 只能吐出
    `{[key: string]: unknown}`，前端就得自己手寫一份同名型別 & 上來——
    那份手寫副本沒有任何機制保證它跟後端一致。
    """

    score_date: date
    total_score: float | None = None
    passed_filter: bool | None = None
    passed_styles: list[str] | None = None
    reasons: list[str] | None = None
    buy_low: float | None = None
    buy_high: float | None = None
    stop_loss: float | None = None
    close: float | None = None


class ThesisStatus(BaseModel):
    """進場論點追蹤：進場快照 vs 最新評分的對照結論。"""

    status: Literal["intact", "weakening", "broken", "unknown"]
    entry_score: float | None = None
    latest_score: float | None = None
    latest_passed_filter: bool | None = None
    messages: list[str] = []


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
    entry_snapshot: EntrySnapshot | None = None  # 建倉當下 Score 凍結副本
    thesis: ThesisStatus | None = None  # 論點是否還成立
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
    turnover_chg5: float | None = None  # 成交佔比 vs 前5日均（個百分點，+=資金移入）


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
    tags: list[str] = []  # 產業鏈細分標籤（如 晶圓製造 / 消費性IC）
    # 細分狀態聚合用（前端按標籤即時聚合成「細分狀態卡」）
    mom5_pct: float | None = None    # 近 5 交易日漲跌 %
    mom20_pct: float | None = None   # 近 20 交易日漲跌 %
    above_ma20: bool | None = None   # 站上月線
    inst_net5: int | None = None     # 近 5 日三大法人淨買超（張）
    inst_net20: int | None = None    # 近 20 日三大法人淨買超（張，判斷 5 日是加速還是退潮）
    rev_yoy: float | None = None     # 最新月營收年增 %


class SectorDetail(BaseModel):
    sector: SectorItem
    constituents: list[SectorConstituent]
    interpretation: str | None = None  # AI 類股方向解讀（盤後批次快取）
    market_mom5: float | None = None   # 加權指數近 5 交易日漲跌 %（細分相對強弱基準）
    market_mom20: float | None = None  # 加權指數近 20 交易日漲跌 %


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


# ─────────── 策略室（模擬倉 / 勝率分析 / 敏感度）───────────


class PaperPositionDTO(BaseModel):
    """模擬倉單筆部位（確定性重播產物，非落庫資料）。"""

    stock_id: str
    name: str
    signal_date: date          # 推薦日（Score 日）
    entry_date: date           # 進場日＝隔一交易日
    entry_price: float         # 進場價＝隔日最高（保守錨，與回看口徑一致）
    stop_price: float
    target_price: float
    status: str                # open / closed
    exit_date: date | None = None
    exit_price: float | None = None
    exit_reason: str | None = None  # stop / target / timeout
    return_pct: float | None = None  # closed=實現；open=以最新收盤計
    days_held: int = 0
    score: float | None = None
    prob_hit: float | None = None


class PaperEquityPoint(BaseModel):
    date: date
    cum_return_pct: float  # 已實現報酬累計（每筆等權 1 單位）


class PaperSimStats(BaseModel):
    trades: int
    closed: int
    open: int
    wins: int
    win_rate: float | None      # 已平倉勝率
    avg_return_pct: float | None
    total_return_pct: float | None  # 已實現累計（等權和）
    open_unrealized_pct: float | None  # 未平倉浮動合計
    avg_days_held: float | None
    max_drawdown_pct: float | None  # 權益曲線最大回撤（等權和口徑）


class PaperSimResponse(BaseModel):
    since: date | None
    today_date: date | None
    style: str
    prob_min: float
    top_n: int
    hold_days: int
    stop_pct: float
    target_pct: float
    stats: PaperSimStats
    equity: list[PaperEquityPoint]
    positions: list[PaperPositionDTO]


class LookbackGroupStat(BaseModel):
    """一個分組（風格標籤 / 分數帶）的歷史績效。"""

    key: str
    n: int
    hit_count: int
    hit_rate: float | None
    avg_return_pct: float | None   # 至今報酬（與回看口徑一致：隔日高錨）
    avg_mfe_pct: float | None
    avg_mae_pct: float | None


class LookbackStatsResponse(BaseModel):
    since: date | None
    today_date: date | None
    min_age_days: int              # 樣本至少距今 N 個交易日（避免太新未走完）
    by_style: list[LookbackGroupStat]
    by_score_bin: list[LookbackGroupStat]


class SensitivityPoint(BaseModel):
    """累積門檻（prob ≥ X）的成效。

    days 才是有效樣本數：同一天的個股命中高度相關，n=22 若只落在 2 天，統計上就是
    2 個觀測。故 reliable 以 days 為主判準，前端據此把不可信的列灰化。
    """

    prob_min: float
    n: int                     # 個股樣本數（同日高度相關，勿當獨立觀測）
    days: int                  # 有貨的進場日數 ← 有效樣本數
    day_cover: float | None    # 有貨日 / 全部進場日（揭露「集中在少數幾天」）
    avg_daily_n: float | None  # 平均每日推薦檔數（分母=全部進場日）
    hit_count: int
    hit_rate: float | None     # 個股級碰到率
    hit_rate_lo: float | None  # Wilson 95% 下界（以 days 為有效 n 調整）
    hit_rate_hi: float | None
    day_hit_rate: float | None  # 日層級（每日一觀測取平均，不被大日子灌權重）
    lift: float | None          # vs 無門檻基準的倍數
    avg_return_pct: float | None       # 隔日高錨（保守／最壞追高）
    avg_return_open_pct: float | None  # 隔日開盤錨（貼近實務）
    avg_mfe_pct: float | None
    avg_mae_pct: float | None
    reliable: bool


class CalibrationBin(BaseModel):
    """非累積分箱：預測機率 vs 實現碰到率，看查表準不準（累積門檻看不出來）。"""

    lo: float
    hi: float
    n: int
    days: int
    pred_avg: float             # 該箱預測機率均值
    hit_rate: float | None      # 該箱實現碰到率
    hit_rate_lo: float | None
    hit_rate_hi: float | None
    err_pp: float | None        # 實現 − 預測（負＝機率高估）
    reliable: bool


class SensitivityResponse(BaseModel):
    since: date | None
    today_date: date | None
    min_age_days: int
    entry_days: int                 # 統計涵蓋的進場日總數
    base_hit_rate: float | None     # 不設門檻的碰到率（lift 的分母）
    points: list[SensitivityPoint]
    calibration: list[CalibrationBin]
    note: str


# ── 回測實驗室（spec 2026-08-20-backtest-lab）──


class FieldInfo(BaseModel):
    key: str
    label: str
    group: str
    unit: str


class ConditionDTO(BaseModel):
    field: str
    op: Literal["gt", "lt", "gte", "lte", "streak_gt", "streak_lt"]
    value: float | dict  # streak op 用 {n, threshold}


class StrategyDTO(BaseModel):
    id: int
    name: str
    conditions: list[ConditionDTO]
    sort_field: str
    sort_desc: bool
    top_n: int
    target_pct: float
    horizon_days: int
    stop_pct: float | None
    is_active: bool


class StrategyCreate(BaseModel):
    name: str = "我的策略"
    conditions: list[ConditionDTO] = []
    sort_field: str = "turnover"
    sort_desc: bool = True
    top_n: int = 30
    target_pct: float = 10.0
    horizon_days: int = 10
    stop_pct: float | None = None


class StrategyPatch(BaseModel):
    name: str | None = None
    conditions: list[ConditionDTO] | None = None
    sort_field: str | None = None
    sort_desc: bool | None = None
    top_n: int | None = None
    target_pct: float | None = None
    horizon_days: int | None = None
    stop_pct: float | None = None
    clear_stop: bool = False  # PATCH 語意下 null 無法表達「清掉停損」，用旗標


class BacktestRequest(BaseModel):
    start: date
    end: date


class BacktestMonthly(BaseModel):
    month: str
    samples: int
    hits: int


class BacktestDetail(BaseModel):
    date: str
    stock_id: str
    name: str
    entry: float
    hit: bool
    stopped: bool
    max_gain_pct: float
    max_dd_pct: float


class BacktestResponse(BaseModel):
    samples: int
    hits: int
    hit_rate: float | None
    base_rate: float | None
    lift: float | None
    avg_max_drawdown: float | None
    monthly: list[BacktestMonthly]
    recent: list[BacktestDetail]
    warn_loose: bool
    signal_days: int


class StrategyDailyItem(BaseModel):
    stock_id: str
    name: str
    close: float | None
    sort_value: float | None


class StrategyDailyResponse(BaseModel):
    strategy: StrategyDTO | None
    date: str | None
    items: list[StrategyDailyItem]
