# K 線推薦標記 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 個股 K 線圖上標出歷史被推薦日（波段軌），三色區分達標／未達標／評估中。

**Architecture:** 新後端 endpoint 查 `scores` 表推薦日 → 純函式合併連續段落取起始日 → 重用 `_lookback_review()` 判定狀態；前端以 lightweight-charts v4 `setMarkers()` 疊圖。

**Tech Stack:** FastAPI + SQLAlchemy（backend）、React + react-query + lightweight-charts 4.x（frontend）。

## Global Constraints

- 「被推薦」＝ wave 軌 `passed_filter=True` **或** `passed_styles` 非空（與回看 `_build_lookback_response` 成員口徑一致）。
- 達標觀察窗 `_MARK_HORIZON = 30` 交易日（與 `poppability._H` 對齊）；噴出門檻沿用 `_lookback_review` 的 `_POP_TARGET = 0.10`。
- 段落中斷 ≥ 5 個交易日（`_MARK_GAP = 5`）才算新段落。
- 顏色：hit 金 `#f59e0b` arrowUp；miss 灰 `#6b7280` circle；pending 藍 `#38bdf8` circle。皆 belowBar。
- `ret_pct`（hit 時）＝期間 MFE（最大有利偏移%，`_lookback_review` 現成回傳值）。

---

### Task 1: 後端純函式（段落合併＋狀態判定）＋測試

**Files:**
- Modify: `backend/app/api/routes.py`（`_lookback_review` 附近新增兩個模組層函式與常數）
- Test: `backend/tests/test_recommendation_marks.py`（新檔）

**Interfaces:**
- Produces: `_mark_segments(rec_dates: list[date], trade_dates: list[date], gap: int = 5) -> list[date]`（回段落起始日，升冪）
- Produces: `_mark_status(hit_pop: bool, days_to_pop: int | None, days_elapsed: int, horizon: int = 30) -> str`（回 `"hit" | "miss" | "pending"`）

- [ ] **Step 1: 寫失敗測試**

```python
"""K 線推薦標記：段落合併與狀態判定（純函式，免 DB）。"""

from __future__ import annotations

from datetime import date, timedelta

from app.api.routes import _mark_segments, _mark_status


def _days(n: int) -> list[date]:
    """n 個連續交易日（週末略過不影響邏輯，直接用連續日）。"""
    base = date(2026, 1, 5)
    return [base + timedelta(days=i) for i in range(n)]


def test_consecutive_days_merge_into_one_segment():
    td = _days(20)
    assert _mark_segments([td[3], td[4], td[5]], td) == [td[3]]


def test_short_gap_stays_same_segment():
    td = _days(20)
    # 中斷 4 個交易日（<5）仍算同段
    assert _mark_segments([td[3], td[8]], td) == [td[3]]


def test_long_gap_starts_new_segment():
    td = _days(20)
    # 中斷 5 個交易日（≥5）→ 新段落
    assert _mark_segments([td[3], td[9]], td) == [td[3], td[9]]


def test_dates_missing_from_calendar_are_skipped():
    td = _days(20)
    stray = date(2030, 1, 1)
    assert _mark_segments([td[3], stray], td) == [td[3]]


def test_status_hit_within_horizon():
    assert _mark_status(True, 12, 40) == "hit"


def test_status_hit_after_horizon_counts_as_miss():
    assert _mark_status(True, 35, 40) == "miss"


def test_status_miss_when_window_elapsed():
    assert _mark_status(False, None, 30) == "miss"


def test_status_pending_when_window_open():
    assert _mark_status(False, None, 5) == "pending"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && python -m pytest tests/test_recommendation_marks.py -v`
Expected: FAIL（ImportError: `_mark_segments` 不存在）

- [ ] **Step 3: 最小實作**（放在 `routes.py` 的 `_lookback_review` 之後）

```python
_MARK_GAP = 5  # 推薦中斷 ≥5 個交易日視為新段落
_MARK_HORIZON = 30  # 會噴觀察窗（與 poppability._H 對齊）


def _mark_segments(
    rec_dates: list[date], trade_dates: list[date], gap: int = _MARK_GAP
) -> list[date]:
    """連續推薦日合併成段落、回起始日。中斷（未推薦的交易日數）≥ gap 才算新段。"""
    idx = {d: i for i, d in enumerate(trade_dates)}
    starts: list[date] = []
    prev_i: int | None = None
    for d in rec_dates:
        i = idx.get(d)
        if i is None:
            continue
        if prev_i is None or (i - prev_i - 1) >= gap:
            starts.append(d)
        prev_i = i
    return starts


def _mark_status(
    hit_pop: bool, days_to_pop: int | None, days_elapsed: int, horizon: int = _MARK_HORIZON
) -> str:
    """段落起始日的達標狀態：30 交易日內噴=hit；窗走完沒噴=miss；窗未走完=pending。"""
    if hit_pop and days_to_pop is not None and days_to_pop <= horizon:
        return "hit"
    if days_elapsed >= horizon:
        return "miss"
    return "pending"
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && python -m pytest tests/test_recommendation_marks.py -v`
Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes.py backend/tests/test_recommendation_marks.py
git commit -m "feat: K 線推薦標記純函式（段落合併＋狀態判定）"
```

### Task 2: 後端 schema ＋ endpoint

**Files:**
- Modify: `backend/app/api/schemas.py`（`OhlcvResponse` 附近新增兩個 model）
- Modify: `backend/app/api/routes.py`（`stock_ohlcv` 附近新增 endpoint；import 補 schema）

**Interfaces:**
- Consumes: Task 1 的 `_mark_segments` / `_mark_status`、既有 `_lookback_review` / `_latest_score_date`
- Produces: `GET /stocks/{stock_id}/recommendation-marks?days=120` → `RecommendationMarksResponse`

- [ ] **Step 1: schemas.py 新增**

```python
class RecommendationMark(BaseModel):
    """K 線上的推薦段落標記（起始日）。"""

    date: date
    status: str  # "hit"（30日內噴）| "miss"（窗走完沒噴）| "pending"（窗未走完）
    hit_date: date | None = None  # 首次摸到 +10% 的交易日（僅 hit）
    ret_pct: float | None = None  # 期間 MFE %（僅 hit）


class RecommendationMarksResponse(BaseModel):
    stock_id: str
    marks: list[RecommendationMark]
```

- [ ] **Step 2: routes.py 新增 endpoint**（`stock_ohlcv` 之後；import 區補 `RecommendationMark, RecommendationMarksResponse`）

```python
@router.get("/stocks/{stock_id}/recommendation-marks", response_model=RecommendationMarksResponse)
def stock_recommendation_marks(
    stock_id: str,
    days: int = Query(120, ge=20, le=3000),
    session: Session = Depends(get_session),
) -> RecommendationMarksResponse:
    """K 線推薦標記：波段軌被推薦的段落起始日 + 達標狀態（口徑同回看）。"""
    today_d = _latest_score_date(session)
    if today_d is None:
        return RecommendationMarksResponse(stock_id=stock_id, marks=[])
    rows = session.execute(
        select(models.Score.date, models.Score.passed_filter, models.Score.passed_styles)
        .where(models.Score.stock_id == stock_id, models.Score.track == "wave")
        .order_by(models.Score.date)
    ).all()
    rec_dates = [r[0] for r in rows if r[1] or r[2]]  # 回看同口徑：過硬篩或有風格標籤
    if not rec_dates:
        return RecommendationMarksResponse(stock_id=stock_id, marks=[])
    trade_dates = session.execute(
        select(models.DailyPrice.date)
        .where(models.DailyPrice.stock_id == stock_id, models.DailyPrice.date <= today_d)
        .order_by(models.DailyPrice.date)
    ).scalars().all()
    visible = set(trade_dates[-days:])
    marks: list[RecommendationMark] = []
    for d0 in _mark_segments(rec_dates, trade_dates):
        if d0 not in visible:
            continue
        rv = _lookback_review(session, stock_id, d0, today_d)
        status = _mark_status(rv.hit_pop, rv.days_to_pop, rv.days_elapsed)
        marks.append(
            RecommendationMark(
                date=d0,
                status=status,
                hit_date=rv.hit_pop_date if status == "hit" else None,
                ret_pct=rv.mfe_pct if status == "hit" else None,
            )
        )
    return RecommendationMarksResponse(stock_id=stock_id, marks=marks)
```

- [ ] **Step 3: 驗證**

Run: `cd backend && python -m pytest tests/ -v`（全綠）＋ `python -c "from app.api.routes import stock_recommendation_marks"`
Expected: PASS / 無 ImportError

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/schemas.py backend/app/api/routes.py
git commit -m "feat: 推薦標記 endpoint /stocks/{id}/recommendation-marks"
```

### Task 3: 前端 client 型別＋hook

**Files:**
- Modify: `frontend/src/api/client.ts`（`useOhlcv` 附近）

**Interfaces:**
- Produces: `MarkDTO`、`useRecommendationMarks(stockId: string | undefined, days = 120)`

- [ ] **Step 1: 新增型別與 hook**

```ts
export interface MarkDTO {
  date: string;
  status: "hit" | "miss" | "pending";
  hit_date: string | null;
  ret_pct: number | null;
}

export interface RecommendationMarksResponse {
  stock_id: string;
  marks: MarkDTO[];
}

export function useRecommendationMarks(stockId: string | undefined, days = 120) {
  return useQuery({
    queryKey: ["recommendation-marks", stockId, days],
    queryFn: () => getJson<RecommendationMarksResponse>(`/stocks/${stockId}/recommendation-marks?days=${days}`),
    enabled: !!stockId,
  });
}
```

- [ ] **Step 2: 型別檢查** Run: `cd frontend && npx tsc --noEmit`　Expected: 無錯誤

- [ ] **Step 3: Commit** `git add frontend/src/api/client.ts && git commit -m "feat: 推薦標記 API hook"`

### Task 4: KLineChart 畫標記＋頁面接線

**Files:**
- Modify: `frontend/src/components/KLineChart.tsx`
- Modify: `frontend/src/pages/StockDetailPage.tsx`

**Interfaces:**
- Consumes: Task 3 的 `MarkDTO` / `useRecommendationMarks`

- [ ] **Step 1: KLineChart 加 `marks` props**

import 補 `type MarkDTO`（自 `../api/client`）與 `type SeriesMarker`（自 `lightweight-charts`）。簽名改為：

```ts
export function KLineChart({ candles, levels = [], marks = [] }: { candles: Candle[]; levels?: LevelDTO[]; marks?: MarkDTO[] }) {
```

在支撐/壓力線之後、`fitContent()` 之前加：

```ts
    // 推薦標記：金✓=30日內達標、灰✗=未達標、藍…=評估中（口徑同回看）
    if (marks.length > 0) {
      const times = new Set(candles.map((c) => c.date));
      candleSeries.setMarkers(
        marks
          .filter((m) => times.has(m.date))
          .map((m): SeriesMarker<Time> => ({
            time: m.date as Time,
            position: "belowBar",
            shape: m.status === "hit" ? "arrowUp" : "circle",
            color: m.status === "hit" ? "#f59e0b" : m.status === "miss" ? "#6b7280" : "#38bdf8",
            text: m.status === "hit" ? `推薦✓${m.ret_pct != null ? ` +${m.ret_pct}%` : ""}` : m.status === "miss" ? "推薦✗" : "推薦…",
          })),
      );
    }
```

useEffect 依賴陣列改為 `[candles, levels, marks]`。

- [ ] **Step 2: StockDetailPage 接線**

import 補 `useRecommendationMarks`；`useLevels` 下一行加：

```ts
  const { data: marks } = useRecommendationMarks(id, klineDays);
```

`<KLineChart ... />` 加 `marks={marks?.marks ?? []}`，Card 標題改為 `"K 線（日K，疊均線 MA5/20/60 + 支撐/壓力 + 推薦標記）"`。

- [ ] **Step 3: 型別檢查** Run: `cd frontend && npx tsc --noEmit`　Expected: 無錯誤

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/KLineChart.tsx frontend/src/pages/StockDetailPage.tsx
git commit -m "feat: K 線圖顯示推薦標記（達標金／未達標灰／評估中藍）"
```

### Task 5: 端對端驗證

- [ ] 啟動 dev server，開一檔近期被推薦過的個股詳情頁
- [ ] 確認 K 棒下方出現標記、三色與文字正確、切換時間範圍標記跟著更新
- [ ] marks API 404/失敗時 K 線仍正常顯示
