# Level 1 v3 News Data Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 `events` 表成為可用於 Level 1 v3 訓練的 point-in-time 新聞資料層：每日收錄可交易池全體、記錄抓取時間、用 FinMind 付費層回補 2020 起的全市場個股新聞，並提供 lag-1 的消息面特徵矩陣建構函式。

**Architecture:** 三段：(1) schema——`events` 加 `published_at`（來源給的發布時間字串）與 `fetched_at`（我們抓到的時間，PIT 唯一證據），走既有 `_COLUMN_ADDITIONS` 冪等遷移；(2) 收錄——`FinMindSource._map_news` 保留發布時間、新增 `NewsUniverseStep`（排程 `NewsStep` 之後）抓 U_t 全體單日整市場新聞、`scripts/news_backfill.py` 逐日回補可中斷續跑；(3) 消費——`app/research/level1/news_features.py` 由 `events` 長表建 lag-1 特徵矩陣（`news_cnt5`, `news_burst`, `risk_flag3`, `theme_flag3`, `outlook_flag3`, `news_none60`），供 v3 ablation 的 `+W4` 步。分類沿用 `news_engine.classify`，不用 LLM。

**Tech Stack:** Python 3.12、SQLAlchemy 2（SQLite）、pandas 2.3、httpx（經 `BaseSource`）、pytest。

**對應設計文件：** `docs/superpowers/specs/2026-09-28-level1-v3-target-features-design.md` §2.4、§3。與 `2026-09-28-level1-v3-research-foundation.md` 並行，唯一交會點是 Task 6 產出的 `build_news_features` 會被研究計畫的 ablation 以 `+W4` 步呼叫（本計畫不改 ablation 腳本）。

## Global Constraints

- 消息面特徵**一律 lag 1 個交易日**：t 日決策只能用 `date ≤ t−1` 的新聞（新聞多半只有日期沒有時間；21:30 決策不能用當晚新聞）。
- `fetched_at` 必填（UTC ISO 字串）；回補列的 `fetched_at` = 回補當下，`source` 前綴 `backfill:`——讓日後能區分「當時知道」與「事後補」。
- `events` 去重鍵 `(stock_id, date, title)` 不變，append-only、`on_conflict_do_nothing`，重跑安全。
- 回補用 **FinMind 付費層**整市場單日呼叫（`_data("TaiwanStockNews", d)` 不帶 `data_id`）；免費層會回 4xx → `SourceError`，腳本必須 fail-fast 說明「需付費層」，不得 silently 退回逐檔。
- token 走既有 `credentials.get_token("finmind")`（keychain 或 `backend/credentials.toml` 的 `[sources] finmind_token`）；付費 token 由使用者自行更新，**不進版控、不進對話**。
- 每日收錄母體 = `universe.build_tradable_universe` 的當日 U_t（不是自選股），仍走 `NewsEngine` 的分類與去重邏輯。
- 分類器沿用 `news_engine.classify`；不做情緒極性；不做 PTT。
- 測試指令自 `backend/` 執行：`.venv/Scripts/python.exe -m pytest <path> -q`；lint：`.venv/Scripts/python.exe -m ruff check app scripts tests`。
- Commit 訊息結尾加 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。

## File Structure

| 檔案 | 責任 |
|---|---|
| `backend/app/storage/models.py`（改） | `Event` 加 `published_at: str|None`、`fetched_at: str|None` |
| `backend/app/storage/database.py`（改） | `_COLUMN_ADDITIONS["events"]` 兩欄；`_INDEX_ADDITIONS` 加 `ix_events_date_stock` |
| `backend/app/sources/schemas.py`（改） | `EVENT_COLS` 加 `published_at` |
| `backend/app/sources/finmind.py`（改） | `_map_news` 保留 `published_at`；新增 `fetch_market_news_day(d)` |
| `backend/app/sources/twse.py`／`tpex.py`／`research.py`（改，若其 `fetch_events` 自組 DataFrame） | 補 `published_at=None` 欄，維持 `EVENT_COLS` 契約 |
| `backend/app/engines/news_engine.py`（改） | 抽出 `upsert_events(session, df, fetched_at) -> dict` 純寫入函式；`run` 改呼叫它 |
| `backend/app/scheduler/steps.py`（改） | 新增 `NewsUniverseStep`（U_t 全體整市場單日新聞） |
| `backend/app/scheduler/run.py`（改） | 兩條 step 清單在 `NewsStep()` 後插 `NewsUniverseStep()` |
| `backend/scripts/news_backfill.py`（新） | 逐日整市場回補、checkpoint 續跑、逐年統計 |
| `backend/app/research/level1/news_features.py`（新） | `load_events_long`、`build_news_features`（lag-1 矩陣） |
| `backend/tests/test_events_schema_migration.py`（新） | 欄位存在、遷移冪等 |
| `backend/tests/test_news_sources.py`（改） | `published_at` 映射、`fetch_market_news_day` 付費層失敗 fail-fast |
| `backend/tests/test_news_upsert_and_step.py`（新） | `upsert_events` 去重／fetched_at／分類；`NewsUniverseStep` 母體 = U_t |
| `backend/tests/test_news_backfill.py`（新） | checkpoint 續跑、`backfill:` 前綴、免費層 fail-fast |
| `backend/tests/test_level1_news_features.py`（新） | lag-1 硬規則、burst、none60、只算 U_t |

---

### Task 1: `events` schema——`published_at` / `fetched_at` 與冪等遷移

**Files:**
- Modify: `backend/app/storage/models.py:652-666`
- Modify: `backend/app/storage/database.py`（`_COLUMN_ADDITIONS`、`_INDEX_ADDITIONS`）
- Modify: `backend/app/sources/schemas.py:60`
- Test: `backend/tests/test_events_schema_migration.py`

**Interfaces:**
- Produces: `models.Event.published_at: Mapped[str | None]`（來源原始發布時間字串，如 `"2026-06-10 09:00:00"`；無則 NULL）、`models.Event.fetched_at: Mapped[str | None]`（UTC ISO，寫入時由我們填）；`schemas.EVENT_COLS = [..., "url", "published_at"]`

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_events_schema_migration.py
"""events 表 PIT 欄位：published_at / fetched_at 存在，遷移冪等。"""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.sources import schemas
from app.storage import database as db
from app.storage import models


def test_event_model_has_pit_columns():
    cols = {c.name for c in models.Event.__table__.columns}
    assert {"published_at", "fetched_at"} <= cols


def test_event_cols_contract_includes_published_at():
    assert schemas.EVENT_COLS[-1] == "published_at"
    assert "fetched_at" not in schemas.EVENT_COLS       # fetched_at 由寫入端填，不是來源欄位


def test_column_additions_declare_events_pit_columns():
    assert db._COLUMN_ADDITIONS["events"] == {"published_at": "TEXT", "fetched_at": "TEXT"}
    assert any("ix_events_date_stock" in ddl for ddl in db._INDEX_ADDITIONS)


def test_ensure_columns_is_idempotent_on_legacy_table(monkeypatch):
    """模擬舊 DB（events 無兩欄）：跑兩次 _ensure_columns 都不炸、欄位齊。"""
    eng = create_engine("sqlite://", future=True)
    with eng.begin() as c:
        c.execute(text("""CREATE TABLE events(
            id INTEGER PRIMARY KEY, stock_id VARCHAR(10), date DATE NOT NULL,
            category VARCHAR(30), title TEXT NOT NULL, summary TEXT,
            is_risk BOOLEAN NOT NULL, source VARCHAR(30), url TEXT)"""))
        for t in [t for t in db._COLUMN_ADDITIONS if t != "events"]:
            c.execute(text(f"CREATE TABLE IF NOT EXISTS {t}(id INTEGER PRIMARY KEY)"))
    monkeypatch.setattr(db, "engine", eng)
    db._ensure_columns()
    db._ensure_columns()
    with eng.begin() as c:
        names = {r[1] for r in c.execute(text("PRAGMA table_info(events)"))}
    assert {"published_at", "fetched_at"} <= names
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_events_schema_migration.py -q`
Expected: 4 FAIL（欄位不存在、`_COLUMN_ADDITIONS` 無 `events`）

- [ ] **Step 3: 實作**

`models.py` 的 `Event` 在 `url` 後加：

```python
    # PIT 兩欄（設計 2026-09-28 §3.3）：published_at = 來源給的發布時間字串（多半只有日期）；
    # fetched_at = 我們抓到的 UTC 時間——這是唯一能證明「當時知道」的欄位，回補列 = 回補當下。
    published_at: Mapped[str | None] = mapped_column(String(32))
    fetched_at: Mapped[str | None] = mapped_column(String(32))
```

`database.py` 的 `_COLUMN_ADDITIONS` 加一項：

```python
    "events": {"published_at": "TEXT", "fetched_at": "TEXT"},  # PIT 證據欄（v3 消息面）
```

`_INDEX_ADDITIONS` 追加：

```python
    "CREATE INDEX IF NOT EXISTS ix_events_date_stock ON events(date, stock_id)",
```

`schemas.py`：

```python
EVENT_COLS = ["stock_id", "date", "category", "title", "summary", "is_risk", "source", "url", "published_at"]
```

- [ ] **Step 4: 跑測試 + 既有新聞來源測試（`EVENT_COLS` 變動會影響）**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_events_schema_migration.py tests/test_news_sources.py -q`
Expected: 遷移測試 4 passed；`test_news_sources.py` **會 FAIL**（`_map_news` 尚未產 `published_at`）——這是預期的，Task 2 修。

- [ ] **Step 5: Commit（schema 先行）**

```bash
git add backend/app/storage/models.py backend/app/storage/database.py backend/app/sources/schemas.py backend/tests/test_events_schema_migration.py
git commit -m "feat(events): 加 published_at / fetched_at PIT 欄位與冪等遷移

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: FinMind 新聞映射保留發布時間；整市場單日抓取

**Files:**
- Modify: `backend/app/sources/finmind.py:342-395`
- Modify: `backend/app/sources/twse.py`、`backend/app/sources/tpex.py`、`backend/app/sources/research.py`（凡自組 `EVENT_COLS` DataFrame 者補 `published_at`）
- Test: `backend/tests/test_news_sources.py`

**Interfaces:**
- Produces:
  - `FinMindSource._map_news(df) -> DataFrame[EVENT_COLS]`，`published_at` = 原始 `date` 字串（`str`，空為 None），`date` 仍為 `datetime.date`
  - `FinMindSource.fetch_market_news_day(d: date) -> pd.DataFrame`：整市場單日；免費層 4xx → 拋 `SourceError`（不吞）

- [ ] **Step 1: 追加／修改失敗測試**

在 `tests/test_news_sources.py` 的 `test_finmind_fetch_events_maps_to_event_cols` 內 `row = out.iloc[0]` 後追加：

```python
    assert row["published_at"] == "2026-06-10"
```

檔尾追加：

```python
def test_finmind_map_news_keeps_publish_time_string(monkeypatch):
    fake = pd.DataFrame([
        {"date": "2026-06-10 21:45:00", "stock_id": "2330", "title": "盤後重訊", "link": "u", "source": "X"},
    ])
    src = FinMindSource(token="t")
    monkeypatch.setattr(src, "_data", lambda *a, **k: fake)
    out = src.fetch_events(date(2026, 6, 10), date(2026, 6, 10))
    assert out.iloc[0]["published_at"] == "2026-06-10 21:45:00"
    assert out.iloc[0]["date"] == date(2026, 6, 10)


def test_finmind_fetch_market_news_day_calls_without_data_id(monkeypatch):
    src = FinMindSource(token="t")
    calls = []

    def fake_data(dataset, start=None, end=None, data_id=None):
        calls.append((dataset, start, end, data_id))
        return pd.DataFrame([{"date": "2026-06-10", "stock_id": "2330", "title": "a", "link": "u", "source": "X"}])

    monkeypatch.setattr(src, "_data", fake_data)
    out = src.fetch_market_news_day(date(2026, 6, 10))
    assert calls == [("TaiwanStockNews", date(2026, 6, 10), None, None)]
    assert len(out) == 1 and list(out.columns) == schemas.EVENT_COLS


def test_finmind_fetch_market_news_day_raises_on_free_tier(monkeypatch):
    """付費層才給整市場；免費層 4xx 必須拋出，不得 silently 回空表。"""
    src = FinMindSource(token="t")

    def fake_data(*a, **k):
        raise SourceError("FinMind 400: paid only", status=400)

    monkeypatch.setattr(src, "_data", fake_data)
    import pytest
    with pytest.raises(SourceError):
        src.fetch_market_news_day(date(2026, 6, 10))
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_news_sources.py -q`
Expected: `published_at` 相關 FAIL、`fetch_market_news_day` AttributeError

- [ ] **Step 3: 實作**

`finmind.py` 的 `_map_news` 改為：

```python
    @staticmethod
    def _map_news(df: pd.DataFrame) -> pd.DataFrame:
        """TaiwanStockNews 原始欄位 → EVENT_COLS。published_at 保留原始時間字串（PIT 用）。"""
        if df.empty or "stock_id" not in df:
            return pd.DataFrame(columns=schemas.EVENT_COLS)
        out = pd.DataFrame()
        out["stock_id"] = df["stock_id"].astype(str)
        raw_dt = df["date"].astype(str).str.strip()
        out["date"] = pd.to_datetime(raw_dt, errors="coerce").dt.date
        out["category"] = None
        out["title"] = df.get("title", "").fillna("").astype(str)
        out["summary"] = None
        out["is_risk"] = False
        out["source"] = df.get("source", "").fillna("").replace("", "新聞")
        out["url"] = df.get("link")
        out["published_at"] = raw_dt.where(raw_dt != "", None)
        out = out[out["title"].str.len() > 0]
        return out[schemas.EVENT_COLS]

    def fetch_market_news_day(self, d: date) -> pd.DataFrame:
        """整市場單日個股新聞（付費層）。免費層 4xx 由 _data 拋 SourceError——刻意不吞：
        回補腳本必須知道自己拿的是整市場還是空集合。"""
        return self._map_news(self._data("TaiwanStockNews", d))
```

`fetch_events` 保持呼叫 `_map_news`，不動。

其他來源：用 `grep -n "EVENT_COLS\|\"url\"" app/sources/twse.py app/sources/tpex.py app/sources/research.py` 找到自組 DataFrame 的地方，在建立 `url` 欄後加 `out["published_at"] = None`（或 dict 補鍵）。凡最後 `return out[schemas.EVENT_COLS]` 的都必須有這欄，否則 KeyError。

- [ ] **Step 4: 跑測試確認通過（含既有全部新聞來源測試）**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_news_sources.py tests/test_events_schema_migration.py -q`
Expected: 全綠

- [ ] **Step 5: Commit**

```bash
git add backend/app/sources/
git add backend/tests/test_news_sources.py
git commit -m "feat(sources): FinMind 新聞保留發布時間、整市場單日抓取（付費層 fail-fast）

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `upsert_events` 純寫入函式（記 `fetched_at`）

**Files:**
- Modify: `backend/app/engines/news_engine.py`
- Test: `backend/tests/test_news_upsert_and_step.py`

**Interfaces:**
- Produces: `upsert_events(session, df: pd.DataFrame, *, fetched_at: str, trading_date: date, source_prefix: str = "") -> dict`：過濾未知股號、分類、寫 `published_at`／`fetched_at`、`source` 加前綴、`on_conflict_do_nothing`；回 `{"events": n_rows, "risk": n_risk}`。`NewsEngine.run` 改呼叫它。

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_news_upsert_and_step.py
"""events 寫入：fetched_at 必填、去重、分類、source 前綴；NewsUniverseStep 母體 = U_t。"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.engines import news_engine as ne
from app.storage import models
from app.storage.models import Base


@pytest.fixture()
def session():
    eng = create_engine("sqlite://", future=True)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)()
    s.add_all([models.Stock(id="2330", name="台積電"), models.Stock(id="2317", name="鴻海")])
    s.commit()
    yield s
    s.close()


def _df(rows):
    return pd.DataFrame(rows, columns=["stock_id", "date", "category", "title", "summary",
                                       "is_risk", "source", "url", "published_at"])


def test_upsert_events_writes_pit_columns_and_classifies(session):
    df = _df([
        ("2330", date(2026, 9, 1), None, "台積電擴廠投資千億", None, False, "X", "u1", "2026-09-01 10:00:00"),
        ("2317", date(2026, 9, 1), None, "鴻海財測下修", None, False, "Y", "u2", None),
        ("9999", date(2026, 9, 1), None, "未知股", None, False, "Z", "u3", None),   # 未知股號 → 丟
    ])
    out = ne.upsert_events(session, df, fetched_at="2026-09-01T13:45:00Z", trading_date=date(2026, 9, 1))
    session.commit()
    assert out == {"events": 2, "risk": 1}
    rows = {r.stock_id: r for r in session.execute(select(models.Event)).scalars()}
    assert rows["2330"].category == "題材" and rows["2330"].is_risk is False
    assert rows["2317"].category == "利空" and rows["2317"].is_risk is True
    assert rows["2330"].published_at == "2026-09-01 10:00:00"
    assert rows["2317"].published_at is None
    assert all(r.fetched_at == "2026-09-01T13:45:00Z" for r in rows.values())


def test_upsert_events_dedups_and_prefixes_source(session):
    df = _df([("2330", date(2026, 9, 1), None, "同一則", None, False, "X", "u", None)])
    ne.upsert_events(session, df, fetched_at="t1", trading_date=date(2026, 9, 1))
    session.commit()
    ne.upsert_events(session, df, fetched_at="t2", trading_date=date(2026, 9, 1), source_prefix="backfill:")
    session.commit()
    rows = session.execute(select(models.Event)).scalars().all()
    assert len(rows) == 1 and rows[0].fetched_at == "t1"      # 先到先贏，重跑不覆寫
    df2 = _df([("2330", date(2026, 9, 2), None, "另一則", None, False, "X", "u", None)])
    ne.upsert_events(session, df2, fetched_at="t3", trading_date=date(2026, 9, 2), source_prefix="backfill:")
    session.commit()
    r = session.execute(select(models.Event).where(models.Event.date == date(2026, 9, 2))).scalar_one()
    assert r.source == "backfill:X"


def test_upsert_events_requires_fetched_at(session):
    with pytest.raises(ValueError):
        ne.upsert_events(session, _df([]), fetched_at="", trading_date=date(2026, 9, 1))
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_news_upsert_and_step.py -q`
Expected: 3 FAIL，`AttributeError: upsert_events`

- [ ] **Step 3: 實作**

在 `news_engine.py` 的 `class NewsEngine` 之前加：

```python
def upsert_events(session: Session, df: pd.DataFrame, *, fetched_at: str, trading_date: date,
                  source_prefix: str = "") -> dict:
    """events 唯一寫入口：分類 → 記 PIT 欄 → append-only 去重。

    fetched_at 必填：這是唯一能證明「當時知道」的欄位（設計 2026-09-28 §3.3）。
    source_prefix="backfill:" 標記事後補的列，讓研究端能區分即時收錄與回補。
    先到先贏（on_conflict_do_nothing）：回補不得覆寫即時列的 fetched_at。
    """
    if not fetched_at:
        raise ValueError("fetched_at 必填（PIT 證據）")
    if df.empty:
        return {"events": 0, "risk": 0}
    known = set(session.execute(select(models.Stock.id)).scalars().all())
    rows: list[dict] = []
    for rec in df.to_dict("records"):
        sid = str(rec["stock_id"])
        if sid not in known:
            continue
        title = rec["title"]
        if rec.get("is_risk"):
            category, is_risk = rec.get("category") or "利空", True
        elif rec.get("category"):
            category, is_risk = rec["category"], False
        else:
            category, is_risk = classify(title, rec.get("summary"))
        src = rec.get("source")
        rows.append({
            "stock_id": sid, "date": rec.get("date") or trading_date,
            "category": category, "title": title, "summary": rec.get("summary"),
            "is_risk": is_risk, "source": f"{source_prefix}{src}" if src else (source_prefix or None),
            "url": rec.get("url"),
            "published_at": rec.get("published_at") if isinstance(rec.get("published_at"), str) else None,
            "fetched_at": fetched_at,
        })
    if rows:
        stmt = sqlite_insert(models.Event).values(rows).on_conflict_do_nothing(
            index_elements=["stock_id", "date", "title"])
        session.execute(stmt)
        session.flush()
    return {"events": len(rows), "risk": sum(1 for r in rows if r["is_risk"])}
```

`NewsEngine.run` 從 `known = set(...)` 到 `return {...}` 整段改成：

```python
        from datetime import datetime, timezone
        out = upsert_events(session, df, fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            trading_date=trading_date)
        return {"status": "ok", "events": out["events"], "stock_news": stock_news, "risk": out["risk"]}
```

並把空表分支 `pd.DataFrame(columns=[...])` 的欄位清單補上 `"published_at"`。

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_news_upsert_and_step.py tests/test_news_sources.py -q`
Expected: 全綠

- [ ] **Step 5: Commit**

```bash
git add backend/app/engines/news_engine.py backend/tests/test_news_upsert_and_step.py
git commit -m "refactor(news): 抽出 upsert_events 唯一寫入口，記 fetched_at/published_at 與 source 前綴

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `NewsUniverseStep`——每日收錄 U_t 全體

**Files:**
- Modify: `backend/app/scheduler/steps.py`（`NewsStep` 之後）
- Modify: `backend/app/scheduler/run.py:41,69`
- Test: `backend/tests/test_news_upsert_and_step.py`（追加）

**Interfaces:**
- Consumes: `registry.provider("news")`（`CombinedNewsSource`）、`FinMindSource.fetch_market_news_day`、`upsert_events`、`universe.build_tradable_universe`
- Produces: `NewsUniverseStep(PipelineStep)`，`name="news_universe"`, `required=False`；`run(ctx) -> {"status", "events", "risk", "universe_size", "mode"}`，`mode ∈ {"market_day", "unavailable"}`。整市場抓取失敗（免費層）→ `status="skipped"`, `mode="unavailable"`，**不退回逐檔**（逐檔是 `NewsStep` 的職責，且母體不同）。

- [ ] **Step 1: 追加失敗測試**

```python
# 追加到 backend/tests/test_news_upsert_and_step.py
from app.scheduler import steps as st
from app.sources.base import SourceError


class _Ctx:
    def __init__(self, session, td):
        self.session, self.trading_date = session, td


def test_news_universe_step_filters_to_universe_and_records_fetched_at(session, monkeypatch):
    td = date(2026, 9, 1)
    market = pd.DataFrame([
        {"stock_id": "2330", "date": td, "category": None, "title": "A", "summary": None,
         "is_risk": False, "source": "X", "url": "u", "published_at": None},
        {"stock_id": "2317", "date": td, "category": None, "title": "B", "summary": None,
         "is_risk": False, "source": "X", "url": "u", "published_at": None},
    ])

    class _FM:
        def fetch_market_news_day(self, d):
            assert d == td
            return market

    monkeypatch.setattr(st, "_finmind_source", lambda: _FM())
    monkeypatch.setattr(st, "_universe_ids_for", lambda session, d: {"2330"})   # 2317 不在 U_t
    out = st.NewsUniverseStep().run(_Ctx(session, td))
    session.commit()
    assert out["status"] == "ok" and out["mode"] == "market_day"
    assert out["events"] == 1 and out["universe_size"] == 1
    rows = session.execute(select(models.Event)).scalars().all()
    assert [r.stock_id for r in rows] == ["2330"]
    assert rows[0].fetched_at and rows[0].source == "X"          # 即時收錄不加前綴


def test_news_universe_step_skips_when_market_feed_unavailable(session, monkeypatch):
    class _FM:
        def fetch_market_news_day(self, d):
            raise SourceError("paid only", status=400)

    monkeypatch.setattr(st, "_finmind_source", lambda: _FM())
    monkeypatch.setattr(st, "_universe_ids_for", lambda session, d: {"2330"})
    out = st.NewsUniverseStep().run(_Ctx(session, date(2026, 9, 1)))
    assert out["status"] == "skipped" and out["mode"] == "unavailable"
    assert session.execute(select(models.Event)).scalars().all() == []
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_news_upsert_and_step.py -q`
Expected: 2 new FAIL，`AttributeError: _finmind_source`

- [ ] **Step 3: 實作**

`steps.py` 在 `class NewsStep` 之後加：

```python
def _finmind_source():
    """可被測試替換的 FinMind 取得點（registry 內 CombinedNewsSource 的 _finmind）。"""
    prov = registry.provider("news")
    return getattr(prov, "_finmind", prov)


def _universe_ids_for(session, trading_date: date) -> set[str]:
    """當日可交易池 U_t 的股號集合（與 Level 1 研究同一入口）。"""
    import sqlite3
    from ..config import settings as cfg
    from ..research.level1 import universe as uv

    con = sqlite3.connect(cfg.db_path)
    try:
        close, mask = uv.build_tradable_universe(con)
    finally:
        con.close()
    key = trading_date.isoformat()
    if key not in mask.index:
        key = mask.index[mask.index <= key][-1]          # 非交易日／尚未入庫 → 取最近一日
    return set(mask.columns[mask.loc[key].to_numpy()].astype(str))


class NewsUniverseStep(PipelineStep):
    """v3 消息面資料層：每日收錄可交易池 U_t 全體的個股新聞（設計 2026-09-28 §3.3-1）。

    與 NewsStep 分工：NewsStep 抓重訊/處置 + 自選股逐檔（免費層可跑）；本 step 走
    FinMind 付費層「整市場單日」一次呼叫，再以 U_t 過濾。整市場不可用（免費層 4xx）
    → skipped，不退回逐檔（母體不同，混了研究端分不開）。
    """

    name = "news_universe"
    required = False

    def run(self, ctx: PipelineContext) -> dict:
        from datetime import datetime, timezone
        from ..engines.news_engine import upsert_events
        from ..sources.base import SourceError

        try:
            df = _finmind_source().fetch_market_news_day(ctx.trading_date)
        except SourceError as exc:
            return {"status": "skipped", "mode": "unavailable", "reason": str(exc)[:200]}
        ids = _universe_ids_for(ctx.session, ctx.trading_date)
        df = df[df["stock_id"].astype(str).isin(ids)]
        out = upsert_events(ctx.session, df,
                            fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            trading_date=ctx.trading_date)
        return {"status": "ok", "mode": "market_day", "universe_size": len(ids), **out}
```

確認 `steps.py` 頂部已 `from ..sources import registry`（`NewsEngine` 用到，若沒有就加）。`cfg.db_path` 名稱以 `app/config.py` 實際欄位為準（`grep -n "db_path\|database_url" app/config.py`），若只有 SQLAlchemy URL，改用 `engine.raw_connection()` 或從 URL 取檔案路徑。

`run.py` 兩處 step 清單 `NewsStep(),` 後插入 `NewsUniverseStep(),`，並補 import。

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_news_upsert_and_step.py -q`
Expected: 5 passed

- [ ] **Step 5: 本機排程乾跑一次（DB 真跑，不推送）**

Run: `cd backend && .venv/Scripts/python.exe -c "import sys; sys.argv=['x']; from app.scheduler.run import *; print('import ok')"`
Expected: 無 import 錯誤。（實際排程 21:30 自動跑；若目前 token 仍是免費層，log 會看到 `news_universe skipped unavailable`，這是預期。）

- [ ] **Step 6: Commit**

```bash
git add backend/app/scheduler/steps.py backend/app/scheduler/run.py backend/tests/test_news_upsert_and_step.py
git commit -m "feat(scheduler): NewsUniverseStep——每日收錄可交易池全體新聞（付費層整市場，不可用即跳過）

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: 歷史回補腳本 `news_backfill.py`

**Files:**
- Create: `backend/scripts/news_backfill.py`
- Test: `backend/tests/test_news_backfill.py`

**Interfaces:**
- Consumes: `FinMindSource.fetch_market_news_day`、`upsert_events`、`build_tradable_universe`
- Produces:
  - `backfill_range(session, src, start: date, end: date, *, universe_by_day: dict[str, set[str]], checkpoint: Path, log=print) -> dict`：逐日呼叫、每日 commit、寫 checkpoint（最後完成日期）、`source_prefix="backfill:"`；首日即 `SourceError` 4xx → 拋 `SystemExit("需 FinMind 付費層")`；中途 429／網路錯誤由 `BaseSource` 重試，仍失敗則記 `failed_days` 續跑下一日。
  - CLI：`--start 2020-01-01 --end <今>`、`--checkpoint data/news_backfill.ckpt`、`--resume`。
  - 結尾印逐年 `{year: {"days": n, "events": n, "stocks": n}}`。

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_news_backfill.py
"""新聞回補：checkpoint 續跑、backfill: 前綴、免費層 fail-fast、失敗日不中斷。"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.sources.base import SourceError
from app.storage import models
from app.storage.models import Base
from scripts import news_backfill as nb


@pytest.fixture()
def session():
    eng = create_engine("sqlite://", future=True)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)()
    s.add(models.Stock(id="2330", name="台積電"))
    s.commit()
    yield s
    s.close()


def _row(d, title):
    return {"stock_id": "2330", "date": d, "category": None, "title": title, "summary": None,
            "is_risk": False, "source": "X", "url": "u", "published_at": None}


class _Src:
    def __init__(self, fail_on=(), free_tier=False):
        self.calls, self.fail_on, self.free = [], set(fail_on), free_tier

    def fetch_market_news_day(self, d):
        self.calls.append(d)
        if self.free:
            raise SourceError("paid only", status=400)
        if d in self.fail_on:
            raise SourceError("timeout", status=None)
        return pd.DataFrame([_row(d, f"n{d}")])


def test_backfill_writes_prefix_and_checkpoint(session, tmp_path):
    ck = tmp_path / "ck"
    src = _Src()
    u = {"2020-01-02": {"2330"}, "2020-01-03": {"2330"}}
    out = nb.backfill_range(session, src, date(2020, 1, 2), date(2020, 1, 3),
                            universe_by_day=u, checkpoint=ck, log=lambda *_: None)
    assert out["days_done"] == 2 and out["events"] == 2 and out["failed_days"] == []
    assert ck.read_text().strip() == "2020-01-03"
    rows = session.execute(select(models.Event)).scalars().all()
    assert all(r.source == "backfill:X" and r.fetched_at for r in rows)


def test_backfill_resume_skips_done_days(session, tmp_path):
    ck = tmp_path / "ck"
    ck.write_text("2020-01-02")
    src = _Src()
    u = {"2020-01-02": {"2330"}, "2020-01-03": {"2330"}}
    nb.backfill_range(session, src, date(2020, 1, 2), date(2020, 1, 3),
                      universe_by_day=u, checkpoint=ck, log=lambda *_: None)
    assert src.calls == [date(2020, 1, 3)]


def test_backfill_free_tier_fails_fast(session, tmp_path):
    with pytest.raises(SystemExit):
        nb.backfill_range(session, _Src(free_tier=True), date(2020, 1, 2), date(2020, 1, 3),
                          universe_by_day={"2020-01-02": {"2330"}}, checkpoint=tmp_path / "ck",
                          log=lambda *_: None)


def test_backfill_transient_failure_recorded_and_continues(session, tmp_path):
    src = _Src(fail_on={date(2020, 1, 3)})
    u = {f"2020-01-0{i}": {"2330"} for i in (2, 3, 6)}
    out = nb.backfill_range(session, src, date(2020, 1, 2), date(2020, 1, 6),
                            universe_by_day=u, checkpoint=tmp_path / "ck", log=lambda *_: None)
    assert out["failed_days"] == ["2020-01-03"]
    assert out["days_done"] == 2


def test_backfill_skips_non_trading_days(session, tmp_path):
    src = _Src()
    u = {"2020-01-02": {"2330"}}                       # 只有一個交易日
    nb.backfill_range(session, src, date(2020, 1, 1), date(2020, 1, 5),
                      universe_by_day=u, checkpoint=tmp_path / "ck", log=lambda *_: None)
    assert src.calls == [date(2020, 1, 2)]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_news_backfill.py -q`
Expected: FAIL，`ModuleNotFoundError: scripts.news_backfill`（若 `scripts/` 無 `__init__.py`，比照既有 `tests/test_level1_pipeline_parity.py` 的 import 方式）

- [ ] **Step 3: 實作**

```python
# backend/scripts/news_backfill.py
"""v3 消息面歷史回補（設計 2026-09-28 §3.3-2）：FinMind 付費層整市場單日，逐日 2020-01-01 → 今。

- 只打交易日（universe_by_day 有鍵者），以 U_t 過濾後寫 events，source 前綴 backfill:。
- 每日 commit + 寫 checkpoint（最後完成日），--resume 從 checkpoint 下一日續跑。
- 首日就 4xx → SystemExit：這是「需付費層」，不得退回逐檔（母體不同）。
- 其他失敗（429 用盡重試／網路）記 failed_days 續跑，結尾印出供補跑。

用法：cd backend && .venv/Scripts/python.exe -m scripts.news_backfill --start 2020-01-01 [--end YYYY-MM-DD] [--resume]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engines.news_engine import upsert_events  # noqa: E402
from app.sources.base import SourceError  # noqa: E402

_DATA = Path(__file__).resolve().parents[1] / "data"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _iter_days(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def backfill_range(session, src, start: date, end: date, *, universe_by_day: dict[str, set[str]],
                   checkpoint: Path, log=_log) -> dict:
    done_until: date | None = None
    if checkpoint.exists() and checkpoint.read_text().strip():
        done_until = date.fromisoformat(checkpoint.read_text().strip())
    days_done, events, failed, first_call = 0, 0, [], True
    for d in _iter_days(start, end):
        key = d.isoformat()
        if key not in universe_by_day:
            continue                                   # 非交易日
        if done_until and d <= done_until:
            continue                                   # 已完成
        try:
            df = src.fetch_market_news_day(d)
        except SourceError as exc:
            if first_call and exc.status is not None and 400 <= exc.status < 500 and exc.status != 429:
                raise SystemExit(f"FinMind 整市場新聞不可用（{exc}）——需付費層 token，請更新 credentials 後重跑。")
            failed.append(key)
            log(f"  {key} 失敗：{exc}")
            first_call = False
            continue
        first_call = False
        df = df[df["stock_id"].astype(str).isin(universe_by_day[key])]
        out = upsert_events(session, df, fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            trading_date=d, source_prefix="backfill:")
        session.commit()
        checkpoint.write_text(key)
        days_done += 1
        events += out["events"]
        if days_done % 20 == 0:
            log(f"  進度 {key}：{days_done} 日、{events} 則")
    return {"days_done": days_done, "events": events, "failed_days": failed}


def _universe_by_day() -> dict[str, set[str]]:
    from app.research.level1 import universe as uv

    con = sqlite3.connect(_DATA / "twa.db")
    try:
        _, mask = uv.build_tradable_universe(con)
    finally:
        con.close()
    cols = mask.columns.astype(str)
    return {str(d): set(cols[row]) for d, row in zip(mask.index, mask.to_numpy())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--checkpoint", default=str(_DATA / "news_backfill.ckpt"))
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()
    ck = Path(args.checkpoint)
    if not args.resume and ck.exists():
        ck.unlink()

    from app.sources.finmind import FinMindSource
    from app.storage.database import SessionLocal

    src = FinMindSource()
    if not src.token:
        raise SystemExit("找不到 finmind token（credentials.toml [sources] finmind_token 或 keychain）")
    ubd = _universe_by_day()
    _log(f"交易日 {len(ubd)} 天；範圍 {args.start} ~ {args.end}")
    with SessionLocal() as s:
        out = backfill_range(s, src, date.fromisoformat(args.start), date.fromisoformat(args.end),
                             universe_by_day=ubd, checkpoint=ck)
        yearly = s.execute(__import__("sqlalchemy").text(
            "SELECT substr(date,1,4) y, count(*) n, count(distinct stock_id) k, count(distinct date) d "
            "FROM events WHERE source LIKE 'backfill:%' GROUP BY 1 ORDER BY 1")).fetchall()
    _log(f"完成：{out['days_done']} 日、{out['events']} 則；失敗日 {len(out['failed_days'])}：{out['failed_days'][:10]}")
    for y, n, k, d in yearly:
        _log(f"  {y}: {n:>7,} 則 / {k:>4} 檔 / {d:>3} 日")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_news_backfill.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit（腳本入版控；實際回補等付費 token 就位後由使用者啟動）**

```bash
git add backend/scripts/news_backfill.py backend/tests/test_news_backfill.py
git commit -m "feat(news): 歷史回補腳本（付費層整市場逐日、checkpoint 續跑、backfill: 前綴）

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 6: 回補啟動（需使用者先更新付費 token；背景長跑）**

先確認 token 已是付費層（跑一天）：

```bash
cd backend && .venv/Scripts/python.exe -m scripts.news_backfill --start 2024-06-03 --end 2024-06-03 --checkpoint data/news_probe.ckpt
```
Expected: 印出 1 日、數十至數百則。若 `SystemExit ... 需付費層` → 停下，請使用者更新 `credentials.toml` 的 `finmind_token`。

通過後全量（背景，約 1,250 次呼叫；依付費層限速，數十分鐘到數小時）：

```bash
cd backend && .venv/Scripts/python.exe -m scripts.news_backfill --start 2020-01-01 --resume
```

結尾把逐年統計貼進 findings（Task 6 之後的研究計畫 `+W4` 步會用到覆蓋率）。

---

### Task 6: 消息面特徵矩陣 `news_features.py`（lag-1）

**Files:**
- Create: `backend/app/research/level1/news_features.py`
- Test: `backend/tests/test_level1_news_features.py`

**Interfaces:**
- Produces:
  - `load_events_long(con, start: str, end: str) -> pd.DataFrame`（欄 `stock_id, date, category, is_risk`；`date` 為 str）
  - `build_news_features(events: pd.DataFrame, index: pd.Index, columns: pd.Index, in_universe: pd.DataFrame) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]` → `(to_rank, raw)`：
    - `to_rank`：`news_cnt5`（近 5 交易日則數）、`news_burst`（近 5 日則數 ÷ 近 60 日日均則數，分母 0 → NaN）
    - `raw`：`risk_flag3`、`theme_flag3`、`outlook_flag3`（近 3 交易日有該類 0/1）、`news_none60`（近 60 交易日零新聞 0/1）
    - **全部以 t−1 為最後可用日**（先把每日計數矩陣 `shift(1)` 再 rolling）
  - `NEWS_FAMILY: list[str]` 六個名字，供研究計畫 `FAMILIES["W4"]` 註冊

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_level1_news_features.py
"""消息面特徵：lag-1 硬規則、burst、none60、只算 U_t。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.research.level1 import news_features as nf


def _frame(n_days=80):
    idx = pd.Index(pd.date_range("2025-01-01", periods=n_days).astype(str), name="date")
    cols = pd.Index(["1111", "2222"], name="stock_id")
    mask = pd.DataFrame(True, index=idx, columns=cols)
    return idx, cols, mask


def _events(rows):
    return pd.DataFrame(rows, columns=["stock_id", "date", "category", "is_risk"])


def test_news_features_are_lagged_one_day():
    idx, cols, mask = _frame()
    ev = _events([("1111", idx[70], "利空", True), ("1111", idx[70], "題材", False)])
    to_rank, raw = nf.build_news_features(ev, idx, cols, mask)
    # t = 事件當日：不可見
    assert to_rank["news_cnt5"].loc[idx[70], "1111"] == 0
    assert raw["risk_flag3"].loc[idx[70], "1111"] == 0
    # t+1 起可見
    assert to_rank["news_cnt5"].loc[idx[71], "1111"] == 2
    assert raw["risk_flag3"].loc[idx[71], "1111"] == 1
    assert raw["theme_flag3"].loc[idx[71], "1111"] == 1
    assert raw["outlook_flag3"].loc[idx[71], "1111"] == 0
    # 3 日窗：t+4 起 flag 歸零；5 日窗：t+6 起 cnt 歸零
    assert raw["risk_flag3"].loc[idx[74], "1111"] == 0
    assert to_rank["news_cnt5"].loc[idx[75], "1111"] == 1  # 第 5 日仍算
    assert to_rank["news_cnt5"].loc[idx[76], "1111"] == 0


def test_news_features_no_future_when_truncated():
    idx, cols, mask = _frame()
    ev = _events([("2222", idx[60], "中性", False), ("2222", idx[78], "利空", True)])
    full_r, full_raw = nf.build_news_features(ev, idx, cols, mask)
    cut_r, cut_raw = nf.build_news_features(ev[ev["date"] <= idx[70]], idx[:71], cols, mask.iloc[:71])
    for k in full_r:
        assert np.allclose(full_r[k].iloc[:71].fillna(-1).to_numpy(), cut_r[k].fillna(-1).to_numpy()), k
    for k in full_raw:
        assert np.allclose(full_raw[k].iloc[:71].fillna(-1).to_numpy(), cut_raw[k].fillna(-1).to_numpy()), k


def test_news_burst_and_none60():
    idx, cols, mask = _frame(n_days=100)
    rows = [("1111", idx[i], "中性", False) for i in range(20, 80)]          # 每天 1 則，60 天
    rows += [("1111", idx[79], "中性", False)] * 4                            # 最後一天爆 5 則
    to_rank, raw = nf.build_news_features(_events(rows), idx, cols, mask)
    # t=80：近 5 日（75..79）= 4+5 = 9 則；近 60 日日均 ≈ (59+5)/60
    assert to_rank["news_burst"].loc[idx[80], "1111"] == pytest.approx(9 / ((59 + 5) / 60), rel=1e-6)
    assert raw["news_none60"].loc[idx[80], "1111"] == 0
    assert raw["news_none60"].loc[idx[80], "2222"] == 1                       # 2222 從無新聞
    assert np.isnan(to_rank["news_burst"].loc[idx[80], "2222"])               # 分母 0 → NaN
    assert set(nf.NEWS_FAMILY) == set(to_rank) | set(raw)


def test_news_features_masked_outside_universe():
    idx, cols, mask = _frame()
    mask.loc[:, "2222"] = False
    ev = _events([("2222", idx[10], "利空", True)])
    to_rank, raw = nf.build_news_features(ev, idx, cols, mask)
    assert to_rank["news_cnt5"]["2222"].isna().all()
    assert raw["risk_flag3"]["2222"].isna().all()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_level1_news_features.py -q`
Expected: FAIL，`ModuleNotFoundError: news_features`

- [ ] **Step 3: 實作**

```python
# backend/app/research/level1/news_features.py
"""Level 1 v3 消息面特徵（設計 2026-09-28 §2.4）——events 長表 → lag-1 日矩陣。

硬規則：t 日決策只能用 date ≤ t−1 的新聞。回補新聞多半只有日期沒有時間，21:30 決策不能用
當晚新聞；這與 Gen1「法人／融資須 lag1」同一教訓。實作：每日計數矩陣先 shift(1) 再 rolling。

角色：news_cnt5 / news_burst → 方向訊號（rank）；三個類別旗標與 news_none60 → 0/1 raw。
news_none60 讓「無覆蓋」與「真的沒新聞」分開——缺值 ≠ 零。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

NEWS_FAMILY = ["news_cnt5", "news_burst", "risk_flag3", "theme_flag3", "outlook_flag3", "news_none60"]


def load_events_long(con, start: str, end: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT stock_id, date, category, is_risk FROM events "
        "WHERE stock_id IS NOT NULL AND date BETWEEN ? AND ?", con, params=(start, end))
    df["stock_id"] = df["stock_id"].astype(str)
    df["date"] = df["date"].astype(str).str[:10]
    return df


def _daily_count(ev: pd.DataFrame, index: pd.Index, columns: pd.Index) -> pd.DataFrame:
    """事件長表 → 每日則數矩陣（日曆日對齊到交易日 index：非交易日的新聞歸到下一個交易日）。"""
    if ev.empty:
        return pd.DataFrame(0.0, index=index, columns=columns)
    cnt = ev.groupby(["date", "stock_id"]).size().unstack("stock_id").reindex(columns=columns)
    union = cnt.index.union(index)
    cnt = cnt.reindex(union).fillna(0.0)
    # 非交易日 → 累加到下一個交易日：用「下一個交易日」標籤 groupby sum
    pos = np.searchsorted(index.to_numpy(), union.to_numpy(), side="left")
    pos = np.clip(pos, 0, len(index) - 1)
    lab = index.to_numpy()[pos]
    valid = union.to_numpy() <= index[-1]                 # index 之後的新聞丟掉（截斷未來）
    out = cnt[valid].groupby(lab[valid]).sum().reindex(index).fillna(0.0)
    return out.astype("float64")


def build_news_features(events: pd.DataFrame, index: pd.Index, columns: pd.Index,
                        in_universe: pd.DataFrame) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    ev = events.copy()
    ev["stock_id"] = ev["stock_id"].astype(str)
    ev["date"] = ev["date"].astype(str).str[:10]
    ev = ev[ev["stock_id"].isin(set(columns))]

    total = _daily_count(ev, index, columns).shift(1).fillna(0.0)          # lag-1
    risk = _daily_count(ev[ev["is_risk"].astype(bool)], index, columns).shift(1).fillna(0.0)
    theme = _daily_count(ev[ev["category"] == "題材"], index, columns).shift(1).fillna(0.0)
    outlook = _daily_count(ev[ev["category"] == "展望"], index, columns).shift(1).fillna(0.0)

    cnt5 = total.rolling(5, min_periods=1).sum()
    avg60 = total.rolling(60, min_periods=20).mean()
    burst = cnt5 / avg60.replace(0.0, np.nan)
    none60 = (total.rolling(60, min_periods=20).sum() == 0).astype(float).where(avg60.notna())

    to_rank = {"news_cnt5": cnt5, "news_burst": burst}
    raw = {
        "risk_flag3": (risk.rolling(3, min_periods=1).sum() > 0).astype(float),
        "theme_flag3": (theme.rolling(3, min_periods=1).sum() > 0).astype(float),
        "outlook_flag3": (outlook.rolling(3, min_periods=1).sum() > 0).astype(float),
        "news_none60": none60,
    }
    to_rank = {k: v.where(in_universe) for k, v in to_rank.items()}
    raw = {k: v.where(in_universe).astype("float32") for k, v in raw.items()}
    return to_rank, raw
```

注意 `test_news_burst_and_none60` 的期望：t=80 時 `total` 已 shift，近 5 日窗涵蓋原始日 75..79 → 4×1 + 5 = 9；近 60 日窗涵蓋原始日 20..79 → 59×1 + 5 = 64，日均 64/60。若實作的視窗邊界差一天，先對照這個手算，不要改測試。

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_level1_news_features.py -q`
Expected: 4 passed

- [ ] **Step 5: 覆蓋率健檢（用現有兩個月資料，只是驗管線能跑）**

Run:
```bash
cd backend && .venv/Scripts/python.exe -X utf8 -c "
import sqlite3, pickle
from app.research.level1 import news_features as nf
p = pickle.load(open('data/level1_v3_targets.pkl','rb')) if __import__('os').path.exists('data/level1_v3_targets.pkl') else pickle.load(open('data/level1_targets.pkl','rb'))
close, mask = p['close'], p['universe']
con = sqlite3.connect('data/twa.db'); ev = nf.load_events_long(con, str(close.index[0]), str(close.index[-1])); con.close()
r, raw = nf.build_news_features(ev, close.index, close.columns, mask)
cov = (raw['news_none60'] == 0).where(mask).mean(axis=1)
print('events', len(ev)); print(cov.groupby(cov.index.str[:4]).mean().round(3))"
```
Expected: 2020~2025 覆蓋率 ≈ 0（尚未回補），2026 有非零值。這條指令就是回補完成後要再跑一次、把逐年覆蓋率貼進 findings 的那條（設計 §2.5：覆蓋 < 60% 的年份不進 dev）。

- [ ] **Step 6: Commit**

```bash
git add backend/app/research/level1/news_features.py backend/tests/test_level1_news_features.py
git commit -m "feat(level1): 消息面特徵矩陣（lag-1、量／爆增／類別旗標／無覆蓋指示）

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 7: 全套測試 + lint**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check app scripts tests`
Expected: 全綠、All checks passed

---

## 與研究計畫的接點（本計畫不做，記錄給研究計畫的 `+W4` 步）

研究計畫的 `level1_v3_ablation.py` 加 `+W4` 步時：
1. `FAMILIES["W4"] = nf.NEWS_FAMILY`；`build_feature_set` 多收 `news_to_rank, news_raw`，分別併進 `to_rank`／`raw`。
2. 先跑 Task 6 Step 5 的覆蓋率；覆蓋 < 60% 的年份從 dev 期間剔除（以 `PERIODS` 另開 `dev_news` 區間），獨立一輪，不與價量步驟混算。
3. 分類關鍵字是 2026 年寫的，套回 2020 有語彙漂移風險——三個 flag 逐年看 IC 方向是否一致。

## Self-Review 結果

**Spec coverage（§2.4、§3）**
- §3.3-1 每日全池收錄、`fetched_at` 必記 → Task 1、3、4 ✓
- §3.3-2 歷史回補、可中斷續跑、`backfill:` 前綴、付費層 → Task 5 ✓（免費層 fail-fast 有測試）
- §3.4 回補偏誤（生存偏誤／來源結構漂移／關鍵字漂移）→ 無法用程式消除，已寫進 Task 6 接點與研究計畫 findings 要求；`news_burst` 用 rank 表示吸收趨勢 ✓
- §2.4 六個特徵、lag 1、`news_none60` 缺值≠零、不做極性／PTT → Task 6 ✓
- §3.2 token 不進版控、不進對話 → 走既有 `credentials` ✓

**Placeholder scan**：Task 4 Step 3 的 `cfg.db_path` 標了「以實際欄位為準」——這是既有設定名稱查證，不是實作留白；其他無 TBD。

**Type consistency**
- `upsert_events(session, df, *, fetched_at, trading_date, source_prefix="")` 在 Task 3 定義，Task 4、5 呼叫簽名一致 ✓
- `fetch_market_news_day(d: date)` Task 2 定義，Task 4 `_FM` 假物件與 Task 5 `_Src` 同名同參 ✓
- `EVENT_COLS` 末欄 `published_at`，Task 3 測試 `_df` 欄序與之一致 ✓
- `build_news_features` 回 `(to_rank, raw)`，與研究計畫 `build_direction_features` 同型，方便併入 `FeatureSet` ✓
