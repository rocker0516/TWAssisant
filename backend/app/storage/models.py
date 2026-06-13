"""資料庫 schema — 六群表（架構①資料層定稿）。

A 主檔        : Sector, Stock
B 行情運算    : DailyPrice, Indicator            （PK = stock_id + date）
C 籌碼基本面  : Institutional, Margin, RevenueMonthly, FinancialQuarter, Valuation
D 類股        : SectorDaily
E 引擎結果    : Score（雙軌各一列）, Event          ← 前端只讀此群
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


class Score(Base):
    """雙軌評分結果。PK = (stock_id, date, track)，波段/長線各一列。"""

    __tablename__ = "scores"

    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    date: Mapped[date_] = mapped_column(Date, primary_key=True)
    track: Mapped[str] = mapped_column(String(10), primary_key=True)  # wave / long

    passed_filter: Mapped[bool] = mapped_column(Boolean, default=False)  # 過硬篩
    passed: Mapped[bool] = mapped_column(Boolean, default=False)  # 過硬篩 + 門檻
    total_score: Mapped[float | None] = mapped_column(Float)
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


class Holding(Base):
    """持股。成本不存欄位，由 transactions 重算均價。"""

    __tablename__ = "holdings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
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

    note: Mapped[str | None] = mapped_column(Text)

    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="holding", cascade="all, delete-orphan"
    )


class Transaction(Base):
    """交易明細（買 / 加碼 / 賣）。支援加碼算均價、分批賣。"""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
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
    name: Mapped[str] = mapped_column(String(50))
    created_date: Mapped[date_ | None] = mapped_column(Date, server_default=func.current_date())

    items: Mapped[list["WatchlistItem"]] = relationship(
        back_populates="watchlist", cascade="all, delete-orphan"
    )


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
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
