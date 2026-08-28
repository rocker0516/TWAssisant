# /app/level1 畫面設計（今日榜單／模型體檢 兩籤）

日期：2026-08-28
狀態：設計定案，待實作
前置：Tradable Universe Redefinition 已完成上線（`l1_lgbm_v2`，commit 363e36b..2e33cdc）。
榜單與驗證數字現在同源（同一個可交易 U_t），本設計以此為前提。

## 0. 已定案的決策（本設計不重開）

| 決策 | 內容 | 出處 |
|---|---|---|
| 頁籤結構 | 兩籤：今日榜單 / 模型體檢；體檢內部再分「凍結驗證」與「上線實績」兩區 | 使用者選定 |
| 上榜理由 | 不解釋、不接交叉訊號、不做 SHAP——純排序展示 | 使用者選定 |
| 體檢深度 | 完整：核心數字＋分位單調圖＋模型階梯對照 | 使用者選定 |
| 展示層過濾 | **作廢**。過濾已推入 U_t 定義，榜單天然可交易，畫面不再有第二層過濾 | Universe 改版 §13 |
| 主軌 | 5D（2026-08-28 使用者拍板，定義凍結）。1D 在新 U_t 實測最強，但主軌不因此改——體檢籤如實並列三個 horizon 即可 | 凍結決策 |

## 1. 設計原則

1. **誠實優先**。5D 主軌在可交易池的 holdout edge 是窄的（日勝率 53.7%、Top-20
   超額 +0.22pp/5日）。畫面不得用舊 U_t 的數字（+0.85pp/66.2%）或 dev 段數字冒充。
2. **同源原則**。榜單籤顯示的股票與體檢籤引用的驗證母體是同一個 U_t——這是整個
   Universe 改版買回來的性質，畫面 copy 要明示，成為信任賣點而非小字免責。
3. **分數不是指令**（FRS §20）。既有 intro copy 保留並更新 Universe 描述。

## 2. 資訊架構

```
/app/level1
├── Tab 1: 今日榜單（預設）
│   ├── 標頭列：日期・model_version・U_t 檔數・edge 一行摘要（取自 validation）
│   ├── 說明 copy（更新版）
│   ├── 控制列：horizon 1/5/10（預設 5D 主軌）｜Top 20/50/100
│   └── 排名表（欄位改版，見 §3）
└── Tab 2: 模型體檢
    ├── 區 A：凍結驗證（walk-forward OOS，靜態）
    │   ├── A1 核心數字卡（lgbm，dev/holdout 並排）
    │   ├── A2 分位單調圖（lgbm 十分位，dev/holdout 並排）
    │   └── A3 模型階梯表（random/動能/ridge_v2/lgbm）
    └── 區 B：上線實績（live Ledger，動態累積）
        ├── B1 摘要列（既有 /performance 資料）
        └── B2 逐日超額條列
```

頁籤以 React state 切換（不動路由），與 BacktestLab 等現有頁的分區慣例一致。
horizon／K 狀態兩籤共用（切籤不重置 horizon）。

## 3. Tab 1：今日榜單

### 3.1 標頭與 copy

標題列既有格式保留，`universe_size` 改標示為「可交易 Universe N 檔」。

說明 copy 更新為（替換現有兩行）：

> 模型每日收盤後，對**可交易 Universe**（20 日均成交值 ≥ 5,000 萬、非處置股）預測
> 「未來 N 日相對池內的強弱排名」。此處只做排序展示，分數不是買進指令；
> 進出場、部位與風控屬後續交易層。

標頭 edge 摘要（新增一行，資料取自 §5 validation endpoint，隨 horizon 切換）：

> holdout 驗證：日勝率 {win}%・Top-20 平均超額 {excess}pp／{N} 日（詳見模型體檢）

窄 edge 是特性不是缺陷——這行的存在就是誠實標示，數字難看也照放。

### 3.2 排名表欄位改版

| 欄 | 來源 | 說明 |
|---|---|---|
| `#` | rank | 不變 |
| 股票 | stock_id+name | 不變（連個股頁） |
| 收盤 | close | 不變 |
| **20日均成交值** | **board 新欄 `adv20`** | 億元、1 位小數。倉位規模脈絡；也讓使用者親眼看到池子確實可交易 |
| **模型分數** | score（已回傳，未顯示） | 4 位小數。**取代「預測分位」欄**——pct_rank 在 Top-20 全是 0.99x 無鑑別度，score 保留真實間距。tooltip：「模型原始分數，僅供同日同 horizon 內比較，跨日不可比」 |
| 實際報酬 | actual_return | 不變（未成熟顯示「未成熟」） |
| 實際分位 | actual_pct | 不變。tooltip 補「0.5＝無資訊」 |

刪除欄：預測分位（degenerate 顯示）。`pct_rank` 資料保留在 API 不動，只是不進表。

### 3.3 既有實績摘要列的去向

現在榜單頁上那條「已成熟 N 個預測日…」摘要列**移到體檢籤區 B**，榜單籤不重複。
榜單籤標頭的 edge 一行（§3.1）已承擔「這模型憑什麼」的最短回答。

## 4. Tab 2：模型體檢

### 4.1 區 A：凍結驗證（靜態，讀 §5 validation endpoint）

區標題：「凍結驗證 — Walk-forward OOS（2022-01 起，dev 只准調參、holdout 只讀）」。
副註明示母體：「驗證母體＝可交易 U_t（與左籤榜單同一池），預測母體約 599 檔／日」。

**A1 核心數字卡**：當前 horizon 的 lgbm，dev_oos 與 holdout 兩欄並排：

| 指標 | 顯示 | tooltip |
|---|---|---|
| Rank IC | mean_ic（4 位） | 每日 Spearman(score, 實際 N 日報酬) 的平均；0.05 以上即具實用排序力 |
| ICIR | icir | IC 均值／IC 波動——穩定度 |
| 分位單調性 | monotonicity | 十分位序與實際報酬的相關；1＝完美單調 |
| Top-20 超額 | topk.top20.excess_pct（pp） | Top-20 日均報酬 − 池內全體日均 |
| 日勝率 | topk.top20.day_win_rate（%） | Top-20 贏過池內均值的日子占比 |
| 評估天數 | n_days | — |

**A2 分位單調圖**：lgbm `quantile_mean_pct` 十分位長條，dev/holdout 並排兩張。
純 SVG／div bar（沿用 Sparkline 的無依賴慣例，不動 echarts）。負值向下。
**不美化**：5D holdout 實際形狀是「d1 相對最弱（+0.157%，全分位皆正——holdout 期有正漂移）、頂部微幅、d8 有凹陷」，
照畫。圖下一行 copy：「holdout 的 edge 主要來自避開最弱分位，頂部拉抬幅度有限」——
這句依 horizon 由前端以規則生成（mono < 0.5 時顯示，否則顯示「分數越高實際越強，
單調性 {mono}」）。

**A3 模型階梯表**：證明 §9 升級閘門是打贏出來的，不是宣稱的。

| 模型 | 說明 | dev IC | holdout IC | holdout Top20 超額 | holdout 勝率 |
|---|---|---|---|---|---|
| Random | 對照組（應 ≈ 0） | | | | |
| 動能 ret20 | 免訓練 baseline | | | | |
| Ridge v2 | 線性 + 基本面 + regime | | | | |
| **LGBM（上線）** | 非線性，現行版本 | | | | |

隨 horizon 切換。LGBM 列高亮。表下一行註：「Ridge 在 holdout 頂端反單調
（mono {ridge_mono}）——非線性升級的理由」。

### 4.2 區 B：上線實績（動態，既有 /performance API）

區標題：「上線實績 — Prediction Ledger（{model_version}，自 2026-08-27 起）」。

- **B1 摘要列**：沿用現有渲染（已成熟 N 日／平均超額／日勝率／平均實際百分位），
  從榜單籤原樣搬過來。
- **B2 逐日條列**：`days[]` 每日一列——日期、Top-K 超額（紅正綠負橫條，純 div，
  寬度∝|excess|，上限 ±3%）、實際百分位。最多顯示最近 60 日。
- **空狀態**（上線初期必經）：「v2 於 2026-08-27 上線。{N}D 預測需 {N} 個交易日
  成熟，首批實績約 {date} 回填；在此之前請看上方凍結驗證。」前端由 board.date
  推算顯示，不寫死日期。
- **區隔 copy**（B 區頂）：「凍結驗證是歷史模擬；這裡是上線後逐日寫入、成熟回填、
  不可重寫的實際紀錄（§15）。兩者數字收斂是模型健康的訊號。」

## 5. API 變更

### 5.1 新端點 `GET /api/level1/validation`

讀 `backend/data/level1_results.json` 的投影（檔案由研究 pipeline 產出、隨 repo
版控；mtime 快取，變更即重讀）：

```json
{
  "first_test": "2022-01-01",
  "periods": {"dev_oos": [...], "holdout": [...]},
  "model_version": "l1_lgbm_v2",
  "horizons": {
    "1": {
      "ladder": {
        "random":    {"dev_oos": {核心欄}, "holdout": {核心欄}},
        "mom_ret20": {...}, "ridge_v2": {...}, "lgbm": {...}
      },
      "quantiles": {"dev_oos": [10 floats], "holdout": [10 floats]}
    },
    "5": {...}, "10": {...}
  }
}
```

核心欄＝`mean_ic, icir, monotonicity, n_days, top20_excess_pct, top20_day_win_rate`
（自 `topk.top20` 攤平）。只投影 1/5/10 三個 horizon 與四個階梯模型；20D/60D 與
ridge_v1/mom_ret20_ex5 不出（研究內部用）。`model_version` 由 routes 的
`CURRENT_MODEL_VERSION` 帶出，前端據此顯示。

404 語意：results.json 不存在時回 404（同 ctx-matrix 慣例，測試比照
`test_ctx_matrix_endpoint_404_when_artifact_missing`）。

### 5.2 `GET /api/level1/board` 加 `adv20` 欄

`Level1Item` 新增 `adv20: float | None`（20 日均成交值，元）。SQL 以**交易日窗**計
（與 `universe.adv20` 同語意，非日曆日）：

```sql
(SELECT AVG(t.turnover) FROM (
   SELECT d2.turnover FROM daily_prices d2
   WHERE d2.stock_id = p.stock_id AND d2.date <= p.prediction_date
   ORDER BY d2.date DESC LIMIT 20) t)
```

k ≤ 100，子查詢成本可接受。前端以億元格式化（`(adv20/1e8).toFixed(1)`）。

## 6. 前端結構

| 檔案 | 動作 | 責任 |
|---|---|---|
| `frontend/src/pages/Level1Page.tsx` | 改寫 | 頁籤切換、共用 horizon/K 狀態、標頭 |
| `frontend/src/components/Level1BoardTable.tsx` | 建立 | 排名表（§3.2 欄位） |
| `frontend/src/components/Level1HealthPanel.tsx` | 建立 | 體檢籤全部（A1–A3、B1–B2、QuantileBars 內部小元件） |
| `frontend/src/api/client.ts` | 修改 | `Level1Item.adv20`、`Level1Validation` 型別、`useLevel1Validation()` |

QuantileBars 與逐日超額條都是純 SVG/div，不引 echarts（小圖遵循 Sparkline 慣例）。
配色沿站規：紅正綠跌（台股慣例）、`tabular-nums` 數字欄。

## 7. 明確不做

- 展示層過濾（任何形式——Universe 已負責可交易性）
- 上榜理由、交叉訊號徽章、SHAP
- 個股排名歷史軌跡（屬個股頁未來擴充，不在此頁）
- 20D/60D horizon 曝光（研究已判定不採）
- 公開站曝光（/app 登入牆內）
- echarts 新圖（兩張小圖不值引擎成本）

## 8. 驗收

1. 兩籤切換不丟 horizon/K 狀態；預設 5D／Top 20／今日榜單籤。
2. 榜單表顯示 adv20 與 score，無「預測分位」欄；Top-20 的 score 有可見間距。
3. 標頭 edge 一行隨 horizon 變動且數字與 validation endpoint 一致。
4. 體檢籤 A2 的 5D holdout 分位圖如實呈現非單調形狀（d1 最低、頂部微幅）。
5. A3 階梯表 random ≈ 0、動能為負、lgbm 高亮，數字與 `level1_results.json` 一致。
6. B 區空狀態正確（5D 首批成熟前顯示說明而非空表）。
7. `/api/level1/validation` 在 results.json 缺檔時回 404；有檔時三 horizon 齊。
8. 後端測試全綠；前端 `tsc` + build 綠；瀏覽器實測零 console 錯誤。
