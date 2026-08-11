# 法人目標價（FactSet 共識）設計

日期：2026-08-12
狀態：已與使用者確認

## 目標

個股頁顯示 FactSet 分析師共識目標價：中位數、最高/最低、人數、評級分布、
歷次調整細項、是否達標；K 線圖可開關目標價水平線。

**本期不做**：個別券商各自的目標價（免費來源已被鉅亨匿名化為 Factset，
新聞抽取覆蓋差；等有可靠來源再加）、推薦頁顯示、全市場調整清單頁。

## 資料來源

- 鉅亨 `tw_forecast` 分類 JSON API（免認證）：
  `https://api.cnyes.com/media/api/v1/newslist/category/tw_forecast?page=N&limit=30`
- 每則「鉅亨速報 - Factset 最新調查」內文為固定格式，regex 抽取：
  - 中位數目標價與前值/方向（「中位數由665元下修至640元」或「預估目標價為80元」）
  - 最高估值、最低估值（「其中最高估值824元，最低估值520元」）
  - 分析師人數（「共12位分析師」）
  - 評級分布（「積極樂觀12位、保持中立1位、保守悲觀0位」）
  - EPS 預估（EPS 型標題「EPS預估上修至3.36元」，順手存）
- 股號從標題「(4958-TW)」抽取；抽不到目標價的則跳過。
- `ratediff.aspx` 不採用（同源資料表格版，需處理 ASP.NET viewstate、無額外資訊）。

## 儲存

新表 `target_prices`：PK `(stock_id, date)`，欄位：
`target_price`（中位數）、`prev_target`、`direction`（up/down/flat/new）、
`target_high`、`target_low`、`analyst_count`、
`rating_bull`、`rating_neutral`、`rating_bear`、`eps_est`、
`news_id`（去重）、`title`。

同日同股多筆時保留最新一則（news_id 較大者覆蓋）。

## 抓取

- 新 `backend/app/sources/cnyes_forecast.py`（BaseSource 模式）；
  HTTP 失敗 graceful 回空，不拖垮 pipeline。
- 每日 pipeline 增量：翻頁抓到「整頁 news_id 都已存在」即停。
- 首次（表空）自動回補：往回翻頁直到 API 無資料或最舊一筆超過 180 天。

## 達標判定

- 每筆目標價有效期間＝發布日 → 被下一筆取代日（最新一筆到今日）。
- 期間內盤中最高價（DailyPrice.high）≥ target_price → 已達標（記首次達標日）。
- 無觀察窗概念（目標價到被更新為止都有效）；不需 pending 狀態。
- 即時計算（讀 API 時算），不入庫。

## API

`GET /stocks/{stock_id}/target-price` →

```json
{
  "stock_id": "4958",
  "latest": {
    "date": "2026-08-11", "target_price": 640.0, "prev_target": 665.0,
    "direction": "down", "target_high": 824.0, "target_low": 520.0,
    "analyst_count": 12, "rating_bull": 12, "rating_neutral": 1, "rating_bear": 0,
    "upside_pct": 30.6, "hit": false, "hit_date": null
  },
  "history": [ "…同 latest 結構（無 upside_pct）…" ]
}
```

無資料 → `latest: null, history: []`。`upside_pct` ＝ 目標價/最新收盤 − 1。

## 前端（個股頁）

右欄新卡片「法人目標價（FactSet 共識）」：

- 中位數目標價（大字）＋隱含漲幅（正紅/負綠，同站配色）
- 區間「最低 520 ~ 最高 824」、分析師 N 位
- 評級分布橫條（樂觀/中立/悲觀）
- 達標 badge：已達標（金、含日期）／未達標（灰）
- 歷次調整清單（細項）：日期、前值→新值、方向、該筆是否達標；預設收合可展開
- **K 線水平線開關**（預設開、存 localStorage `tp-line`）：
  開啟時 K 線畫粉色 `#f472b6` 虛線，標籤「法人目標價 640」
- 無資料顯示「無 FactSet 共識目標價（多為中小型股未被外資覆蓋）」

## 錯誤處理

- 來源壞掉：當日抓不到就沿用既有資料，pipeline 其他步驟不受影響。
- target-price API 失敗：卡片不顯示，K 線與其他區塊照常。

## 測試

- regex 抽取：目標價調整型、EPS 型、內文缺目標價三種 case（純函式）。
- 達標判定純函式（含「被取代日」邊界）。
- fetcher 用假 JSON 測增量停止條件。
