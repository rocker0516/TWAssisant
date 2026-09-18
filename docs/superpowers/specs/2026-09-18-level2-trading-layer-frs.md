# Level 2 交易層 FRS（v1.0）

日期：2026-09-18
狀態：**凍結（2026-09-18 使用者核可）→ 同日 M2 停損結案（見 §13）**
上游：Level 1 FRS v1.1（l1_lgbm_v2 已凍結、ledger 上線兩週、實盤 IC 0.12~0.13）
原始規格：使用者 docx「Level 1 股票推薦 ML 模型 FRS」附錄 B

## 0. Level 2 正式定義

Level 1 回答的是：「在真實可交易股票池中，模型是否真的有預測能力」（Rank IC / 分位單調）。
**Level 2 回答的是：這個 Ranking Edge 經過交易語意——進出場、部位、成本、風險——
翻譯之後，能否轉成帳戶層的、成本後的、可驗證的超額報酬。**

```
Level 1 Ledger → Entry/Exit → Position Sizing → Risk → Execution → NAV
（附錄 B pipeline；Score/Rank 不是買賣指令，交易語意全部在本層定義）
```

設計輸入（上線兩週實證，2026-09-18 評估）：
- edge 型態是**防禦型**——避開最弱分位的能力強於拉抬頂端。1D Top20 +10.00% vs
  全體 −1.26% 是弱市防禦超額，**不得假設頂端有暴衝 alpha**。
- 1D 訊號最強（IC +0.126）但換手極高；5D 主軌（IC +0.134, t=4.4）；10D 觀察中。

## 1. 已確認的範圍決策（2026-09-18 使用者拍板）

| 決策 | 定案 |
|---|---|
| 交易範圍 | **模擬帳戶先行**（paper trading）。Execution 介面預留、第一版不接券商 |
| 主 KPI | **對大盤累積超額報酬**，硬約束 **帳戶 MDD ≤ 大盤同期 MDD** |
| 再平衡節奏 | **5D 主組合＋1D 對照組合**，成本後用數據決定誰活下來 |
| 資金規模 | **100 萬 TWD**，支援零股 |
| 架構 | **兩階段**：Phase 1 環境＋規則基準 → Phase 2 RL 挑戰者（§8） |

Phase 1 的規則基準不是妥協：它是 RL 的訓練環境（模擬引擎）＋對照組。RL policy
必須在同一環境、同一 walk-forward 協定下打贏它，才有資格接管決策。

## 2. 上游介面（唯一）

- **Live**：`level1_predictions` 表（PK=日+股+horizon+版本；score/rank/pct_rank/
  universe_size；成熟回填 actual_*）。只讀 `CURRENT_MODEL_VERSION` 的列。
- **回測**：`walkforward` 產出的 OOS 分數（同 embargo 協定）。
  **禁止**以回填 ledger 的方式取得歷史榜單——回補日預測＝事後模型＝假戰績
  （Level 1 既有條款，本層繼承）。
- Level 2 **不讀任何特徵、不碰模型**。Level 1 換版時 Level 2 只認版本字串。

## 3. 帳戶與成本模型

- 現金帳戶；無融資、無放空、無當沖（第一版）。
- 手續費 0.1425%（買賣各收，**不假設折扣**——保守）、最低手續費 20 元／筆；
  證交稅 0.3%（賣出）。零股同費率。全部參數化（`CostModel`），改動＝版本變更。
- 滑價假設 0（universe 已限 ADV20 ≥ 5,000 萬、單檔部位 ~5 萬），參數化保留。
- **除權息已知偏差**：close 未還原（Level 1 既有限制，全市場股利資料不可得），
  除息缺口會計為虧損 → NAV 系統性低估。比較基準採**發行量加權股價指數（未含息，
  `market_index` 表）**，兩邊同向偏差、大致對稱。文件化，不修。

## 4. 成交模型

- 21:30 排程產生訊號 → **次一交易日開盤價成交**（MOO 假設）。
- 漲跌停：買單開盤即漲停 → 放棄（不追）；賣單開盤跌停鎖死 → 順延次日開盤重試，
  連續順延照常累計（不假裝賣掉）。
- 個股暫停交易／無成交日 → 委託順延，同上。

## 5. Phase 1 基準策略（Baseline **v1**——2026-09-18 dev 調參後修訂並凍結）

參數表（**凍結；任何改動＝policy_version 變更＋戰績歸零**）：

| 參數 | 值 | 說明 |
|---|---|---|
| 主組合 P5 | 每 **20** 個交易日再平衡 | 用 5D pct_rank |
| K_in / K_hold | 20 / **200** | 買進門檻 rank ≤ 20；既有持股 rank ≤ 200 續抱（寬緩衝降換手） |
| 權重 | 等權，目標 5%／檔 | 零股計算，現金殘留滾存 |
| 防禦出場 | 持股 1D pct_rank ≤ 0.2 → 次日出場 | 不等再平衡日；低頻節奏下唯一的盤中風控 |
| 對照組合 P1 | 每日再平衡 1D Top-20 等權 | dev 已判死刑（隔夜跳空拿不到＋成本 102pp），holdout 僅入檔 |

> **v0→v1 修訂紀錄（dev 調參，使用者核可 2026-09-18）**：v0（reb5/hold60）
> 年換手 26.8×、成本 37pp、防禦 604 次觸發成雜訊；dev 變體拆解顯示「頻率」
> 與「防禦」兩槓桿各自單調可解釋（10 變體，非窮舉 mining）。v1 dev：超額
> −5.2%（t=−0.29）、換手 7.9×、MDD −15.2% vs 大盤 −31.6%、vs universe 等權
> 淨 +16.5pp。診斷發現：universe 等權零成本亦落後加權指數 21.7pp（2024
> 大型股行情之基準結構差）——主 KPI 不變，報表增列 vs U_t 等權歸因欄。

引擎驗證對照（回測期跑，不入 live）：
- Baseline 0a：等權持有整個 U_t（市場對照，驗證成本與 NAV 會計）。
- Baseline 0b：隨機 Top-20（3 種子）——超額應 ≈ 0，驗證引擎無結構性 bug
  （對應 Level 1 的 Random baseline 紀律）。

## 6. KPI 與閘門

- **主 KPI**：成本後 NAV vs 大盤之累積超額報酬（同起點）。輔助：日超額 t 值、
  MDD、換手率、成本累計、勝率。
- **硬約束**：帳戶 MDD ≤ 大盤同期 MDD。
- 閘門（live paper）：
  1. ≥ 60 個交易日做首次正式評估（日超額 t 檢定）；期間**不調參**（No Target Mining）。
  2. 20 日滾動超額 < −5% 或 MDD 破約束 → 紅色警報，停機檢查。
  3. 與 Level 1 閘門聯動：Level 1 IC 紅色警報停機 → Level 2 進入**只出不進**模式。

## 7. 驗證協定（walk-forward 回測）

- 以 OOS 分數重建歷史每日榜單，在模擬引擎重放 2022-01 ~ 2026-08。
- **dev（2022-01~2024-12）**：允許調 Baseline 參數。**holdout（2025-01+）**：
  凍結後一次性跑，只讀不調（同 Level 1 紀律，切點同源）。
- **回測引擎與 live 引擎＝同一份程式碼**（OOS ≡ Production——Level 1 tradable
  universe 改版學到的鐵律，過濾/成本/成交規則不得有兩套實作）。
- 重放一致性：live 帳戶任一日的持倉/NAV 必須可由 orders 序列重放重建（測試釘死）。

## 8. Phase 2：RL 挑戰者（骨架；動工前另出細部設計 spec）

- **環境**：Phase 1 模擬引擎包成 gym 介面（1 step = 1 交易日）。這是 Phase 1
  必須把引擎寫成純函式核心（無 I/O）的原因。
- **State**：Level 1 三 horizon pct_rank 摘要、持倉狀態（含浮動損益、持有天數）、
  市場 regime 特徵（mkt_ret20 等）、現金比。
- **Action space v1（刻意縮小）**：離散——目標曝險檔數 K ∈ {0, 10, 20} × 是否再平衡。
  **RL 不碰選股**：選哪些股仍由 Level 1 排名決定，RL 只學「何時防禦、曝險多少」。
  理由：(a) 防禦時機正是 RL 相對固定規則有增益空間的決策；(b) policy 空間縮到
  單一市場路徑的樣本量撐得起；(c) 保留 Level 1 edge 的可解釋性。
- **Reward**：日超額報酬 − λ×回撤懲罰（λ 於細部設計定版）。
  **No Reward Mining 條款：reward 函數凍結後不得因成績不佳修改；改＝版本號＋戰績歸零。**
- **訓練/驗證**：訓練只用 dev 段 OOS 分數；holdout 段一次性驗證；上線門檻＝
  holdout 與 live paper **皆**成本後勝過 Baseline v0。
- **失敗預期（先寫下來）**：台股只有一條歷史路徑，RL 最可能的死法是把路徑背下來
  （[[ten-day-70pct-regime-bound]] 的 policy 版）。若 holdout 無法穩定勝過基準，
  正式結論是「規則基準已足夠」，**不是**繼續調到贏為止。

## 9. 資料模型

全部 append-only，帳戶狀態可由 orders 重放推導（snapshot 僅為查詢方便）：

- `level2_accounts`（id, name, policy_version, start_date, initial_cash）
- `level2_orders`（account, trade_date, stock_id, side, qty, price, fee, tax,
  status[filled/rejected/deferred], reason[rebalance/defense/limit_up…]）
- `level2_positions`（account, date, stock_id, qty, close, market_value）每日快照
- `level2_nav`（account, date, nav, cash, invested, benchmark_close）

## 10. 排程整合

- 21:30 pipeline 於 `Level1PredictStep` 後接 `Level2PaperStep`（required=False，
  子行程慣例同 Level1：**step 先 commit 放掉 SQLite 寫鎖再啟子行程**）。
- 當晚產生次日委託 → 次日盤後（同一 step 的下一次執行）以當日開盤價回填成交、
  更新 positions/NAV。關機漏日由啟動 catch-up 補（成交價仍用該日真實開盤價，
  委託仍源自前一日 ledger——不產生事後訊號）。
- **backfill 管線不含此 step**（假戰績條款同 Level 1）。

## 11. 展示層

`/app/level2`：NAV 曲線 vs 大盤（同起點指數化）、目前持倉、今日委託與成交、
KPI 卡（累積超額、MDD vs 大盤 MDD、換手率、成本累計）。數字一律後端 artifact
動態帶出（禁手寫；同 level1 頁紀律），掛 provenance chip（policy_version）。

## 12. 里程碑與停損點

| 里程碑 | 內容 | 停損點 |
|---|---|---|
| M1 | 模擬引擎（純函式核心）＋成本/成交模型＋重放一致性測試 | — |
| M2 | 回測：dev 調參 → 參數凍結 → holdout 一次性 | **holdout 成本後超額 ≤ 0 → 停，檢討交易語意（節奏/成本），不動 Level 1 模型** |
| M3 | live paper 上線（排程）＋ `/app/level2` 展示層 | 閘門 §6 |
| M4 | RL 細部設計 spec → 離線訓練 → holdout → live 對照 | holdout 輸基準 → 結案「基準已足夠」 |

## 13. 結案紀錄（2026-09-18，M2 停損觸發，使用者裁決）

holdout（2025-01~2026-09，一次性）：P5 baseline_v1 **vs 加權指數 −50.9pp
→ §12 M2 停損觸發**。檢討結論：交易語意已非瓶頸（換手 5.9×、成本 6.6pp）；
選股 alpha 為真且跨段穩定（vs U_t 等權 dev +16.5pp / holdout +15.5pp 淨，
MDD −15.7% 優於大盤 −26.7%，絕對 +51.8%）；失敗根因是**度量空間錯配**——
橫斷面等權 alpha 對上市值加權基準，在 2024-2026 超大型股行情下 U_t 等權
零成本亦落後指數 66pp，結構上不可追。

**使用者裁決：主 KPI 不修改（輸給大盤就是輸），Level 2 停在 M2。**
M3（live paper）／M4（RL）不啟動。後續方向屬 Level 1 端新研究題：
能否產出追得上市值加權基準的組合（如市值/流動性加權部位、或目標改
市值加權超額）——另立 spec 再議。

存活資產：`app/research/level2/` 模擬引擎（純函式、重放一致性測試釘死）
為未來任何交易層研究的現成環境；P1（1D 每日再平衡）永久關閉
（隔夜跳空＋成本，執行結構問題）。回測紀錄：`level2_backtest_dev.json`、
`level2_backtest_holdout.json`、dev 全變體 `level2_backtest_dev_sweep_20260918.json`。
