# K 線推薦標記（被推薦＋達標）設計

日期：2026-08-12
狀態：已與使用者確認

## 目標

在個股頁面的 K 線圖上，標出這檔股票歷史上「被進場推薦」的日子，並以顏色區分結果：

- **達標（已噴）**：金色 `#f59e0b`，`arrowUp`，K 棒下方，文字 `推薦✓ +X%`
- **未達標**：灰色 `#6b7280`，圓點，K 棒下方，文字 `推薦✗`
- **評估中**（觀察窗未走完）：天藍 `#38bdf8`，圓點，K 棒下方，文字 `推薦…`

## 口徑（與「回看」功能完全一致）

- 只涵蓋**波段（wave）軌**「`passed_filter=True` 或 `passed_styles` 非空」的日子
  （與回看 `_build_lookback_response` 成員口徑一致；實作時發現回看用的是這個而非 `passed`）。
- 達標判斷直接重用 `backend/app/api/routes.py` 的 `_lookback_review()`：
  隔日最高價為錨點、**30 個交易日**（`poppability._H`）內是否摸到 +10%。不另寫第二份邏輯。
- `ret_pct` ＝ 期間 MFE（最大有利偏移%，`_lookback_review` 現成值）。
- 連續推薦日合併為一個「段落」，只在段落**起始日**放標記；
  中斷 **≥ 5 個交易日**才視為新段落。
- 段落狀態以起始日的回看結果為準：
  - `hit_pop=True` → `hit`（附達標日與報酬）
  - 觀察窗已走完仍未噴 → `miss`
  - 觀察窗未走完 → `pending`

## 後端

新 endpoint：`GET /stocks/{stock_id}/recommendation-marks?days=120`

1. 查該股 wave 軌 `passed=True` 的日期（區間對齊 K 線 days 參數，
   起始日判斷需多往前查一段以正確辨識段落邊界）。
2. 合併連續段落、取起始日。
3. 每段起始日呼叫 `_lookback_review()` 得出狀態。
4. 回傳：

```json
{ "marks": [ { "date": "2026-05-02", "status": "hit", "hit_date": "2026-05-10", "ret_pct": 12.3 } ] }
```

`status ∈ {"hit", "miss", "pending"}`；`hit_date` / `ret_pct` 僅 `hit` 時有值
（`ret_pct` 為達標當下相對錨點的報酬）。

## 前端

- `api/client.ts`：新增 `MarkDTO` 型別與 `useRecommendationMarks(id, days)` hook。
- `KLineChart.tsx`：新增 `marks?: MarkDTO[]` props，
  以 `candleSeries.setMarkers()` 依上述三色規格繪製（依日期排序）。
- `StockDetailPage.tsx`：撈 marks 傳入 KLineChart，`days` 與 `klineDays` 共用，
  切換時間範圍時標記同步更新。

## 錯誤處理

marks 為獨立 query；API 失敗或無資料時不畫標記，不影響 K 線本體。

## 測試

- 後端：段落合併（連續、中斷 <5 日、中斷 ≥5 日）與三種狀態各一個 case。
- 前端：目測驗證三色標記與時間範圍切換。

## 非目標（YAGNI）

- 長線軌標記（無達標口徑，暫不做）。
- 推薦區間背景帶。
- 達標結果入庫（維持即時計算）。
