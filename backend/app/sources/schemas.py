"""來源統一輸出欄位（架構②：固定欄位 DataFrame，對齊①直接落庫）。

每個來源實作把各自 API 的欄位 rename 成這裡的標準欄位，下游（FetchStep）
不需認得各家 API 格式。欄位名對齊 storage.models，落庫時直接轉 dict upsert。
"""

from __future__ import annotations

# 主檔（universe）。sector_name 由 FetchStep 解析成 sector_id。
UNIVERSE_COLS = ["id", "name", "industry_category", "market", "listed_date", "is_etf", "sector_name"]

PRICE_COLS = ["stock_id", "date", "open", "high", "low", "close", "volume", "turnover"]

INSTITUTIONAL_COLS = ["stock_id", "date", "foreign_net", "trust_net", "dealer_net", "total_net"]

# 全市場三大法人買賣超總表（TWSE BFI82U，買賣差額）。單位＝億元（由原始「元」換算）。
#   foreign_net = 外資及陸資(不含外資自營商) + 外資自營商
#   trust_net   = 投信
#   dealer_net  = 自營商(自行買賣) + 自營商(避險)
#   total_net   = 合計（三大法人）
INSTITUTIONAL_MARKET_COLS = ["date", "foreign_net", "trust_net", "dealer_net", "total_net"]

# 加權指數日線（TWSE MI_INDEX「發行量加權股價指數」收盤）。疊圖/量化關係對照用。
MARKET_INDEX_COLS = ["date", "close"]

MARGIN_COLS = ["stock_id", "date", "margin_balance", "margin_change", "short_balance", "short_change"]

REVENUE_COLS = ["stock_id", "year", "month", "revenue", "yoy", "mom"]

FINANCIAL_COLS = [
    "stock_id", "year", "quarter", "eps", "revenue",
    "gross_margin", "op_margin", "net_margin", "roe",
]

VALUATION_COLS = ["stock_id", "date", "pe", "pb", "dividend_yield"]

# 集保戶股權分散（TDCC 開放資料消化後）：占比為「占集保庫存」%。
#   big_pct      = 大戶（≥400 張，分級 12~15）占比
#   over1000_pct = 千張大戶（≥1000 張，分級 15）占比
#   small_pct    = 散戶（<10 張，分級 1~3）占比
#   holders      = 總股東人數（分級 17 合計）
#   avg_lots     = 平均每人持股（張）= 合計股數 / 人數 / 1000
HOLDING_COLS = ["stock_id", "date", "big_pct", "over1000_pct", "small_pct", "holders", "avg_lots"]

EVENT_COLS = ["stock_id", "date", "category", "title", "summary", "is_risk", "source", "url"]

ETF_PROFILE_COLS = ["stock_id", "fund_type", "track_index", "has_foreign", "units", "etf_listed_date"]
