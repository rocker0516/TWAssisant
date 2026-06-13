"""新聞來源層測試：FinMind 新聞欄位映射 + 合併來源容錯。"""

from __future__ import annotations

from datetime import date

import pandas as pd

from app.sources import schemas
from app.sources.base import SourceError
from app.sources.combined import CombinedNewsSource
from app.sources.finmind import FinMindSource


def test_finmind_fetch_events_maps_to_event_cols(monkeypatch):
    fake = pd.DataFrame([
        {"date": "2026-06-10", "stock_id": "2330", "title": "台積電法說會看好", "link": "http://x", "source": "經濟日報"},
        {"date": "2026-06-09", "stock_id": "2317", "title": "", "link": None, "source": ""},  # 空標題應被濾掉
    ])
    src = FinMindSource(token="t")
    monkeypatch.setattr(src, "_data", lambda *a, **k: fake)

    out = src.fetch_events(date(2026, 6, 1), date(2026, 6, 11))

    assert list(out.columns) == schemas.EVENT_COLS
    assert len(out) == 1  # 空標題列被濾除
    row = out.iloc[0]
    assert row["stock_id"] == "2330"
    assert row["date"] == date(2026, 6, 10)
    assert row["source"] == "經濟日報"
    assert row["is_risk"] is False or row["is_risk"] == False  # noqa: E712


def test_finmind_fetch_events_empty(monkeypatch):
    src = FinMindSource(token="t")
    monkeypatch.setattr(src, "_data", lambda *a, **k: pd.DataFrame())
    out = src.fetch_events(date(2026, 6, 1), date(2026, 6, 11))
    assert list(out.columns) == schemas.EVENT_COLS
    assert out.empty


def test_finmind_fetch_stock_events_loops_days(monkeypatch):
    """逐檔新聞：逐日呼叫並合併；單日失敗略過不中斷。"""
    src = FinMindSource(token="t")
    calls = []

    def fake_data(dataset, start=None, end=None, data_id=None):
        calls.append((dataset, start, end, data_id))
        assert end is None  # 關鍵：TaiwanStockNews 不可帶 end_date
        if start == date(2026, 6, 10):
            raise SourceError("limit")  # 某日失敗
        return pd.DataFrame([
            {"date": f"{start} 09:00:00", "stock_id": "2330", "title": f"news {start}", "link": "u", "source": "X"},
        ])

    monkeypatch.setattr(src, "_data", fake_data)
    out = src.fetch_stock_events("2330", date(2026, 6, 9), date(2026, 6, 11))

    assert list(out.columns) == schemas.EVENT_COLS
    assert len(out) == 2  # 3 天中 1 天失敗 → 2 筆
    assert all(c[3] == "2330" for c in calls)  # 每次都帶 data_id


def test_combined_news_merges_and_tolerates_failure(monkeypatch):
    """一個子源失敗（SourceError）不應中斷，另一個照樣回傳。"""
    src = CombinedNewsSource()

    twse_df = pd.DataFrame([
        {"stock_id": "2330", "date": date(2026, 6, 10), "category": "處置警示",
         "title": "處置股票", "summary": None, "is_risk": True, "source": "處置", "url": None},
    ])[schemas.EVENT_COLS]

    monkeypatch.setattr(src._twse, "fetch_events", lambda s, e: twse_df)

    def boom(s, e):
        raise SourceError("FinMind 402: 額度用盡")

    monkeypatch.setattr(src._finmind, "fetch_events", boom)

    out = src.fetch_events(date(2026, 6, 1), date(2026, 6, 11))
    assert list(out.columns) == schemas.EVENT_COLS
    assert len(out) == 1
    assert out.iloc[0]["source"] == "處置"
