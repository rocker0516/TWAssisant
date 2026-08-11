"""法人目標價：快報解析與達標判定（純函式，免 DB / 免網路）。"""

from __future__ import annotations

from datetime import date

from app.sources.cnyes_forecast import parse_forecast_item

TITLE_TP = "鉅亨速報 - Factset 最新調查：臻鼎-KY(4958-TW)目標價調降至640元，幅度約3.76%"
CONTENT_TP = (
    "&lt;p&gt;根據FactSet最新調查，共12位分析師，對臻鼎-KY(4958-TW)提出目標價估值："
    "中位數由665元下修至640元，調降幅度3.76%。其中最高估值824元，最低估值520元。&lt;/p&gt;"
    "&lt;p&gt;綜合評級 - 共有12位分析師給予臻鼎-KY(4958-TW)評價：積極樂觀12位、保持中立1位、保守悲觀0位。&lt;/p&gt;"
)

TITLE_EPS = "鉅亨速報 - Factset 最新調查：台化(1326-TW)EPS預估上修至3.36元，預估目標價為80元"
CONTENT_EPS = (
    "&lt;p&gt;根據FactSet最新調查，共8位分析師，對台化(1326-TW)做出2026年EPS預估："
    "中位數由3.3元上修至3.36元，其中最高估值4.1元，最低估值2.8元，預估目標價為80元。&lt;/p&gt;"
)


def test_parse_target_price_revision():
    r = parse_forecast_item(TITLE_TP, CONTENT_TP, date(2026, 8, 11))
    assert r is not None
    assert r["stock_id"] == "4958"
    assert r["target_price"] == 640.0
    assert r["prev_target"] == 665.0
    assert r["direction"] == "down"
    assert r["target_high"] == 824.0
    assert r["target_low"] == 520.0
    assert r["analyst_count"] == 12
    assert (r["rating_bull"], r["rating_neutral"], r["rating_bear"]) == (12, 1, 0)
    assert r["date"] == date(2026, 8, 11)


def test_parse_eps_type_takes_target_and_eps():
    r = parse_forecast_item(TITLE_EPS, CONTENT_EPS, date(2026, 8, 11))
    assert r is not None
    assert r["stock_id"] == "1326"
    assert r["target_price"] == 80.0
    assert r["eps_est"] == 3.36
    assert r["direction"] == "new"  # EPS 型內文的中位數是 EPS，非目標價前值
    assert r["prev_target"] is None
    # EPS 型的最高/最低估值是 EPS 區間，不可誤收為目標價區間
    assert r["target_high"] is None
    assert r["target_low"] is None


def test_parse_skips_non_tw_or_no_target():
    assert parse_forecast_item("Factset 最新調查：Oscar(OSCR-US)EPS預估上修", "無", date(2026, 8, 11)) is None
    assert parse_forecast_item("台股盤後速記", "今日大盤...", date(2026, 8, 11)) is None
