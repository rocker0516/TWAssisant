# /app/level1 UI 改版設計（ML 進場推薦 FRS v1・Research Shadow）

- 日期：2026-09-30
- 範圍：`/app/level1` 三分頁（今日榜單／模型體檢／系統狀態）的前端版面與支撐它的 `/api/mlentry` 端點
- 前置：`2026-09-30-ml-entry-recommendation-frs-v1.md`（FRS 與附錄 C Prospective Shadow Observation）
- Mockup：`.superpowers/brainstorm/2033-1790750245/content/`（card-layout／tracking／convergence／health-bar）

## 0. 使用情境與設計目標

| 優先 | 情境 | 頁面要回答的問題 |
|---|---|---|
| 主（A） | 每天盤後自己看，30 秒判讀 | 今天有沒有出單？系統健不健康？推了誰、憑什麼？最近推的走得如何？ |
| 輔（B） | 前瞻觀察期（20／60 成熟日） | Live 是否收斂到 Frozen OOF？NO_TRADE／drift 是否合理？ |

使用者動作：A1 純觀察判讀品質、A2 紙上／模擬帳戶跟單（需要目標價、停損價與追蹤）。

### 不可違反的約束（附錄 C）

- 不改任何模型、feature、calibration、gate 門檻、ranking 權重、K、promotion 門檻、holdout。
- 本改版只新增**讀取與呈現**；唯一寫入端改動是每日 run 的監控明細多存「漂移特徵 PSI」（不影響預測、不影響 gate 判定）。
- 前端不自帶任何判定門檻或解讀語意；判讀句、收斂判定、NO_TRADE 文案一律由後端提供。
- 「Research Shadow」定位不得從畫面移除（縮成徽章，保留 tooltip）。
- 台股色：漲／Target 紅系、跌／Stop 綠系。

### 不做

- 模型／policy 任何調整；行動版專屬排版（只保證 ≥360px 不破版、表格可橫捲）；跨軌徽章（波段軌／推薦卡交叉比對）；SHAP／上榜理由。

---

## 1. 頂部健康條（三分頁共用，取代 `MLEntryStatusBanner`）

版型（mockup health-bar B）：

```
┌────────────────────────────────────────────────────────────────────┐
│ [Research Shadow] 09-29 正常出單 5 檔                    前瞻 0/60 │
│ Universe 1721 → 通過 Gate 6 → Top-K 5 ｜ 候選數在 OOF 常態範圍內  ●●●● │
└────────────────────────────────────────────────────────────────────┘
```

- 第一行：徽章 + `signal_date` + **判讀句 headline**（大字）；右側前瞻進度 `matured_days / 60`。
- 第二行：**判讀說明 detail**（小字）＋ 四顆燈號（資料／特徵／預測／推薦分布；綠＝通過、紅＝未通過、灰＝未執行）。
- 三種狀態樣式：

| run.status | headline | 樣式 |
|---|---|---|
| `OK` | `正常出單 N 檔` | 中性框 |
| `NO_TRADE`（`POLICY_NO_CANDIDATE` 等市場面原因） | `今日不出單：市場無機會` | 中性框；detail 明講「系統健康、非故障」 |
| `SYSTEM_NO_TRADE` | `系統暫停出單（fail-closed）` | 紅框；detail 點名原因；右側「看原因 →」切到系統狀態頁 |

- 徽章 tooltip：`未通過 promotion contract，非正式進場推薦`。`model_status == PROMOTED` 時徽章改綠色 `Promoted`。
- 燈號 tooltip：該 gate 的人讀摘要（見 §4.2）；點擊切到系統狀態頁並捲到該 gate。
- 無 run：headline `尚無 run`，detail `每日 21:30 MLEntryDailyStep 執行後產生`。

### 後端

`RunInfo` 新增 `verdict: {headline: str, detail: str, tone: "ok"|"quiet"|"fail"}`，由 `routes_mlentry._verdict(run, health)` 產生：

- OK：detail = `Universe {u} → 通過 Gate {q} → Top-K {r}`，若 `health.recommendation` 顯示 qualified_count 超出近 60 日常態，附加「候選數偏離常態」，否則「候選數在常態範圍內」。
- 市場面 NO_TRADE：沿用 `NO_TRADE_TEXT`。
- SYSTEM_NO_TRADE：依 reason 組句；`FEATURE_DRIFT` 時列出前 3 個漂移特徵（有 PSI 時附數值與門檻）。

`/mlentry/status` 回應新增 `live_progress: {matured_days, observe_at: 20, decide_at: 60}`（matured_days 來源同 `/health` 的 `live.matured_days`）。

---

## 2. 今日榜單頁

由上而下：今日推薦表 → 通過 Gate 未入 Top-K（折疊）→ 追蹤中。

### 2.1 今日推薦（mockup card-layout B：緊湊表格＋展開列）

| 欄 | 內容 | 備註 |
|---|---|---|
| # | rank | |
| 股票 | `stock_id name`，連結 `/stocks/:id` | |
| Target 10D | `p_target_10d`（紅）＋ `ratio×`（對 `market_target_rate`） | |
| Stop 10D | `p_stop_10d`（綠）＋ `ratio×`（對 `market_stop_rate`） | |
| MFE | `pred_mfe_10d` | |
| vn T/S | `p_target_vn / p_stop_vn`（兩位小數，去前導 0） | tooltip 附 gate 門檻（取自 policy config，後端提供） |
| 目標／停損* | 收盤估算價 | 見下 |

- 點列展開一行：`時序 T 3D→5D→10D ｜ S 3D→5D→10D ｜ ATR% ｜ Score ｜ P exec ｜ 個股頁 →`。
- 表下註：`* 以收盤估算；實際 barrier 從明日開盤 ×1.10／×0.95 起算`。
- **估算價由後端計算**（`BoardItem.est_target_price / est_stop_price`）：`close×1.10` 向下取整至台股升降單位、`close×0.95` 向上取整（兩者皆取保守側）。升降單位：<10 → 0.01；10–50 → 0.05；50–100 → 0.1；100–500 → 0.5；500–1000 → 1；≥1000 → 5。實作為 `app/mlentry/serving/ticks.py`（純函式、單元測試）。
- NO_TRADE／SYSTEM_NO_TRADE 當日：表格不渲染，健康條已說明；下方追蹤中照常顯示。
- 表頭摘要行保留：`基率 Target 14.8%・Stop 34.4%（Frozen 期間）`。

`BoardAPI` 另新增 `gate_thresholds: {target_vn_min, stop_vn_max}`（讀 policy config，只讀）。

### 2.2 通過 Gate 但未入 Top-K

維持 `<details>` 折疊；欄位與 2.1 相同（不含展開列與估算價）。

### 2.3 追蹤中（mockup tracking B：數字＋區間量尺）

範圍：最近 10 個交易日（含今日）所有 `recommended = true` 的 prediction（每個 signal_date 取當日最後一個 run），新→舊排序。已結案者保留到 10 日窗滿才移出。

| 欄 | 內容 |
|---|---|
| 推薦日 | signal_date（MM-DD） |
| 股票 | 連結 |
| 天數 | `d / 10`；d = entry_date 起已走完的交易日數（entry_date 當天算第 1 天） |
| 目前報酬 | `last_close / entry_open − 1`（紅／綠） |
| 量尺 | −5%～0～+10%：白棒＝目前報酬；灰帶＝期間 [MAE, MFE]（以 High/Low 計）；超出範圍截在邊界 |
| 狀態 | 籤：`待進場`／`進行中`／`TARGET Dn`／`STOP Dn`／`TIMEOUT`／`STOP_AMBIGUOUS`／`無法進場` |

- 表上摘要：`近 10 日 N 檔 · TARGET a · STOP b · 進行中 c`。
- 量尺 tooltip：`MFE +x.x% ／ MAE −y.y% ／ 距目標 +z.zpp ／ 距停損 w.wpp`。
- 空狀態：`近 10 個交易日沒有推薦`。

**狀態判定（後端，與 FRS barrier 同語意）**：

- `entry_date` 尚無日線 → `待進場`。
- 已成熟（ledger `event_type` 非空）→ 直接用 ledger 結果（TARGET／STOP／TIMEOUT／STOP_AMBIGUOUS）與 first-hit day。
- 未成熟 → 以 `benchmark_entry_price`（缺時用 entry_date 開盤）逐日掃 High/Low：同日同時觸及 +10% 與 −5% → `STOP_AMBIGUOUS`；先觸 +10% → `TARGET Dn`；先觸 −5% → `STOP Dn`；否則 `進行中`。**必須共用** labels 層的 barrier／first-hit 函式，不得另寫一套。
- `entry_status` 非可成交 → `無法進場`，量尺不畫。

**新端點** `GET /mlentry/tracking?days=10` → `{as_of, summary:{n, target, stop, timeout, live, pending}, items:[{signal_date, stock_id, name, day_index, horizon:10, ret_now, mfe, mae, status, hit_day}]}`。純讀取，不寫 DB。

---

## 3. 模型體檢頁

由上而下：前瞻進度條 → 收斂對照表 → 逐日成熟紀錄。（「近 60 日 run 歷史」移至系統狀態頁。）

### 3.1 前瞻進度條

`matured_days / 60`，刻度於 20（標「首個觀察點：只看不決策」）與 60（標「判斷點」）。

### 3.2 收斂對照表（mockup convergence A）

| 欄 | 說明 |
|---|---|
| 指標 | |
| Frozen OOF | 點估計 |
| Frozen CI | 區塊 bootstrap CI 或 p10–p90 分布區間（依指標） |
| Live 20D | `live.windows["20"]`；不足時顯示「累積中（n/20）」 |
| Live 60D | 同上 |
| 收斂 | 後端判定籤：`CI 內`／`CI 外`／`分布內`／`分布外`／`參考`／`累積中` |

列（順序固定）：

1. Lift@1、Lift@3、Lift@5（Lift@5 有 CI）
2. StopRatio@5（有 CI）
3. Net10／筆（扣 0.585%，有 CI）
4. Coverage
5. 候選數／日（Frozen 中位數 + p10–p90）
6. NO_TRADE 率
7. ECE Target 10D
8. Median MFE 10D／Median MAE 10D（Top-5）

收斂判定（後端 `performance.convergence(frozen, live)`）：有 CI 的指標比對 Live 值是否落在 CI；分布型指標比對 p10–p90；其餘標 `參考`。**20D 期間判定只顯示，不寫入任何狀態、不觸發任何動作**。表下固定註：`「收斂」為描述性比對；20 成熟日只看不決策，60 成熟日為判斷點（附錄 C）`。

### 3.3 Frozen 描述統計補算

現有 `frozen_validation` 缺：Lift@1／@3、候選數／日分布、NO_TRADE 率、ECE、Median MFE／MAE。

- 新增唯讀腳本 `scripts/mlentry_frozen_stats.py`：讀 `data/mlentry/<ds>/policy/policy_baseline_v1/{per_day,per_row}.parquet`，計算上述統計，寫入 `data/mlentry/<ds>/policy/policy_baseline_v1/frozen_stats.json`。
- `/mlentry/health` 讀此 JSON 合併進 `frozen_validation.metrics`；檔案不存在時對應列 Frozen 欄顯示 `—`，不報錯。
- **不寫回 champion.json**，不重跑 OOF、不重算 policy：只從既有 per_day／per_row 做描述統計。腳本需斷言讀到的 policy_name 與 champion 相同。

### 3.4 逐日成熟紀錄

表格：`signal_date ｜ 推薦數 ｜ TARGET ｜ STOP ｜ TIMEOUT ｜ 當日 Lift ｜ 當日 Net10`，新→舊，最多 60 列。來源為 `performance.load_matured` 的日級彙總（`/mlentry/health` 回傳 `live.days`）。空狀態：`尚無成熟日；第一個 10D 成熟日約在首個 run 後 11 個交易日`。

---

## 4. 系統狀態頁

由上而下：本次 run 敘事 → 四個 gate 卡片 → 近 60 日 run 歷史 → （折疊）Serving stack、監控門檻。

### 4.1 本次 run 敘事

健康條 verdict 的完整版：headline + detail，外加 FEATURE_DRIFT 時的漂移特徵表（`特徵 ｜ PSI ｜ 門檻`，最多 20 列）、DATA_HEALTH_FAIL 時的缺值欄位。

### 4.2 Gate 卡片人讀化

每個 gate 卡改為標籤化欄位（不再 `key=value` 串接）：

| Gate | 顯示 |
|---|---|
| 資料品質 | Universe 檔數 vs 參考中位數；核心缺值欄位（若有） |
| 特徵健康 | 漂移特徵數／門檻；PSI 最大值；超出範圍的日級特徵 |
| 預測健康 | 判定與 `why` |
| 推薦分布 | 通過 Gate 數 vs 近 60 日中位；近 60 日 NO_TRADE 率 |

欄位中文標籤表放在前端常數（只是標籤，非判定語意）。

### 4.3 近 60 日 run 歷史

自體檢頁搬來，新增欄 `漂移特徵數`（`health.feature_health.n_drifted`）。狀態文字色同健康條 tone。

### 4.4 折疊區

`<details>`：Serving stack 版本表（現行內容不變）、監控門檻（現行 JSON）。

### 後端（監控明細）

- `monitoring/health.feature_health_gate` 的 detail 新增 `drifted_psi: {feature: {psi, thr}}`（僅漂移特徵，最多 20 個）。只影響寫入 `health_json` 的明細，不改判定邏輯。
- `routes_mlentry._run_info` 的 health key 白名單加入 `drifted_psi`、`missing_shift`。
- 舊 run（無 `drifted_psi`）：畫面退回只列特徵名稱。

---

## 5. 元件與檔案

| 檔案 | 變更 |
|---|---|
| `frontend/src/components/MLEntryStatusBanner.tsx` | 改寫為健康條（verdict＋燈號＋進度） |
| `frontend/src/components/MLEntryBoard.tsx` | 卡片 → 緊湊表格＋展開列；估算價欄 |
| `frontend/src/components/MLEntryTracking.tsx` | 新增：追蹤中表格＋量尺 |
| `frontend/src/components/MLEntryHealth.tsx` | 改寫：進度條、收斂對照表、逐日成熟紀錄；移除 run 歷史 |
| `frontend/src/components/MLEntrySystem.tsx` | 改寫：敘事、人讀 gate、run 歷史、折疊區 |
| `frontend/src/pages/MLEntryPage.tsx` | 新增 tracking query；健康條燈號切頁 |
| `frontend/src/api/client.ts`／`types.ts` | 新型別與 `useMLEntryTracking` |
| `backend/app/api/routes_mlentry.py` | verdict、估算價、gate_thresholds、live_progress、`/tracking`、frozen_stats 合併、白名單 |
| `backend/app/mlentry/serving/ticks.py` | 新增：升降單位取整 |
| `backend/app/mlentry/serving/tracking.py` | 新增：追蹤路徑計算（共用 labels barrier） |
| `backend/app/mlentry/monitoring/performance.py` | `convergence()`、日級成熟彙總 |
| `backend/app/mlentry/monitoring/health.py` | `drifted_psi` 明細 |
| `backend/scripts/mlentry_frozen_stats.py` | 新增：唯讀補算 Frozen 描述統計 |

## 6. 錯誤與邊界

- 任何端點資料缺失一律回空結構＋前端空狀態文案，不回 500。
- `/tracking` 遇到股票日線缺漏：該列 `status = 資料缺漏`，不中斷其他列。
- 同一 signal_date 多個 run：一律取 `run_id` 最大者（與 `_latest_run` 一致）。
- 估算價：`close` 為空則顯示 `—`。

## 7. 測試

- `ticks.py`：各價位區間邊界（9.99、10、49.95、50、99.9、100、499.5、500、999、1000）向上／向下取整。
- `tracking.py`：用合成日線驗證 TARGET／STOP／STOP_AMBIGUOUS／TIMEOUT／待進場／無法進場；並與 labels 層 canonical outcome 對同一路徑結果一致（parity test）。
- `convergence()`：CI 內／外、分布內／外、樣本不足 → 累積中。
- `_verdict()`：三種 status × 有無 `drifted_psi`。
- `mlentry_frozen_stats.py`：policy_name 不符時拒絕執行；輸出欄位齊全。
- API：`/tracking`、`/board`（估算價、gate_thresholds）、`/status`（live_progress、verdict）、`/health`（frozen_stats 缺檔降級）回應結構測試。
- 前端：瀏覽器實測三分頁，含 NO_TRADE 與 SYSTEM_NO_TRADE 兩種 run（09-29 FEATURE_DRIFT run 為真實樣本）、360px 寬不破版。
