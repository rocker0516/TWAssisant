# Level 1 Tradable Universe Redefinition

日期：2026-08-28
狀態：設計定案（使用者核可，升格為 FRS v1.1 核心架構），待實作
影響：FRS §3（Universe）、§5–7（Target）、§14（Daily Pipeline）、§15（Ledger）

本改版的本質不是「加一個流動性 filter」，而是：

```
Research Universe  →  Product Universe
```

即研究問題與產品問題正式對齊。

## 0. Level 1 正式定義（FRS v1.1）

> **Level 1**：在 T 日可取得的資訊下，對「當日符合產品推薦資格的可交易股票 Universe」
> 進行 Cross-sectional Prediction，穩定辨識未來 N 日相對強勢股票，並以 OOS 驗證其
> Ranking Edge。

```
U_t^Tradable → X_t → ML → Score → Rank → Top-K
```

Target：

```
R(i,t,N) → Percentile( R | U_t^Tradable )
```

Validation：

```
OOS Prediction ≡ Production Recommendation
```

### 0.1 Alpha 的定義域

**Level 1 的 Alpha 從一開始就定義在產品可交易 Universe 上。**

不採「先研究 Alpha，再加交易限制」的兩階段哲學。理由：§12.1 已量測到流動性門檻使
Top-K 超額由 +0.660pp 降至 +0.337pp——流動性不是工程細節，它實質改變 Alpha 的大小。
既然如此，就不能先在一個無法執行的母體上宣稱 Alpha、再事後打折；Alpha 的定義域必須
與產品的執行域相同。

### 0.2 與 Level 2 的邊界

| 層 | 負責回答 |
|---|---|
| Level 1 | 在真實可交易股票池中，模型是否真的有預測能力 |
| Level 2 | 該能力在交易成本、資金、風控、成交限制下能不能變成錢 |

```
Level 2: Recommendation → Entry → Position → Exit → Execution → Net P&L
```

Level 1 不再追求「理論上最有 Alpha 的股票池」。

## 1. 起因

規劃 `/app/level1` 畫面時，實測當日（2026-08-27, horizon=5）Top-30 的流動性，發現
榜單由近乎不可交易的股票主導：

| 排名 | 股票 | 20 日均成交值 | 日成交張數 |
|---|---|---|---|
| 1 | 8444 綠河-KY | 10 萬元 | 13 張 |
| 3 | 2424 隴華 | 30 萬元 | 34 張 |
| 17 | 2937 集雅社 | 50 萬元 | 10 張 |
| 14 | 6187 萬潤 | 68 億 | 4,797 張 |
| 對照 | 2330 台積電 | 465 億 | 19,214 張 |

Top-30 中 28 檔日均成交值低於 5,000 萬。`daily_prices.turnover` 本身健康
（2026-08-27 全市場 2,356/2,387 檔有值、均值 5.18 億），故非資料缺漏。

現有 holdout 驗證（5D：IC 0.0913、ICIR 0.633、Top20 超額 +0.85pp、日勝率 66.2%、
n=394）建立在同一個沒有流動性底線的 U_t 上，描述的是一個無法建立的組合。

## 2. 一致性稽核結果（OOS 路徑 A vs Production 路徑 B）

以逐項靜態比對加定點量測進行，非雙跑。

| 環節 | A：`level1_run` + `walkforward` | B：`level1_predict` | 判定 |
|---|---|---|---|
| Universe 規則 | `uv.eligible_ids` + `uv.universe_mask` | 同一組函式 | 相同 |
| close 來源 | `data/level1_targets.pkl` 研究快取 | 直接讀 `twa.db` | 快照漂移風險 |
| 價量特徵 ×11 | `build_price_features` | 同 | 相同 |
| PIT 基本面 ×5 | `build_fundamental_features` | 同 | 相同 |
| 橫斷面 rank | `rank_transform(feats, mask)` | 同 | 相同 |
| regime 交互 ×4 | `build_regime_interactions` | 同 | 相同 |
| Target | `cross_sectional_pct(forward_returns(close,N), mask)` | 同一行 | 相同 |
| 模型 | `LGBMRegressor(100, rs=42, n_jobs=-1)` | `LGBMRegressor(100, rs=42)` | `n_jobs` 未釘 |
| 訓練窗 | `train_slice`：`index(t) + N < test_start` | `close.index` 全歷史 | **不同** |
| 推論列母體 | `assemble_dataset`：target 非 NaN | `assemble_for_dates`：U_t 內全部 | **不同** |

### 2.1 訓練窗（最嚴重）

`level1_predict.predict()` 以 `ft.assemble_dataset(ranked, pct, close.index)` 建訓練集，
`close.index` 為 DB 全歷史。訓練集實效終點是「DB 最新日 − N」而非「pred_date − N」。

- 跑最新交易日：等價於正確 embargo（最近 N 日 target 為 NaN 被 dropna）。
- 跑歷史日期：訓練集吃進 pred_date 之後的所有資料。

`Level1PredictStep` 永遠不帶 `--date`，故現有 ledger（2026-08-26、08-27）乾淨。
但任何歷史回填都會產生偷看未來的假戰績，違反 §15 可驗證性。

A 端 embargo 是被測試釘死的規則；B 端 embargo 是「最近 N 天 label 為 NaN 所以被
dropna」的巧合。巧合在參數改變時失效。

### 2.2 推論列母體

A 的評估列由「target 非 NaN」定義（T 日在且 T+N 日在），B 的預測列由「T 日在 U_t」
定義。實測差距（horizon=5）：

```
2022-06-01  |U_t|=1734  A 評估母體=1719  差 15
2023-06-01  |U_t|=1770  A 評估母體=1760  差 10
2024-06-03  |U_t|=1816  A 評估母體=1814  差  2
2025-06-02  |U_t|=1791  A 評估母體=1770  差 21
2026-06-01  |U_t|=1814  A 評估母體=1801  差 13
```

差的是「T 日在、T+N 日下市或長停」者，0.1–1.2%。此差為本質差異（評估需要未來報酬、
預測不需要），不可消滅，但必須分別記錄、不再混用。

### 2.3 快照漂移

A 讀 `level1_targets.pkl`（含 close 至 2026-08-26），B 讀即時 DB（至 2026-08-27）。
目前僅差一日，無害。一旦有基本面回補，兩端會在同一天同一支股票看到不同特徵值，
且無任何警告。

## 3. FRS §3 條文修訂

原條文：「Universe 只管能不能被選，不藏 Alpha 條件」。

修訂為：

> Universe 不得包含以未來績效為目的的選股條件；允許使用與產品交易資格、資料可用性
> 及市場制度相關的 Eligibility Constraints。

判準是**排除的理由**，不是排除的後果。允許與禁止的分類：

| 條件 | 類型 | 允許 |
|---|---|---|
| 上市／上櫃普通股 | Structural | 是 |
| T 日已存在（有收盤價） | Structural | 是 |
| 非 ETF／ETN／DR／特別股 | Structural | 是 |
| T 日非停牌 | Trading | 是 |
| T 日非處置 | Trading | 是 |
| 最低流動性水準 | Trading | 是 |
| 20 日漲幅 > 20% | Alpha | 否 |
| RSI > 70 | Alpha | 否 |
| 成交量暴增 | Alpha | 否 |
| 過去報酬排名 Top 20% | Alpha | 否 |

註：「處置狀態」本身可能帶預測資訊。將其作為 Universe Rule（不允許模型推薦）與作為
Feature（允許進場並讓模型利用）是兩種不同設計，皆合理但回答不同問題。Level 1 的
產品定義是「產生今日可正常交易的推薦榜單」，故採前者。

## 4. U_t 新定義

```
U_t = { i | Structural(i) ∧ close(i,T) 存在 ∧ ADV20(i,T) ≥ 5e7 ∧ T ∉ Punish(i) }
```

- **Structural(i)**：不變。股號 4 位數字、`is_etf=0`、market ∈ {上市, 上櫃}、
  `industry_category` 不屬非普通股類別。
- **存在性**：不變。T 日 `daily_prices` 有收盤價（停牌當日無列，自然排除）。
- **ADV20**：`mean(turnover[i, T-19..T])`，`min_periods=10`，門檻 5,000 萬。純回看。
- **Punish**：T 落在任何 `attention_listings.kind='punish'` 的 `[begin_date, end_date]`
  區間內則排除。**只排除處置（punish），不排除注意（notice）**——注意股仍為正常競價
  撮合，且既有實證顯示其帶上漲動能，排除等同丟棄 alpha。

### 4.1 凍結性

5,000 萬為 FRS 凍結常數，選定後不得再調。以績效調整此門檻等同燒毀 holdout。

### 4.2 暖身

`rolling(20, min_periods=10)` 前 10 個交易日無值。研究期起點設為 **2020-02-01**。

### 4.3 資料來源 PIT 保證

`attention_listings` 覆蓋 punish 自 2019-12-19、notice 自 2020-01-02，3,137 筆 punish
全數具備 `begin_date`／`end_date`，涵蓋整個研究期。禁止以今日名單回填歷史可交易性
（與 §3 現有存活者偏差條款同一紀律）。

### 4.4 實測影響

`|U_t|` 全期中位數：**1766 → 564**。逐年統計（已含 ADV 門檻與 punish 排除，
起點 2020-02-01）：

| 年 | 中位數 | 最少 | 最多 |
|---|---|---|---|
| 2020 | 474 | 368 | 609 |
| 2021 | 585 | 493 | 810 |
| 2022 | 485 | 388 | 608 |
| 2023 | 577 | 423 | 669 |
| 2024 | 662 | 534 | 797 |
| 2025 | 555 | 511 | 631 |
| 2026 | 633 | 537 | 770 |

處置排除的邊際效果：每日中位數再剔除 8 檔（約 1.4%）。

`|U_t|` 隨行情擺動約 2.2 倍（最少 368 ↔ 最多 810）。百分位 target 每日獨立故
尺度無虞，但固定 K 的榜單語意會隨時間漂移（Top 20 在 368 檔中是前 5.4%、在 810 檔中
是前 2.5%）。展示層必須一律顯示當日 `|U_t|`。

訓練列數：每日約 1801 → 564，全期約 250 萬 → 90 萬列。對 LGBM 充足。

### 4.5 Data Eligibility 不列入 U_t

新 U_t 內基本面覆蓋率（分母＝U_t 為真的格數）：

| 特徵 | 新 U_t | 舊 U_t |
|---|---|---|
| `rev_yoy` | 98.4% | 97.1% |
| `rev_yoy_chg` | 97.4% | 95.9% |
| `rev_yoy3` | 97.5% | 95.9% |
| `eps_yoy_d` | 81.0% | 79.1% |
| `gm_chg` | 88.9% | 87.9% |

覆蓋率在新 U_t 中略優於舊 U_t。缺值續採 `assemble_dataset` 的 0.5 中性補值。將資料
完整性升級為硬條件會以「是否已出財報」砍掉約 19% 樣本，引入不必要的偏差。

附帶修正：`scripts/level1_run.py` 的基本面覆蓋率 log 以整個矩陣為分母
（`v.where(mask).notna().stack().mean()`），長期低報覆蓋率。改為以 mask 為真的格數
為分母。

## 5. Target 重定義

```
Y(i,t,N) = Percentile( R(i,t,N) | U_t^new )
```

在新 U_t（~563 檔）內重新排名，**不是**舊 1801 檔排名後取子集。

Prediction Universe 必須等於 Target Universe——兩者不一致是 §2 所述「OOS 數字與可交易
榜單對不上」的來源之一。

## 6. 訓練窗修正（P0 / Blocking）

`level1_predict.predict()` 改為與 OOS 共用同一個 embargo 規則：

```python
i = close.index.get_loc(pred_date)
train_dates = wf.train_slice(close.index, i, embargo=n)
x_tr, y_tr, _ = ft.assemble_dataset(ranked, pct, train_dates)
```

這不是「可能有 leakage」，是**確定性的 temporal leakage**：以 T=2023-06-01 產生預測時，
訓練集實際涵蓋 2020→2026。此項獨立於 Universe 改版，且必須最先做——重建 ledger 必然要
跑歷史日期，未修則產出假戰績。

**硬性要求**：OOS 與 Production 必須**呼叫同一個 `walkforward.train_slice()`**，
不得各自複製相同邏輯。

```
              walkforward.py
                    │
                    └── train_slice()
                           ↑
                  ┌────────┴────────┐
                  │                 │
            level1_run        level1_predict
```

複製邏輯正是本次三個裂縫的共同成因；共用函式是唯一能防止再度分叉的機制。

同時將兩端的 `LGBMRegressor` 統一為 `n_jobs=1`。LightGBM 在多執行緒下的直方圖累加順序
不保證固定，score 可能無法逐位元重現；§15 要求每次推薦可重現，故以單執行緒換取確定性。
訓練規模約 90 萬列，成本可接受。（既有 `level1_diag.py` 註記亦獨立觀察到「`n_jobs=-1`
疑似不穩」。）

**FRS 措辭要求**：`n_jobs=1` 是 **reproducibility control，不是 predictive-performance
control**。未來換模型或版本時不得將其誤認為方法論的一部分。

## 7. 母體記錄分離：`universe_size` 與 `evaluation_n`

Target 的排名母體是 **T 日的 U_t**，不是 T+N 的 U_{t+N}。某股票若在 T 日符合推薦資格，
它就是 U_t 的成員；即使 T+2 下市或 T+3 停牌，它仍屬 U_t，只是其 Future Return 為 NaN。

正式命名（兩者皆須輸出，不得混用）：

```
universe_size = |U_t|
evaluation_n  = |{ i ∈ U_t : R(i,t,N) 存在 }|
```

- ledger 的 `universe_size` 記前者（預測母體）
- 評估結果的 `n` 記後者（評估母體）

實作驗證：現行 `targets.cross_sectional_pct(fwd, in_universe)` 展開為
`fwd.where(in_universe).rank(axis=1, pct=True)`——遮罩取第 t 列的 U_t、排名只在非 NaN
者之間，已等於 `U_t ∩ ValidFutureReturn`。方向正確，本節只是將其正式命名。

實測（horizon=5，新 U_t）：

| 日期 | `universe_size` | `evaluation_n` | 差 |
|---|---|---|---|
| 2022-06-01 | 450 | 450 | 0 |
| 2023-06-01 | 599 | 599 | 0 |
| 2024-06-03 | 712 | 712 | 0 |
| 2025-06-02 | 547 | 547 | 0 |
| 2026-06-01 | 715 | 715 | 0 |

全期抽樣 32 日：差值中位數 0、最大 2（舊 U_t 為 2–21）。流動性底線順帶幾乎消滅了此
落差——日均成交值 5,000 萬以上的股票，5 日內下市或長停近乎不發生。此定義仍須明文寫出
（它是定義而非巧合，且在更長 horizon 下差值會擴大）。

## 8. 版本策略

| 常數 | 舊 | 新 | 理由 |
|---|---|---|---|
| `MODEL_VERSION` | `l1_lgbm_v1` | `l1_lgbm_v2` | ledger PK 含此欄，舊版天然並存 |
| `FEATURE_VERSION` | `v2_feat20` | `v2_feat20_u2` | 特徵公式未改，但 `rank_transform` 為橫斷面操作，母體由 1801 縮至 563 使同股同日特徵值改變 |

`data/level1_results.json` 重跑前先另存 `level1_results_u1.json` 留檔。

新舊 holdout 數字不可並排比較——它們量的是不同母體上的不同 target。唯一有意義的比較
是新 U_t 上的模型階梯（random / 動能 / ridge_v1 / ridge_v2 / lgbm）整座重跑。

### 8.1 API 相容性（版本並存的副作用）

`routes_level1.board()` 目前以

```python
d = select(func.max(P.prediction_date)).where(P.horizon == horizon)
rows = select(...).where(P.horizon == horizon, P.prediction_date == d).order_by(P.rank)
```

取資料，**未過濾 `model_version`**。v1 與 v2 在同一 `prediction_date` 並存時，同一支
股票會回傳兩列、`rank` 出現重複，Top-K 直接失真。`performance()` 的
`avg(actual_return)` 同樣會跨版本混算。

處理方式：於 `routes_level1` 新增模組常數 `CURRENT_MODEL_VERSION`，`board()` 與
`performance()` 皆加上 `P.model_version == CURRENT_MODEL_VERSION` 條件，`max(prediction_date)`
的子查詢一併加。版本切換時只改此常數。

此項必須與版本號變更同一批上線，否則並存策略會立即破壞現有畫面。

## 9. 快照漂移防護

`scripts/level1_targets.py` 產 pkl 時將 DB 的 `max(date)` 寫入 payload；
`scripts/level1_run.py` 啟動時比對 DB 現況，不一致則 fail-fast，不得靜默沿用舊快照。

## 10. 一致性回歸測試

新增 `backend/tests/test_level1_pipeline_parity.py`，對指定歷史日 T 斷言：

1. A 與 B 產出的 `U_t` 股票集合相同
2. A 與 B 建出的訓練集 index 相同
3. 相同 X 進相同模型得到相同 score

將一次性稽核轉為永久護欄。

## 11. 執行順序與停損點

1. 修訓練窗共用 `train_slice`，釘 `n_jobs`（含測試）
2. 改 `universe.py`：ADV20 + punish 排除
3. 修 `level1_run.py` 覆蓋率 log 分母
4. 重跑 `scripts.level1_targets` → 新 pkl（含 DB max(date)）
5. 重跑 `scripts.level1_run` → 新 `level1_results.json`
6. **停損點**：檢視新 U_t 上的模型階梯。若 lgbm 的 IC 崩至接近 0、或未能穩定勝過
   新 U_t 上的 baseline，停止並重新評估，不得直接上線。另需個別檢視 10D——
   §12.1 的先驗顯示其在可交易池的日勝率僅 52.6%，可能需自展示層降級
7. 改 `level1_predict.py`（版本號、訓練窗）
8. 新增 parity 測試
9. 重跑最新交易日，v1 ledger 保留並存
10. 回到 `/app/level1` 畫面設計

## 12. 預期與風險

### 12.1 已量測的先驗

以既有 `data/level1_scores_lgbm.pkl`（全 universe 訓練的 walk-forward OOS score）套上
本設計的可交易門檻重新排名並評估，full_oos（2022-01 起）結果：

| horizon | 切片 | IC | ICIR | Top20 超額 | 日勝率 | 天數 |
|---|---|---|---|---|---|---|
| 1D | 全 universe | 0.0932 | 0.83 | +1.423pp | 86.7% | 1125 |
| 1D | 可交易 U_t | 0.0794 | 0.70 | +0.613pp | 71.4% | 1125 |
| 5D | 全 universe | 0.0892 | 0.67 | +0.660pp | 63.2% | 1121 |
| 5D | 可交易 U_t | 0.0691 | 0.55 | +0.337pp | 56.9% | 1121 |
| 10D | 全 universe | 0.0870 | 0.66 | +0.863pp | 63.2% | 1116 |
| 10D | 可交易 U_t | 0.0673 | 0.55 | +0.365pp | 52.6% | 1116 |

讀法：

- **IC 掉 15–23%，Top-20 超額掉 49–58%。** 模型的平均排序能力大部分不來自不可交易
  區段，但 Top-K 的超額報酬高度集中在該區段。與 Ridge v1 時期的「Rank IC 正 ≠
  Top-K 可用」同構，兇手由 regime 換成流動性。
- **10D 在可交易池的日勝率降至 52.6%**，接近擲硬幣。改版後 10D 是否仍值得列入展示，
  待新 U_t 重訓結果再定。
- **5D 主軌保留 +0.337pp／5 日、勝率 56.9%**，edge 存活但約為原先一半。

**此量測的限制**：測的是「全 universe 訓練的模型」在可交易切片上的表現，非「可交易
池訓練的模型」。重訓後可能更佳（模型容量不再耗在殼股噪音）或更差（訓練訊號本就在被
移除的區段）。此為先驗，非結論——但已將第 11 節第 6 步的停損點由完全未知，收斂為
「預期不觸發，惟 10D 需個別檢視」。

### 12.2 風險

舊 U_t 中約 66% 為日均成交值低於 5,000 萬的股票，其價格由極少數委託驅動、橫斷面排名
相對易猜。§12.1 已量化此依賴程度。這與
`ten-day-70pct-regime-bound`（穩定的是超額、基率不可預測）及 `wave-crash-atr9`
（≥70% 存在但全由波動度買單）是同一模式的第三次出現。差別在於這次於上線前發現。

若第 6 步顯示 alpha 大部分來自不可交易區段，那是關於此模型的真實結論，不是失敗。

## 13. 明確不做

- 不在展示層做過濾（那會使 Production 變成 Model + Rule Filter，重新製造 §2 的不一致）
- 不將流動性門檻參數化或開放調整
- **不因新 U_t 的 OOS 績效變差而回調 ADV20 門檻。** 5,000 萬既已宣告為 FRS 凍結常數，
  就必須真正 freeze。否則會發生「5,000 萬 → IC 不夠漂亮 → 3,000 萬 → 更漂亮 → 1,000 萬」
  的滑坡，那等同以 Universe 做 Target Mining，直接摧毀 holdout 的效力
- 不刪除 v1 ledger 與 v1 results（留檔對照）
- 不新增 SHAP／特徵貢獻（畫面決策為純排序展示）
