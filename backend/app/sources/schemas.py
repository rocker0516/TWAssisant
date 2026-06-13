"""來源統一輸出欄位（架構②：固定欄位 DataFrame，對齊①直接落庫）。

每個來源實作把各自 API 的欄位 rename 成這裡的標準欄位，下游（FetchStep）
不需認得各家 API 格式。欄位名對齊 storage.models，落庫時直接轉 dict upsert。
"""

from __future__ import annotations

# 主檔（universe）。sector_name 由 FetchStep 解析成 sector_id。
UNIVERSE_COLS = ["id", "name", "industry_category", "market", "listed_date", "is_etf", "sector_name"]

PRICE_COLS = ["stock_id", "date", "open", "high", "low", "close", "volume", "turnover"]

INSTITUTIONAL_COLS = ["stock_id", "date", "foreign_net", "trust_net", "dealer_net", "total_net"]

MARGIN_COLS = ["stock_id", "date", "margin_balance", "margin_change", "short_balance", "short_change"]

REVENUE_COLS = ["stock_id", "year", "month", "revenue", "yoy", "mom"]

FINANCIAL_COLS = [
    "stock_id", "year", "quarter", "eps", "revenue",
    "gross_margin", "op_margin", "net_margin", "roe",
]

VALUATION_COLS = ["stock_id", "date", "pe", "pb", "dividend_yield"]

EVENT_COLS = ["stock_id", "date", "category", "title", "summary", "is_risk", "source", "url"]

ETF_PROFILE_COLS = ["stock_id", "fund_type", "track_index", "has_foreign", "units", "etf_listed_date"]
