"""資料庫 schema — 六群表（架構①資料層定稿）。

A 主檔        : Sector, Stock
B 行情運算    : DailyPrice, Indicator            （PK = stock_id + date）
C 籌碼基本面  : Institutional, Margin, RevenueMonthly, FinancialQuarter, Valuation
D 類股        : SectorDaily
E 引擎結果    : Score（雙軌各一列）, Event, SignalLog   ← 前端只讀此群
F 使用者      : Holding, Transaction, Watchlist, WatchlistItem, Setting, LlmCache
排程 log      : PipelineRun

要點：
  - 持股成本不存欄位，由 Transaction 重算均價（支援加碼/分批賣）。
  - 來源抓進 A~C；引擎算 B(indicators)/D/E；使用者操作寫 F。
"""

from __future__ import annotations

from datetime import date as date_, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

# ─────────────────────────── A 主檔 ───────────────────────────


class Sector(Base):
    """官方產業類股（約 28 類）。"""

    __tablename__ = "sectors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, index=True)

    stocks: Mapped[list["Stock"]] = relationship(back_populates="sector")


class Stock(Base):
    """個股主檔。id = 股票代號（如 '2330'）。"""

    __tablename__ = "stocks"

    id: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(50), index=True)
    sector_id: Mapped[int | None] = mapped_column(ForeignKey("sectors.id"), nullable=True)
    market: Mapped[str | None] = mapped_column(String(10))  # 上市 / 上櫃
    industry_category: Mapped[str | None] = mapped_column(String(50))  # 來源原始產業字串
    is_etf: Mapped[bool] = mapped_column(Boolean, default=False)
    listed_date: Mapped[date_ | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    sector: Mapped[Sector | None] = relationship(back_populates="stocks")


# ─────────────────────────── B 行情運算 ───────────────────────────


class DailyPrice(Base):
    """日 K 行情。PK = (stock_id, date)。"""

    __tablename__ = "daily_prices"

    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    date: Mapped[date_] = mapped_column(Date, primary_key=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(Integer)  # 成交股數
    turnover: Mapped[float | None] = mapped_column(Float)  # 成交金額


class Indicator(Base):
    """技術指標（P1 IndicatorEngine 算）。PK = (stock_id, date)。"""

    __tablename__ = "indicators"

    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    date: Mapped[date_] = mapped_column(Date, primary_key=True)
    ma5: Mapped[float | None] = mapped_column(Float)
    ma10: Mapped[float | None] = mapped_column(Float)
    ma20: Mapped[float | None] = mapped_column(Float)
    ma60: Mapped[float | None] = mapped_column(Float)
    ma120: Mapped[float | None] = mapped_column(Float)  # 半年線
    ma240: Mapped[float | None] = mapped_column(Float)  # 年線
    vol_ma5: Mapped[float | None] = mapped_column(Float)
    vol_ma20: Mapped[float | None] = mapped_column(Float)
    kd_k: Mapped[float | None] = mapped_column(Float)
    kd_d: Mapped[float | None] = mapped_column(Float)
    macd: Mapped[float | None] = mapped_column(Float)
    macd_signal: Mapped[float | None] = mapped_column(Float)
    macd_hist: Mapped[float | None] = mapped_column(Float)
    atr14: Mapped[float | None] = mapped_column(Float)
    bias_20: Mapped[float | None] = mapped_column(Float)
    bias_60: Mapped[float | None] = mapped_column(Float)


# ─────────────────────────── C 籌碼 / 基本面 ───────────────────────────


class Institutional(Base):
    """三大法人買賣超（張）。PK = (stock_id, date)。"""

    __tablename__ = "institutional"

    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    date: Mapped[date_] = mapped_column(Date, primary_key=True)
    foreign_net: Mapped[int | None] = mapped_column(Integer)  # 外資
    trust_net: Mapped[int | None] = mapped_column(Integer)  # 投信
    dealer_net: Mapped[int | None] = mapped_column(Integer)  # 自營商
    total_net: Mapped[int | None] = mapped_column(Integer)


class Margin(Base):
    """融資融券餘額（張）。PK = (stock_id, date)。"""

    __tablename__ = "margin"

    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    date: Mapped[date_] = mapped_column(Date, primary_key=True)
    margin_balance: Mapped[int | None] = mapped_column(Integer)  # 融資餘額
    margin_change: Mapped[int | None] = mapped_column(Integer)
    short_balance: Mapped[int | None] = mapped_column(Integer)  # 融券餘額
    short_change: Mapped[int | None] = mapped_column(Integer)


class ShareholdingDistribution(Base):
    """集保戶股權分散（TDCC 開放資料消化後）。PK = (stock_id, date)。

    占比皆為「占集保庫存」%。來源僅回最新週快照，靠每週 upsert 累積歷史；
    趨勢（大戶占比變化）由評分端取近數週序列計算。
    """

    __tablename__ = "shareholding"

    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    date: Mapped[date_] = mapped_column(Date, primary_key=True)
    big_pct: Mapped[float | None] = mapped_column(Float)        # 大戶（≥400 張）占比
    over1000_pct: Mapped[float | None] = mapped_column(Float)   # 千張大戶（≥1000 張）占比
    small_pct: Mapped[float | None] = mapped_column(Float)      # 散戶（<10 張）占比
    holders: Mapped[int | None] = mapped_column(Integer)        # 總股東人數
    avg_lots: Mapped[float | None] = mapped_column(Float)       # 平均每人持股（張）


class ShortLending(Base):
    """借券賣出餘額（TWSE TWT93U / TPEX margin/sbl，信用額度總量管制餘額表借券欄）。

    PK = (stock_id, date)。單位＝張（原始為股，/1000）。融券已在 margin 表；
    此表補外資主要放空管道「借券賣出」，軋空軸（券資比）才完整。
    """

    __tablename__ = "short_lending"

    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    date: Mapped[date_] = mapped_column(Date, primary_key=True)
    sbl_balance: Mapped[int | None] = mapped_column(Integer)  # 借券賣出當日餘額（張）
    sbl_change: Mapped[int | None] = mapped_column(Integer)   # 當日增減（張）
    sbl_sell: Mapped[int | None] = mapped_column(Integer)     # 當日借券賣出（張）


class DayTrading(Base):
    """個股現股當沖統計（TWSE TWTB4U，上市限定；上櫃無個股級開放端點）。

    PK = (stock_id, date)。當沖占比（dt_volume / 當日成交量）由查詢端 join
    daily_prices 計算，不落欄位。
    """

    __tablename__ = "day_trading"

    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    date: Mapped[date_] = mapped_column(Date, primary_key=True)
    dt_volume: Mapped[int | None] = mapped_column(Integer)    # 當沖成交股數→張
    dt_buy_value: Mapped[float | None] = mapped_column(Float)  # 當沖買進金額（元）
    dt_sell_value: Mapped[float | None] = mapped_column(Float)  # 當沖賣出金額（元）


class InsiderHolding(Base):
    """董監事持股彙總（TWSE/TPEX OpenAPI t187ap11 月快照，逐公司加總）。

    PK = (stock_id, year, month)。看趨勢用（董監持股月變化、設質比率變化），
    絕對值受發行股數影響不跨股比較。
    """

    __tablename__ = "insider_holding"

    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    month: Mapped[int] = mapped_column(Integer, primary_key=True)
    director_shares: Mapped[float | None] = mapped_column(Float)  # 董監目前持股合計（股）
    pledge_pct: Mapped[float | None] = mapped_column(Float)       # 設質占董監持股 %
    positions: Mapped[int | None] = mapped_column(Integer)        # 申報席次數


class RevenueMonthly(Base):
    """月營收。PK = (stock_id, year, month)。"""

    __tablename__ = "revenue_monthly"

    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    month: Mapped[int] = mapped_column(Integer, primary_key=True)
    revenue: Mapped[float | None] = mapped_column(Float)  # 千元
    yoy: Mapped[float | None] = mapped_column(Float)  # 年增 %
    mom: Mapped[float | None] = mapped_column(Float)  # 月增 %


class FinancialQuarter(Base):
    """季財報。PK = (stock_id, year, quarter)。"""

    __tablename__ = "financials_quarterly"

    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    quarter: Mapped[int] = mapped_column(Integer, primary_key=True)
    eps: Mapped[float | None] = mapped_column(Float)
    revenue: Mapped[float | None] = mapped_column(Float)
    gross_margin: Mapped[float | None] = mapped_column(Float)
    op_margin: Mapped[float | None] = mapped_column(Float)
    net_margin: Mapped[float | None] = mapped_column(Float)
    roe: Mapped[float | None] = mapped_column(Float)


class AttentionListing(Base):
    """注意股（notice）／處置股（punish）名單（TWSE + TPEX 官方公告）。

    使用者實證觀點：被列入者常帶上漲動能（熱錢聚集的果），故此表同時供
    「動能標籤驗證」與個股頁狀態徽章；PK = (stock_id, date, kind)。
    """

    __tablename__ = "attention_listings"

    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    date: Mapped[date_] = mapped_column(Date, primary_key=True)      # 公告日
    kind: Mapped[str] = mapped_column(String(8), primary_key=True)   # notice / punish
    times: Mapped[int | None] = mapped_column(Integer)               # 累計次數
    begin_date: Mapped[date_ | None] = mapped_column(Date)           # 處置起（notice 為 None）
    end_date: Mapped[date_ | None] = mapped_column(Date)             # 處置迄
    reason: Mapped[str | None] = mapped_column(String(200))          # 條款/原因摘要


class IndexConstituentEvent(Base):
    """TIP 指數定審成分股納入/刪除事件（sources/tip_index.py 解析技術通知 PDF）。

    僅涵蓋台灣指數公司自編指數（00919/00929/00932 等追蹤標的）；
    0050/0056（富時合編）與 00878（MSCI）不在此源。
    """

    __tablename__ = "index_constituent_events"

    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    index_name: Mapped[str] = mapped_column(String(80), primary_key=True)
    announce_date: Mapped[date_] = mapped_column(Date, primary_key=True)
    action: Mapped[str] = mapped_column(String(8), primary_key=True)  # add / remove
    effective_date: Mapped[date_ | None] = mapped_column(Date)
    title: Mapped[str | None] = mapped_column(String(120))


class FinancialStatementQuarter(Base):
    """資產負債表（期末時點）＋現金流量表（單季化）關鍵科目。

    來源 FinMind TaiwanStockBalanceSheet / TaiwanStockCashFlowsStatement（逐檔），
    採「個股頁首讀懶抓＋30 天過期重抓」快取，不做全市場回補。金額單位：元。
    """

    __tablename__ = "financial_statements"

    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    quarter: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 資產負債表（期末餘額）
    cash: Mapped[float | None] = mapped_column(Float)             # 現金及約當現金
    current_assets: Mapped[float | None] = mapped_column(Float)   # 流動資產合計
    total_assets: Mapped[float | None] = mapped_column(Float)     # 資產總額
    current_liab: Mapped[float | None] = mapped_column(Float)     # 流動負債合計
    total_liab: Mapped[float | None] = mapped_column(Float)       # 負債總額
    equity: Mapped[float | None] = mapped_column(Float)           # 權益總額
    inventories: Mapped[float | None] = mapped_column(Float)      # 存貨
    receivables: Mapped[float | None] = mapped_column(Float)      # 應收帳款淨額
    # 現金流量表（累計制已差分為單季）
    op_cf: Mapped[float | None] = mapped_column(Float)    # 營業活動現金流
    inv_cf: Mapped[float | None] = mapped_column(Float)   # 投資活動現金流
    fin_cf: Mapped[float | None] = mapped_column(Float)   # 籌資活動現金流
    capex: Mapped[float | None] = mapped_column(Float)    # 取得不動產廠房設備（資本支出）
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)


class EtfIndexEvent(Base):
    """TIP 指數定期審核成分股異動（ETF 成分效應事件研究用）。

    來源 taiwanindex.com.tw 技術通知 PDF（sources/tip_index.py）。
    涵蓋 TIP 自編指數（00878/00919/00929/00932/00940 等追蹤指數）；0050/0056 屬富時合編不在內。
    """

    __tablename__ = "etf_index_events"

    file_id: Mapped[int] = mapped_column(Integer, primary_key=True)   # 技術通知檔案 id
    stock_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    action: Mapped[str] = mapped_column(String(6), primary_key=True)  # add / remove
    index_name: Mapped[str | None] = mapped_column(String(80))
    stock_name: Mapped[str | None] = mapped_column(String(30))
    announce_date: Mapped[date_ | None] = mapped_column(Date, index=True)  # 檔案日期（公告日）
    effective_date: Mapped[date_ | None] = mapped_column(Date, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Valuation(Base):
    """估值（本益比 / 股價淨值比 / 殖利率）。PK = (stock_id, date)。"""

    __tablename__ = "valuation"

    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    date: Mapped[date_] = mapped_column(Date, primary_key=True)
    pe: Mapped[float | None] = mapped_column(Float)
    pb: Mapped[float | None] = mapped_column(Float)
    dividend_yield: Mapped[float | None] = mapped_column(Float)


class EtfProfile(Base):
    """ETF 身分資料（TWSE t187ap47_L 基金基本資料彙總表）。PK = stock_id。

    個股不報的欄位（月營收/本益比）對 ETF 無意義；改以此表的身分資料補上：
    基金類型、追蹤指數、是否含國外成分、發行單位數（× 收盤價 ≈ 規模）。
    """

    __tablename__ = "etf_profile"

    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    fund_type: Mapped[str | None] = mapped_column(String(60))   # 基金類型（股票/債券/主動式…）
    track_index: Mapped[str | None] = mapped_column(String(80))  # 追蹤指數（主動式/不適用→None）
    has_foreign: Mapped[bool | None] = mapped_column(Boolean)   # 是否含國外成分股
    units: Mapped[float | None] = mapped_column(Float)          # 發行單位數
    etf_listed_date: Mapped[date_ | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CompanyProfile(Base):
    """公司基本資料（TWSE t187ap03_L / TPEx mopsfin_t187ap03_O 全快照）。PK = stock_id。

    董監層資訊 + 股本/發行股數（× 收盤價 ≈ 市值）。ETF 無此資料。
    """

    __tablename__ = "company_profile"

    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    chairman: Mapped[str | None] = mapped_column(String(50))       # 董事長
    president: Mapped[str | None] = mapped_column(String(50))      # 總經理
    capital: Mapped[float | None] = mapped_column(Float)           # 實收資本額（元）
    issued_shares: Mapped[float | None] = mapped_column(Float)     # 已發行普通股數（股）
    established_date: Mapped[date_ | None] = mapped_column(Date)   # 成立日期
    listed_date: Mapped[date_ | None] = mapped_column(Date)        # 上市/上櫃日期（stocks.listed_date 為來源資料日不可靠）
    website: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class IndustryChainMember(Base):
    """產業價值鏈成員（ic.tpex.org.tw 官方平台，全快照）。

    一公司可屬多鏈多節點（如鴻海）。node_name = 最細分類（子節點；無子節點時＝主節點），
    作為個股「業務標籤」與類股內細分依據。
    """

    __tablename__ = "industry_chain_members"

    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    chain_id: Mapped[str] = mapped_column(String(8), primary_key=True)   # 如 D000
    node_id: Mapped[str] = mapped_column(String(8), primary_key=True)    # 如 D330
    chain_name: Mapped[str] = mapped_column(String(30))                  # 半導體
    stream: Mapped[str | None] = mapped_column(String(6))                # 上游/中游/下游
    main_node: Mapped[str | None] = mapped_column(String(40))            # IC/晶圓製造
    node_name: Mapped[str | None] = mapped_column(String(40), index=True)  # 晶圓製造
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Dividend(Base):
    """股利政策（FinMind TaiwanStockDividend，個股頁首讀懶抓快取）。

    PK = (stock_id, period)。period = 股利所屬期間字串（如 '114年' / '114年第4季'）。
    cash/stock 單位＝元/股（盈餘+公積加總）。
    """

    __tablename__ = "dividends"

    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    period: Mapped[str] = mapped_column(String(20), primary_key=True)
    cash: Mapped[float | None] = mapped_column(Float)              # 現金股利（元/股）
    stock: Mapped[float | None] = mapped_column(Float)             # 股票股利（元/股）
    cash_ex_date: Mapped[date_ | None] = mapped_column(Date)       # 除息交易日
    pay_date: Mapped[date_ | None] = mapped_column(Date)           # 現金發放日
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class InstitutionalMarketTotal(Base):
    """全市場三大法人買賣超總表（TWSE BFI82U）。PK = date。單位＝億元。

    與個股 institutional（張）不同口徑：這是整個市場的法人資金流向，用來看大盤方向、
    法人買超循環處於哪一段。foreign/trust/dealer 三欄可個別看（外資/投信/自營常分歧）。
    """

    __tablename__ = "institutional_market_total"

    date: Mapped[date_] = mapped_column(Date, primary_key=True)
    foreign_net: Mapped[float | None] = mapped_column(Float)  # 外資（含外資自營商），億元
    trust_net: Mapped[float | None] = mapped_column(Float)    # 投信，億元
    dealer_net: Mapped[float | None] = mapped_column(Float)   # 自營商（自行+避險），億元
    total_net: Mapped[float | None] = mapped_column(Float)    # 三大法人合計，億元


class MarketDerivatives(Base):
    """期貨籌碼市場級（TAIFEX 期交所）。PK = date。

    台指期三大法人未平倉淨口數 + 選擇權 P/C ratio。與現貨 institutional_market_total
    對照看「外資現貨期貨背離」（現貨買超但期貨空單增＝對沖非看多）。
    定位＝觀察儀表；要折進 regime 閘門或評分需先過回測。
    """

    __tablename__ = "market_derivatives"

    date: Mapped[date_] = mapped_column(Date, primary_key=True)
    tx_foreign_oi_net: Mapped[int | None] = mapped_column(Integer)  # 外資台指期未平倉淨口數
    tx_trust_oi_net: Mapped[int | None] = mapped_column(Integer)    # 投信
    tx_dealer_oi_net: Mapped[int | None] = mapped_column(Integer)   # 自營商
    pc_vol_ratio: Mapped[float | None] = mapped_column(Float)       # 買賣權成交量比率 %
    pc_oi_ratio: Mapped[float | None] = mapped_column(Float)        # 買賣權未平倉量比率 %


class MarketIndex(Base):
    """加權指數日線（TWSE 發行量加權股價指數收盤）。PK = date。疊圖/量化關係對照用。"""

    __tablename__ = "market_index"

    date: Mapped[date_] = mapped_column(Date, primary_key=True)
    close: Mapped[float | None] = mapped_column(Float)


# ─────────────────────────── D 類股 ───────────────────────────


class SectorDaily(Base):
    """類股每日方向（P3 SectorEngine 算）。PK = (sector_id, date)。"""

    __tablename__ = "sector_daily"

    sector_id: Mapped[int] = mapped_column(ForeignKey("sectors.id"), primary_key=True)
    date: Mapped[date_] = mapped_column(Date, primary_key=True)
    strength_score: Mapped[float | None] = mapped_column(Float)
    trend_short: Mapped[str | None] = mapped_column(String(10))  # 偏多/中性/偏空
    trend_long: Mapped[str | None] = mapped_column(String(10))
    rotation_stage: Mapped[str | None] = mapped_column(String(20))  # 起漲/主升/高檔鈍化/轉弱/破底
    momentum_5: Mapped[float | None] = mapped_column(Float)
    momentum_20: Mapped[float | None] = mapped_column(Float)
    foreign_net: Mapped[int | None] = mapped_column(Integer)  # 近5日法人淨買超（張）
    # 三維度子分數 + 熱力圖/排行用
    dim_momentum: Mapped[float | None] = mapped_column(Float)
    dim_fund: Mapped[float | None] = mapped_column(Float)
    dim_tech: Mapped[float | None] = mapped_column(Float)
    turnover_share: Mapped[float | None] = mapped_column(Float)  # 成交佔比 %（熱力圖大小）
    above_ma20: Mapped[float | None] = mapped_column(Float)  # 站上月線家數比
    constituents: Mapped[int | None] = mapped_column(Integer)


# ─────────────────────────── E 引擎結果（前端只讀）───────────────────────────


class TargetPrice(Base):
    """FactSet 共識目標價（鉅亨 tw_forecast 快報）。PK=(stock_id, date)，一日一筆取最新。"""

    __tablename__ = "target_prices"

    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    date: Mapped[date_] = mapped_column(Date, primary_key=True)

    target_price: Mapped[float] = mapped_column(Float)  # 共識中位數
    prev_target: Mapped[float | None] = mapped_column(Float)
    direction: Mapped[str] = mapped_column(String(5), default="new")  # up/down/flat/new
    target_high: Mapped[float | None] = mapped_column(Float)
    target_low: Mapped[float | None] = mapped_column(Float)
    analyst_count: Mapped[int | None] = mapped_column(Integer)
    rating_bull: Mapped[int | None] = mapped_column(Integer)
    rating_neutral: Mapped[int | None] = mapped_column(Integer)
    rating_bear: Mapped[int | None] = mapped_column(Integer)
    eps_est: Mapped[float | None] = mapped_column(Float)
    news_id: Mapped[int | None] = mapped_column(Integer)  # 去重／同日取 news_id 較大者
    title: Mapped[str | None] = mapped_column(String(200))


class Score(Base):
    """雙軌評分結果。PK = (stock_id, date, track)，波段/長線各一列。"""

    __tablename__ = "scores"

    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    date: Mapped[date_] = mapped_column(Date, primary_key=True)
    track: Mapped[str] = mapped_column(String(10), primary_key=True)  # wave / long

    passed_filter: Mapped[bool] = mapped_column(Boolean, default=False)  # 過任一風格硬篩（波段=遲滯後狀態）
    strict_filter: Mapped[bool | None] = mapped_column(Boolean)  # 當日原始硬篩（無遲滯；狀態機隔日回看用）
    passed: Mapped[bool] = mapped_column(Boolean, default=False)  # 過硬篩 + 門檻
    passed_styles: Mapped[list | None] = mapped_column(JSON)  # 通過哪些進場風格硬篩 ["breakout","pullback"]
    total_score: Mapped[float | None] = mapped_column(Float)  # 主風格(波段=breakout)總分
    style_totals: Mapped[dict | None] = mapped_column(JSON)  # 各風格加權總分 {"breakout":..,"pullback":..}
    style_coverage: Mapped[dict | None] = mapped_column(JSON)  # 各風格完整度（只看該風格押注維度）
    style_confidence: Mapped[dict | None] = mapped_column(JSON)  # 各風格可信度（只看該風格押注維度）
    style_stability: Mapped[dict | None] = mapped_column(JSON)  # 各風格穩定度（各用自己風格總分歷史）
    sub_scores: Mapped[dict | None] = mapped_column(JSON)  # 5 大類細項（缺料維度不入列）
    sector_adjust: Mapped[float | None] = mapped_column(Float)  # 類股修正分
    coverage: Mapped[float | None] = mapped_column(Float)  # 有資料維度占比 0~1（缺料偵測）
    confidence: Mapped[float | None] = mapped_column(Float)  # 分數可信度 0~100（完整度×共識度×穩定度）
    stability: Mapped[float | None] = mapped_column(Float)  # 近期總分穩定度 0.6~1（L3，史料不足=1）

    buy_low: Mapped[float | None] = mapped_column(Float)
    buy_high: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    loss_pct: Mapped[float | None] = mapped_column(Float)

    reasons: Mapped[list | None] = mapped_column(JSON)  # 理由 chips
    details: Mapped[list | None] = mapped_column(JSON)  # 展開區：各面向 {category, score, evidence}


class Event(Base):
    """重訊 / 新聞事件（P4 NewsEngine）。"""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_id: Mapped[str | None] = mapped_column(ForeignKey("stocks.id"), nullable=True, index=True)
    date: Mapped[date_] = mapped_column(Date, index=True)
    category: Mapped[str | None] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    is_risk: Mapped[bool] = mapped_column(Boolean, default=False)  # 重大利空
    source: Mapped[str | None] = mapped_column(String(30))
    url: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("stock_id", "date", "title", name="uq_event"),)


# ─────────────────────────── F 使用者 ───────────────────────────


class User(Base):
    """帳號（分層設計第 6 節）。tier＝付費層級（free/pro），role＝權限（user/admin）。

    tier 與 role 分開存的理由：Admin 也可能想看 Free 視角除錯；付費狀態與
    管理權限是兩個正交的事實，混成一欄日後必然要拆。

    session_version：可撤銷 session 的機制（設計 7.2-2）。token 內嵌簽發當下的
    版本號，改密碼／登出全部裝置時 +1，舊 token 立即全部失效——不需要 server
    端存 token 名單。

    failed_logins / locked_until：per-account 鎖定落 DB（設計 7.2-3）。
    in-memory per-IP 鎖擋不住分散 IP、重啟即清空，只能當第一道。
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    tier: Mapped[str] = mapped_column(String(10), default="free")   # free / pro
    role: Mapped[str] = mapped_column(String(10), default="user")   # user / admin
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class EmailVerification(Base):
    """Email 驗證 token（一次性、有時效）。驗證成功即刪列。"""

    __tablename__ = "email_verifications"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class PasswordReset(Base):
    """密碼重設 token。used_at 留痕而非刪列——重設是安全敏感事件，要能回查。"""

    __tablename__ = "password_resets"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)


class Holding(Base):
    """持股。成本不存欄位，由 transactions 重算均價。"""

    __tablename__ = "holdings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # nullable：舊資料在遷移補值前短暫為 NULL；所有查詢一律經 UserData（強制 user_id）
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), index=True)
    track: Mapped[str] = mapped_column(String(10))  # wave / long
    status: Mapped[str] = mapped_column(String(10), default="open")  # open / closed
    opened_date: Mapped[date_ | None] = mapped_column(Date)
    closed_date: Mapped[date_ | None] = mapped_column(Date)

    highest_price: Mapped[float | None] = mapped_column(Float)  # 持有期間最高價（日更，移動停利用）
    realized_pnl: Mapped[float | None] = mapped_column(Float)  # 賣到 0 張時結算

    # 出場參數覆寫（None = 用軌道預設）
    stop_loss_override: Mapped[float | None] = mapped_column(Float)
    trail_trigger_override: Mapped[float | None] = mapped_column(Float)
    trail_pullback_override: Mapped[float | None] = mapped_column(Float)

    # 進場理由快照：建倉當下該軌最新 Score 的凍結副本（date/total_score/passed_filter/
    # reasons/buy_low/buy_high/stop_loss/close）。之後與最新分數對照＝論點是否還成立。
    entry_snapshot: Mapped[dict | None] = mapped_column(JSON)

    note: Mapped[str | None] = mapped_column(Text)

    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="holding", cascade="all, delete-orphan"
    )


class Transaction(Base):
    """交易明細（買 / 加碼 / 賣）。支援加碼算均價、分批賣。"""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    holding_id: Mapped[int] = mapped_column(ForeignKey("holdings.id"), index=True)
    type: Mapped[str] = mapped_column(String(10))  # buy / add / sell
    date: Mapped[date_] = mapped_column(Date)
    price: Mapped[float] = mapped_column(Float)
    shares: Mapped[int] = mapped_column(Integer)  # 張
    fee: Mapped[float | None] = mapped_column(Float)
    tax: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)

    holding: Mapped[Holding] = relationship(back_populates="transactions")


class Watchlist(Base):
    """觀察清單（可多組命名）。"""

    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(50))
    created_date: Mapped[date_ | None] = mapped_column(Date, server_default=func.current_date())

    items: Mapped[list["WatchlistItem"]] = relationship(
        back_populates="watchlist", cascade="all, delete-orphan"
    )


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    watchlist_id: Mapped[int] = mapped_column(ForeignKey("watchlists.id"), index=True)
    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"))
    added_price: Mapped[float | None] = mapped_column(Float)
    target_price: Mapped[float | None] = mapped_column(Float)
    added_date: Mapped[date_ | None] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)

    watchlist: Mapped[Watchlist] = relationship(back_populates="items")


class Setting(Base):
    """key-value JSON 設定（配分 / 門檻 / 版面 / 通知…）。"""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)


class LlmCache(Base):
    """每日盤後批次 LLM 解讀快取，白天讀此。"""

    __tablename__ = "llm_cache"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)  # 如 sector_dir:23:2026-06-06
    kind: Mapped[str | None] = mapped_column(String(40))
    ref_id: Mapped[str | None] = mapped_column(String(40))
    date: Mapped[date_ | None] = mapped_column(Date, index=True)
    content: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CornerSignal(Base):
    """高確信角落影子軌訊號（實驗）。角落定義=data/corners.json（挖掘凍結產物）。

    純標籤層：不影響排序/推薦；累積 forward 驗證用（30 日後可對照 daily_prices
    算「隔日高錨摸 +10%」實際命中 vs 各角落歷史帶）。
    """

    __tablename__ = "corner_signals"

    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    date: Mapped[date_] = mapped_column(Date, primary_key=True, index=True)
    corner_id: Mapped[str] = mapped_column(String(8), primary_key=True)  # C01~C30
    close: Mapped[float | None] = mapped_column(Float)  # 訊號日收盤（回顧展示用）


class SignalLog(Base):
    """全站狀態變化事件（append-only）。

    與 `events` 的分野：`events` 是**外部來的消息**（重訊/新聞，帶 url/source/is_risk）；
    這裡記的是**本站自己算出來的東西發生了什麼變化**。名字不叫 `events` 是因為那個
    名字已經被前者佔走——泛用名詞當表名，第二種事件出現時必然撞名。

    為什麼要有這張表：通知、每日盤後、戰績三個功能要的都是「變化」而非「狀態」。
    沒有它，三者會各自寫一套「比對昨天和今天」的邏輯，三份都會有各自的 bug。

    append-only：只 insert 不 update、不 delete。pipeline 重跑靠 unique 約束去重
    （`on_conflict_do_nothing`），故整條可重跑的性質不變，但**已寫下的紀錄不會被改寫**
    ——這正是公開戰績可驗證的前提（scores 是 upsert 覆寫，重跑會改寫歷史，不能當戰績依據）。

    date vs created_at vs backfilled：`date` 是事件所屬的交易日，`created_at` 是實際
    寫入時間，回填歷史時兩者相差數月。`backfilled` 明確標記「這筆是事後從 scores 補的，
    不是當天寫下的」——公開戰績只有 backfilled=False 的部分能宣稱「我們事前就說了」，
    回填段落只能當背景參考。不用 created_at 反推是因為那個推論很脆弱（補跑一天前的
    缺口也會讓兩者不同），而這裡不能有模稜兩可。

    刻意不設通用的 ref/payload_key 欄位：kind 各自需要什麼鍵就開什麼欄位。
    通用欄位在第三種 kind 出現時會變成「這一列的 ref 是什麼意思要看 kind」，
    是泛用表名的同一個陷阱換一層。
    """

    __tablename__ = "signal_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date_] = mapped_column(Date, index=True)      # 事件所屬交易日
    kind: Mapped[str] = mapped_column(String(24), index=True)  # listed / delisted
    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), index=True)
    track: Mapped[str] = mapped_column(String(10))             # wave / long
    payload: Mapped[dict | None] = mapped_column(JSON)         # 事件當下的快照（見 engines/signal_log.py）
    backfilled: Mapped[bool] = mapped_column(Boolean, default=False)  # 事後補的，非當日寫下
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("date", "kind", "stock_id", "track", name="uq_signal_log"),
    )


# ─────────────────────────── 排程 log ───────────────────────────


class PipelineRun(Base):
    """每日 pipeline 執行紀錄（設定頁顯示 / catch-up 判斷資料是否齊）。"""

    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trading_date: Mapped[date_ | None] = mapped_column(Date, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running/success/failed
    steps: Mapped[list | None] = mapped_column(JSON)  # 各 step 結果
    error: Mapped[str | None] = mapped_column(Text)
