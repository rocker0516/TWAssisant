# 法人目標價（FactSet 共識）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 個股頁顯示 FactSet 共識目標價（中位數/最高/最低/人數/評級分布/歷次調整/達標），K 線可開關目標價水平線。

**Architecture:** 鉅亨 `tw_forecast` JSON API → 純函式 regex 解析 → `target_prices` 表（冪等 upsert）→ pipeline step 每日增量（首次自動回補）→ 讀 API 即時算達標 → 前端卡片＋K 線 priceLine。

**Tech Stack:** FastAPI + SQLAlchemy(SQLite) + httpx（backend）、React + react-query + lightweight-charts 4.x（frontend）。

## Global Constraints

- 來源：`https://api.cnyes.com/media/api/v1/newslist/category/tw_forecast?page=N&limit=30`，免 token。
- 回補上限：API 無資料或最舊一筆超過 **180 天**即停；增量：整頁 news_id 皆已存在即停。
- 達標＝有效期間（發布日→被下一筆取代日；最新一筆到今日）內 `DailyPrice.high ≥ target_price`。無 pending。
- K 線目標價線：粉色 `#f472b6` 虛線，標籤「法人目標價」；開關預設開、localStorage key `tp-line`。
- 抓取失敗 graceful 回空，不拖垮 pipeline。

---

### Task 1: 解析純函式 + CnyesForecastSource

**Files:**
- Create: `backend/app/sources/cnyes_forecast.py`
- Test: `backend/tests/test_target_price.py`（新檔，先放解析測試）

**Interfaces:**
- Produces: `parse_forecast_item(title: str, content: str, publish_date: date) -> dict | None`
  回 `{stock_id, date, target_price, prev_target, direction, target_high, target_low, analyst_count, rating_bull, rating_neutral, rating_bear, eps_est, news_id(呼叫端補), title}` 或 None（非台股/無目標價）。
- Produces: `CnyesForecastSource(BaseSource)`，方法
  `fetch_target_prices(known_ids: set[int], min_date: date) -> list[dict]`
  （翻頁抓、每則呼叫 parse、補 news_id；整頁皆 known 或最舊 < min_date 即停；回 rows 給 repo upsert）。

- [ ] **Step 1: 寫失敗測試**（解析部分）

```python
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


def test_parse_skips_non_tw_or_no_target():
    assert parse_forecast_item("Factset 最新調查：Oscar(OSCR-US)EPS預估上修", "無", date(2026, 8, 11)) is None
    assert parse_forecast_item("台股盤後速記", "今日大盤...", date(2026, 8, 11)) is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_target_price.py -v`
Expected: FAIL（ModuleNotFoundError: cnyes_forecast）

- [ ] **Step 3: 實作 `cnyes_forecast.py`**

```python
"""鉅亨「台股預估」快報 → FactSet 共識目標價。

來源：https://api.cnyes.com/media/api/v1/newslist/category/tw_forecast
每則快報內文固定格式，regex 抽中位數/最高低/人數/評級分布。
抓失敗 graceful 回空（SourceError 由呼叫端吞），不可拖垮 pipeline。
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime

from .base import BaseSource

_CATEGORY_URL = "https://api.cnyes.com/media/api/v1/newslist/category/tw_forecast"

_RE_STOCK = re.compile(r"[（(](\d{4,6})-TW[)）]")
_NUM = r"(-?\d+(?:,\d{3})*(?:\.\d+)?)"
# 目標價型內文：「提出目標價估值：中位數由665元下修至640元」／「中位數維持640元」
_RE_TP_REVISE = re.compile(rf"目標價估值：中位數由{_NUM}元(上修|下修)至{_NUM}元")
_RE_TP_KEEP = re.compile(rf"目標價估值：中位數(?:維持|為){_NUM}元")
# EPS 型：目標價只在「預估目標價為80元」出現（標題與內文皆可）
_RE_TP_PLAIN = re.compile(rf"預估目標價為{_NUM}元")
_RE_HILO = re.compile(rf"最高估值{_NUM}元，最低估值{_NUM}元")
_RE_COUNT = re.compile(r"共(?:有)?(\d+)位分析師")
_RE_RATING = re.compile(r"積極樂觀(\d+)位、保持中立(\d+)位、保守悲觀(\d+)位")
_RE_EPS = re.compile(rf"EPS預估(?:上修|下修)至{_NUM}元")


def _f(s: str) -> float:
    return float(s.replace(",", ""))


def parse_forecast_item(title: str, content: str, publish_date: date) -> dict | None:
    """單則快報 → target_prices row（不含 news_id，由呼叫端補）。非台股或無目標價回 None。"""
    m_stock = _RE_STOCK.search(title)
    if not m_stock:
        return None
    text = re.sub(r"<[^>]+>", " ", html.unescape(content or ""))

    target = prev = None
    direction = "new"
    m = _RE_TP_REVISE.search(text)
    if m:
        prev, target = _f(m.group(1)), _f(m.group(3))
        direction = "up" if m.group(2) == "上修" else "down"
    else:
        m = _RE_TP_KEEP.search(text)
        if m:
            target = _f(m.group(1))
            direction = "flat"
        else:
            m = _RE_TP_PLAIN.search(text) or _RE_TP_PLAIN.search(title)
            if m:
                target = _f(m.group(1))
    if target is None:
        return None

    hilo = _RE_HILO.search(text)
    cnt = _RE_COUNT.search(text)
    rating = _RE_RATING.search(text)
    eps = _RE_EPS.search(title) or _RE_EPS.search(text)
    # EPS 型內文的「最高/最低估值」是 EPS 區間，不是目標價區間 → 只有目標價型才收
    is_tp_body = "目標價估值" in text
    return {
        "stock_id": m_stock.group(1),
        "date": publish_date,
        "target_price": target,
        "prev_target": prev,
        "direction": direction,
        "target_high": _f(hilo.group(1)) if hilo and is_tp_body else None,
        "target_low": _f(hilo.group(2)) if hilo and is_tp_body else None,
        "analyst_count": int(cnt.group(1)) if cnt else None,
        "rating_bull": int(rating.group(1)) if rating else None,
        "rating_neutral": int(rating.group(2)) if rating else None,
        "rating_bear": int(rating.group(3)) if rating else None,
        "eps_est": _f(eps.group(1)) if eps else None,
        "title": title,
    }


class CnyesForecastSource(BaseSource):
    name = "cnyes_forecast"
    requires_token = False

    def _probe(self) -> None:
        self._request(_CATEGORY_URL, {"page": 1, "limit": 1})

    def fetch_target_prices(self, known_ids: set[int], min_date: date) -> list[dict]:
        """翻頁抓快報：整頁 news_id 皆已存在（增量到頂）或最舊 < min_date（回補到底）即停。"""
        rows: list[dict] = []
        page = 1
        while True:
            j = self._request(_CATEGORY_URL, {"page": page, "limit": 30}).json()
            data = (j.get("items") or {}).get("data") or []
            if not data:
                break
            all_known = True
            oldest = None
            for it in data:
                nid = it.get("newsId")
                ts = it.get("publishAt")
                if nid is None or ts is None:
                    continue
                d = datetime.fromtimestamp(ts).date()
                oldest = d if oldest is None or d < oldest else oldest
                if nid in known_ids:
                    continue
                all_known = False
                parsed = parse_forecast_item(it.get("title") or "", it.get("content") or "", d)
                if parsed:
                    parsed["news_id"] = nid
                    rows.append(parsed)
            if all_known or (oldest is not None and oldest < min_date):
                break
            page += 1
        return rows
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_target_price.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/sources/cnyes_forecast.py backend/tests/test_target_price.py
git commit -m "feat: 鉅亨 FactSet 目標價快報來源與解析"
```

### Task 2: TargetPrice model + repository + pipeline step

**Files:**
- Modify: `backend/app/storage/models.py`（`Score` class 之前新增）
- Modify: `backend/app/storage/repositories.py`（新增 repository）
- Modify: `backend/app/scheduler/steps.py`（新增 `TargetPriceStep`）
- Modify: `backend/app/scheduler/run.py`（兩個 step 清單各加 `TargetPriceStep()`，放 `NewsStep()` 之後）

**Interfaces:**
- Consumes: Task 1 的 `CnyesForecastSource.fetch_target_prices`
- Produces: `models.TargetPrice`（PK `(stock_id, date)`）、`TargetPriceRepository`

- [ ] **Step 1: models.py 新增**

```python
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
```

- [ ] **Step 2: repositories.py 新增**

```python
class TargetPriceRepository(BaseRepository[models.TargetPrice]):
    model = models.TargetPrice
```

- [ ] **Step 3: steps.py 新增 TargetPriceStep**

```python
class TargetPriceStep(PipelineStep):
    """FactSet 共識目標價（鉅亨 tw_forecast）。首次自動回補 180 天，之後增量。"""

    name = "target_price"
    required = False

    def run(self, ctx: PipelineContext) -> dict:
        from ..sources.cnyes_forecast import CnyesForecastSource

        session = ctx.session
        known = set(
            session.execute(
                select(models.TargetPrice.news_id).where(models.TargetPrice.news_id.isnot(None))
            ).scalars().all()
        )
        min_date = ctx.trading_date - timedelta(days=180)
        src = CnyesForecastSource()
        try:
            rows = src.fetch_target_prices(known, min_date)
        except SourceError as exc:
            return {"ok": False, "reason": exc.reason}
        finally:
            src.close()
        # 只留 universe 內股票（FK 保護）；同日同股取 news_id 較大者
        valid_ids = set(session.execute(select(models.Stock.id)).scalars().all())
        best: dict[tuple[str, object], dict] = {}
        for r in rows:
            if r["stock_id"] not in valid_ids:
                continue
            key = (r["stock_id"], r["date"])
            if key not in best or (r.get("news_id") or 0) > (best[key].get("news_id") or 0):
                best[key] = r
        n = repo.TargetPriceRepository().upsert_many(session, list(best.values()))
        return {"ok": True, "rows": n}
```

- [ ] **Step 4: run.py 兩個清單的 `NewsStep()` 後各加 `TargetPriceStep()`**，並在 steps.py 的 import 不需要新增（同檔）。確認 run.py import 來源是 `from .steps import ...`，加上 `TargetPriceStep`。

- [ ] **Step 5: 驗證（不打真 API）**

Run: `cd backend && ./.venv/Scripts/python.exe -c "from app.scheduler.run import *; from app.storage.database import init_db; init_db(); print('ok')"`
Expected: `ok`（建表成功、import 無誤）

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: 全綠

- [ ] **Step 6: Commit**

```bash
git add backend/app/storage/models.py backend/app/storage/repositories.py backend/app/scheduler/steps.py backend/app/scheduler/run.py
git commit -m "feat: target_prices 表與每日抓取 pipeline step"
```

### Task 3: 達標判定純函式 + API endpoint

**Files:**
- Modify: `backend/app/api/routes.py`（`_mark_status` 之後加 `_tp_windows`；`stock_recommendation_marks` 之後加 endpoint）
- Modify: `backend/app/api/schemas.py`（`RecommendationMarksResponse` 之後新增）
- Test: `backend/tests/test_target_price.py`（追加達標測試）

**Interfaces:**
- Produces: `_tp_windows(entries: list[tuple[date, float]], today: date) -> list[tuple[date, date, float]]`
  （每筆目標價的 `(生效日, 迄日, 目標價)`；迄日＝下一筆生效日前一天或 today）
- Produces: `GET /stocks/{stock_id}/target-price` → `TargetPriceResponse`

- [ ] **Step 1: 追加失敗測試**

```python
from app.api.routes import _tp_windows
from datetime import timedelta


def test_tp_windows_validity_ranges():
    d1, d2, today = date(2026, 6, 1), date(2026, 7, 1), date(2026, 8, 11)
    w = _tp_windows([(d1, 100.0), (d2, 120.0)], today)
    assert w == [
        (d1, d2 - timedelta(days=1), 100.0),
        (d2, today, 120.0),
    ]


def test_tp_windows_single_entry_runs_to_today():
    d1, today = date(2026, 6, 1), date(2026, 8, 11)
    assert _tp_windows([(d1, 100.0)], today) == [(d1, today, 100.0)]
```

- [ ] **Step 2: 跑測試確認失敗**（ImportError）

- [ ] **Step 3: routes.py 加純函式與 endpoint、schemas.py 加 model**

schemas.py（`RecommendationMarksResponse` 之後）：

```python
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
```

routes.py import 補 `TargetPriceEntry, TargetPriceResponse`；`_mark_status` 之後：

```python
def _tp_windows(
    entries: list[tuple[date, float]], today: date
) -> list[tuple[date, date, float]]:
    """每筆目標價的有效期間：(生效日, 迄日, 目標價)。迄日＝下一筆生效日前一天；最新一筆到 today。"""
    from datetime import timedelta

    out: list[tuple[date, date, float]] = []
    for i, (d, tp) in enumerate(entries):
        end = entries[i + 1][0] - timedelta(days=1) if i + 1 < len(entries) else today
        out.append((d, end, tp))
    return out
```

`stock_recommendation_marks` 之後：

```python
@router.get("/stocks/{stock_id}/target-price", response_model=TargetPriceResponse)
def stock_target_price(
    stock_id: str,
    session: Session = Depends(get_session),
) -> TargetPriceResponse:
    """FactSet 共識目標價：最新一筆＋歷次調整，每筆附有效期間內是否達標。"""
    rows = session.execute(
        select(models.TargetPrice)
        .where(models.TargetPrice.stock_id == stock_id)
        .order_by(models.TargetPrice.date)
    ).scalars().all()
    if not rows:
        return TargetPriceResponse(stock_id=stock_id, latest=None, history=[])

    today_d = session.execute(
        select(func.max(models.DailyPrice.date)).where(models.DailyPrice.stock_id == stock_id)
    ).scalar() or rows[-1].date
    windows = _tp_windows([(r.date, r.target_price) for r in rows], today_d)

    price_rows = session.execute(
        select(models.DailyPrice.date, models.DailyPrice.high)
        .where(
            models.DailyPrice.stock_id == stock_id,
            models.DailyPrice.date >= rows[0].date,
            models.DailyPrice.high.isnot(None),
        )
        .order_by(models.DailyPrice.date)
    ).all()
    latest_close = session.execute(
        select(models.DailyPrice.close)
        .where(models.DailyPrice.stock_id == stock_id, models.DailyPrice.close.isnot(None))
        .order_by(models.DailyPrice.date.desc())
        .limit(1)
    ).scalar()

    entries: list[TargetPriceEntry] = []
    for r, (start, end, tp) in zip(rows, windows):
        hit_date = next((d for d, h in price_rows if start <= d <= end and h >= tp), None)
        entries.append(
            TargetPriceEntry(
                date=r.date, target_price=r.target_price, prev_target=r.prev_target,
                direction=r.direction, target_high=r.target_high, target_low=r.target_low,
                analyst_count=r.analyst_count, rating_bull=r.rating_bull,
                rating_neutral=r.rating_neutral, rating_bear=r.rating_bear,
                eps_est=r.eps_est, hit=hit_date is not None, hit_date=hit_date,
            )
        )
    latest = entries[-1]
    if latest_close:
        latest.upside_pct = round((latest.target_price / latest_close - 1) * 100, 2)
    return TargetPriceResponse(stock_id=stock_id, latest=latest, history=list(reversed(entries)))
```

- [ ] **Step 4: 跑測試全綠** `./.venv/Scripts/python.exe -m pytest tests/ -q`

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes.py backend/app/api/schemas.py backend/tests/test_target_price.py
git commit -m "feat: 目標價 endpoint /stocks/{id}/target-price（含達標判定）"
```

### Task 4: 前端 hook + 卡片 + K 線水平線

**Files:**
- Modify: `frontend/src/api/client.ts`（`useRecommendationMarks` 之後）
- Create: `frontend/src/components/TargetPriceCard.tsx`
- Modify: `frontend/src/components/KLineChart.tsx`（加 `targetPrice` props）
- Modify: `frontend/src/pages/StockDetailPage.tsx`（接線；卡片放右欄「籌碼」上方）

**Interfaces:**
- Consumes: Task 3 的 `TargetPriceResponse` JSON
- Produces: `useTargetPrice(stockId)`、`<TargetPriceCard data lineOn onToggleLine />`、`KLineChart` 的 `targetPrice?: number | null`

- [ ] **Step 1: client.ts**

```ts
export interface TargetPriceEntry {
  date: string;
  target_price: number;
  prev_target: number | null;
  direction: "up" | "down" | "flat" | "new";
  target_high: number | null;
  target_low: number | null;
  analyst_count: number | null;
  rating_bull: number | null;
  rating_neutral: number | null;
  rating_bear: number | null;
  eps_est: number | null;
  hit: boolean;
  hit_date: string | null;
  upside_pct: number | null;
}

export interface TargetPriceResponse {
  stock_id: string;
  latest: TargetPriceEntry | null;
  history: TargetPriceEntry[];
}

export function useTargetPrice(stockId: string | undefined) {
  return useQuery({
    queryKey: ["target-price", stockId],
    queryFn: () => getJson<TargetPriceResponse>(`/stocks/${stockId}/target-price`),
    enabled: !!stockId,
  });
}
```

- [ ] **Step 2: KLineChart 加 targetPrice priceLine**（支撐/壓力線區塊之後）

props 簽名加 `targetPrice = null`：

```ts
export function KLineChart({ candles, levels = [], marks = [], targetPrice = null }: { candles: Candle[]; levels?: LevelDTO[]; marks?: MarkDTO[]; targetPrice?: number | null }) {
```

```ts
    // 法人（FactSet 共識）目標價水平線（卡片開關控制 targetPrice 是否傳入）
    if (targetPrice != null) {
      candleSeries.createPriceLine({
        price: targetPrice,
        color: "#f472b6",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "法人目標價",
      });
    }
```

useEffect 依賴陣列改為 `[candles, levels, marks, targetPrice]`。

- [ ] **Step 3: TargetPriceCard.tsx**

```tsx
import type { TargetPriceResponse } from "../api/client";
import { changeColor, fmtNum } from "../lib/format";
import { useState } from "react";

const DIR_LABEL: Record<string, string> = { up: "調升", down: "調降", flat: "維持", new: "新增" };
const DIR_COLOR: Record<string, string> = { up: "text-up", down: "text-down", flat: "text-muted", new: "text-muted" };

export function TargetPriceCard({
  data,
  lineOn,
  onToggleLine,
}: {
  data: TargetPriceResponse;
  lineOn: boolean;
  onToggleLine: (on: boolean) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const tp = data.latest;
  return (
    <div className="rounded-xl border border-edge bg-panel p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <span className="text-sm font-semibold">法人目標價（FactSet 共識）</span>
        {tp && (
          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted">
            <input type="checkbox" checked={lineOn} onChange={(e) => onToggleLine(e.target.checked)} className="accent-pink-400" />
            K線顯示
          </label>
        )}
      </div>
      {!tp ? (
        <p className="text-sm text-muted">無 FactSet 共識目標價（多為中小型股未被外資覆蓋）。</p>
      ) : (
        <>
          <div className="flex items-end justify-between">
            <div>
              <span className="text-2xl font-bold tabular-nums">{fmtNum(tp.target_price)}</span>
              {tp.upside_pct != null && (
                <span className={`ml-2 text-sm tabular-nums ${changeColor(tp.upside_pct)}`}>
                  隱含 {tp.upside_pct > 0 ? "+" : ""}{tp.upside_pct}%
                </span>
              )}
            </div>
            {tp.hit ? (
              <span className="rounded bg-amber-500/20 px-1.5 py-0.5 text-xs text-amber-400">已達標 {tp.hit_date}</span>
            ) : (
              <span className="rounded bg-panel2 px-1.5 py-0.5 text-xs text-muted">未達標</span>
            )}
          </div>
          <div className="mt-2 flex flex-col gap-1 text-sm">
            <div className="flex justify-between"><span className="text-muted">估值區間</span><span className="tabular-nums">{fmtNum(tp.target_low)} ~ {fmtNum(tp.target_high)}</span></div>
            <div className="flex justify-between"><span className="text-muted">分析師</span><span>{tp.analyst_count ?? "—"} 位</span></div>
            <div className="flex justify-between">
              <span className="text-muted">最近調整</span>
              <span className={DIR_COLOR[tp.direction]}>
                {tp.date} {DIR_LABEL[tp.direction]}{tp.prev_target != null ? `（${fmtNum(tp.prev_target)}→${fmtNum(tp.target_price)}）` : ""}
              </span>
            </div>
          </div>
          {tp.rating_bull != null && tp.rating_neutral != null && tp.rating_bear != null && (
            <div className="mt-3">
              <div className="mb-1 flex justify-between text-xs text-muted">
                <span>樂觀 {tp.rating_bull}</span><span>中立 {tp.rating_neutral}</span><span>悲觀 {tp.rating_bear}</span>
              </div>
              <div className="flex h-1.5 overflow-hidden rounded bg-panel2">
                <div className="bg-up" style={{ flexGrow: tp.rating_bull }} />
                <div className="bg-gray-500" style={{ flexGrow: tp.rating_neutral }} />
                <div className="bg-down" style={{ flexGrow: tp.rating_bear }} />
              </div>
            </div>
          )}
          {data.history.length > 1 && (
            <div className="mt-3">
              <button onClick={() => setExpanded(!expanded)} className="text-xs text-sky-400 hover:underline">
                歷次調整（{data.history.length}）{expanded ? "▲" : "▼"}
              </button>
              {expanded && (
                <div className="mt-1.5 flex flex-col gap-1">
                  {data.history.map((h) => (
                    <div key={h.date} className="flex items-center justify-between text-xs">
                      <span className="text-muted">{h.date}</span>
                      <span className={DIR_COLOR[h.direction]}>
                        {DIR_LABEL[h.direction]}{h.prev_target != null ? ` ${fmtNum(h.prev_target)}→` : " "}{fmtNum(h.target_price)}
                      </span>
                      <span className={h.hit ? "text-amber-400" : "text-muted"}>{h.hit ? "✓達標" : "未達"}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          <p className="mt-3 text-[11px] text-muted">來源：鉅亨網 FactSet 調查，僅供參考非投資建議。</p>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 4: StockDetailPage 接線**

import 補 `useTargetPrice`、`TargetPriceCard`；component 內：

```ts
  const { data: targetPrice } = useTargetPrice(id);
  const [tpLineOn, setTpLineOn] = useState(() => localStorage.getItem("tp-line") !== "0");
  const toggleTpLine = (on: boolean) => { setTpLineOn(on); localStorage.setItem("tp-line", on ? "1" : "0"); };
```

`<KLineChart ...>` 加 `targetPrice={tpLineOn ? targetPrice?.latest?.target_price ?? null : null}`。

右欄 `<Card title="籌碼">` 之前加：

```tsx
          {targetPrice && <TargetPriceCard data={targetPrice} lineOn={tpLineOn} onToggleLine={toggleTpLine} />}
```

- [ ] **Step 5: 型別檢查** `cd frontend && npx tsc --noEmit`　Expected: 無錯誤

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/components/TargetPriceCard.tsx frontend/src/components/KLineChart.tsx frontend/src/pages/StockDetailPage.tsx
git commit -m "feat: 個股頁法人目標價卡片＋K 線目標價線（可開關）"
```

### Task 5: 端對端驗證

- [ ] 手動跑一次 TargetPriceStep（scripts 或 python -c）對真實 DB 抓資料，確認 target_prices 有列、內容合理
- [ ] 用驗證後端（port 8010、關 auth）＋ vite proxy 暫指 8010，開有目標價的個股頁：卡片數字正確、開關能隱藏/顯示 K 線粉線、歷次清單展開正常
- [ ] 還原 vite proxy、重打包 dist（使用者網站模式）
