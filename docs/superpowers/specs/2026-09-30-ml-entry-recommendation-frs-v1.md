# 台股 ML 進場推薦系統 FRS v1

- **文件類型**：Functional Requirements Specification (FRS)
- **系統優先級**：P0 = 股票標的推薦進場；P1 = 作為未來 RL 全自動交易之前置 Alpha / State Layer
- **市場範圍**：台灣上市 + 上櫃普通股
- **核心產品形態**：每日收盤後，從全市場可交易股票中產生隔日進場推薦，允許 `NO_TRADE`
- **設計原則**：ML 負責「找 Alpha / 推薦標的」，Execution / Portfolio / RL 負責「如何交易 Alpha」
- **版本狀態**：架構已定案；部分 threshold / 權重 /超參數保留由 Walk-forward Validation 決定

---

# 1. 系統目標

## 1.1 Primary Objective

系統每日於交易日 `t` 收盤後，使用截至當下可取得之資料，從當日 Tradable Universe `U_t` 中找出：

> **隔一交易日進場後，未來 3D / 5D / 10D 具有良好 risk/reward 的股票。**

系統需輸出：

- 候選股票
- 推薦排序
- 多時間尺度 Alpha 機率
- Stop Risk
- 預測 MFE
- Executability
- 是否通過 Candidate Gate
- 最終是否推薦
- `NO_TRADE` 狀態與原因

## 1.2 Secondary Objective

ML 系統輸出需可直接作為未來 RL State / prior，且 ML 與 RL 責任分離：

- ML：哪支股票值得優先交易
- RL：是否進場、持有、減碼、出場、資金配置

ML 不直接承擔完整交易策略最佳化。

---

# 2. 系統總體架構

```text
Raw Market Data
      │
      ▼
Point-in-time Data Layer
      │
      ▼
Tradable Universe U_t
      │
      ▼
Feature Pipeline
      │
      ▼
ML Model Stack
      │
      ▼
Calibration / Horizon Consistency
      │
      ▼
Prediction Vector
      │
      ▼
Candidate Gate
      │
      ▼
Ranking
      │
      ▼
Dynamic Top-K / NO_TRADE
      │
      ├── Benchmark Outcome Evaluation
      │
      └── Execution / Trading Layer (separate)
```

---

# 3. 時間定義與 Sample 單位

## 3.1 Sample Grain

每一筆訓練樣本定義為：

```text
(stock_id, signal_date)
```

即：

> 某一檔股票在某一交易日 `t` 收盤後，是否值得於下一交易日進場。

## 3.2 核心時間欄位

每筆 sample 至少需保存：

```text
sample_id
stock_id
signal_date
entry_date
feature_as_of
label_available_date
```

定義：

- `signal_date = t`
- `entry_date = t+1 trading day`
- `feature_as_of <= signal_date close / prediction timestamp`
- `label_available_date` = 最大 forward horizon outcome 真正成熟日期

## 3.3 Point-in-time Hard Rule

所有 Feature 必須符合：

```text
source.available_at <= as_of_timestamp
```

任何在 inference 當下尚未可取得之資料禁止進入 Feature。

---

# 4. Entry Benchmark 定義

ML Label 使用固定 Benchmark Entry：

```text
benchmark_entry_price = Open[t+1]
```

此價格只用於：

- Canonical Outcome
- Target / Stop
- MFE / MAE
- 模型間公平比較

不得因 execution simulator / slippage model 更新而改寫 ML Label。

---

# 5. Canonical Outcome Layer

Canonical Outcome 不直接壓成單一 0/1 Label，而是保存完整未來 path-derived outcome。

## 5.1 Barrier Definition

```text
Target Barrier = +10%
Stop Barrier   = -5%
Max Horizon    = 10 trading days
```

從 `benchmark_entry_price` 起算。

## 5.2 Event Ordering

核心事件：

```text
TARGET  = +10% 先於 -5% 發生
STOP    = -5% 先於 +10% 發生
TIMEOUT = 10D 內兩者皆未先發生
```

事件順序比是否曾碰到 barrier 更重要。

## 5.3 Ambiguous OHLC Rule

若僅有日 OHLC，且同一天同時觸及 +10% 與 -5%，無法知道先後順序：

```text
STOP_AMBIGUOUS
```

預設採保守原則：不視為成功 Target。

若未來有分鐘 / tick data，可用更細粒度資料還原事件順序。

## 5.4 Canonical Outcome Schema

至少保存：

```text
sample_id
benchmark_entry_price
entry_executable
entry_status

target_first_hit_day
stop_first_hit_day

event_type

mfe_3d
mfe_5d
mfe_10d

mae_3d
mae_5d
mae_10d

mae_before_target

return_1d
return_3d
return_5d
return_10d
```

## 5.5 Derived Labels

Canonical Outcome 再衍生：

```text
target_hit_3d
target_hit_5d
target_hit_10d

stop_hit_3d
stop_hit_5d
stop_hit_10d
```

例如：

```text
target_hit_5d = 1
iff target_first_hit_day <= 5
and target occurs before stop
```

---

# 6. Multi-Horizon 設計

正式採三個時間尺度：

| Horizon | 定位 | 意義 |
|---|---|---|
| 3D | Fast Alpha | 是否快速發動 |
| 5D | Primary Short-term | 一週內交易機會 |
| 10D | Full Opportunity | 原始 +10% 核心 Opportunity |

Barrier 暫時統一：

```text
+10% target
-5% stop
```

不採 3D +5%、5D +7%、10D +10% 這類不同 barrier，以避免 Target 語意混亂。

---

# 7. Universe

## 7.1 Coverage Universe

模型搜尋空間：

```text
TWSE common stocks
+
TPEx common stocks
```

不因下列條件預先排除：

- 注意股
- 高波動
- 高成交量
- 特定產業
- 特定技術型態

以上資訊原則上應 Feature 化。

## 7.2 Tradable Universe U_t

```text
U_t = Coverage_t ∩ Eligibility_t
```

Eligibility 只處理「是否合理可交易」，不處理「是否可能上漲」。

V1 硬性排除：

- 非普通股
- 停止 / 暫停交易
- 核心資料缺失或異常
- 歷史資料不足
- 極低流動性（threshold 待 validation / execution capacity 決定）

## 7.3 新上市股票

V1 採保守方案：

```text
listing_age >= max_feature_lookback
```

若最大 lookback 為 120 trading days，則至少滿足 120 個交易日歷史。

未來可另建 IPO / Newly Listed regime。

## 7.4 不做 Hard Alpha Filter 的欄位

以下不作為 Alpha 硬篩選：

```text
is_attention_stock
is_disposition_stock
industry
market_cap
volatility
turnover
limit_up_history
limit_down_history
```

原則上作為 Feature 或 Execution metadata。

---

# 8. Feature Architecture

V1 目標 Feature 數量：約 40–80 個核心 Feature。

## 8.1 Price / Return

建議至少：

```text
ret_1d
ret_3d
ret_5d
ret_10d
ret_20d

gap_open
close_location

distance_from_20d_high
distance_from_20d_low
```

## 8.2 Volume / Liquidity

```text
turnover_1d
turnover_5d_mean
turnover_20d_mean
volume_ratio_5d
volume_ratio_20d
turnover_change
turnover_rate
turnover_rank
```

## 8.3 Volatility / Path

```text
realized_vol_5d
realized_vol_10d
realized_vol_20d
atr_pct
intraday_range
downside_vol
positive_day_ratio
negative_day_ratio
max_drawdown_5d
max_drawdown_10d
max_drawdown_20d
```

## 8.4 Cross-sectional Relative Features

每日對當日全市場計算 percentile / rank：

```text
ret_1d_pct_rank
ret_5d_pct_rank
ret_20d_pct_rank
turnover_pct_rank
volatility_pct_rank
volume_ratio_pct_rank
```

重要數值特徵優先提供三種表示：

```text
raw
market-relative
cross-sectional rank
```

## 8.5 Market / Industry Regime

```text
market_ret_1d
market_ret_5d
market_ret_20d
market_volatility

industry_ret_5d
industry_ret_20d
industry_strength_rank

stock_excess_return_vs_market
stock_excess_return_vs_industry
```

## 8.6 Event / Microstructure

V1 可納入：

```text
is_attention_stock
is_disposition_stock
limit_up_today
limit_down_today
limit_up_count_20d
large_gap
consecutive_up_days
consecutive_down_days
```

若法人 / 融資融券資料具有可靠 point-in-time timestamp，可再加入。

## 8.7 V1 暫不優先

- 未確認發布時間的基本面資料
- 無 point-in-time 保證之財報資料
- 新聞 NLP
- 社群情緒
- 高頻逐筆特徵
- 大量 TA 指標 permutation
- 人工「妖股分數」
- 任一使用未來修正值的資料

---

# 9. Dataset Construction

## 9.1 每日全市場產生 Sample

對每個交易日 `t`：

```text
D_t = {(i, t) | i ∈ U_t}
```

同一支股票可連續多日出現，視為正常 production distribution。

## 9.2 Overlapping Labels

連續日期的 10D outcome 高度重疊，但不刪除。

禁止為了避免 overlap 而每 10 天抽一次樣本。

Overlap 問題應透過 Purged Walk-forward 解決。

## 9.3 不可用未來 Executability 篩除 Sample

若 `t+1` 實際不可成交，不可在 dataset build 時直接刪除 row，因為這是 `t` 時點未知資訊。

應保存：

```text
entry_executable
entry_status
```

作為 Outcome / Execution target。

## 9.4 日期權重

建議每個交易日 loss 總權重近似相同：

```text
w_i,t = 1 / |U_t|
```

避免市場股票數量隨時間增加造成後期日期天然權重較高。

若需處理 class imbalance，可再疊加，但不可破壞 day-normalization 原則。

---

# 10. Dataset Schema

## 10.1 Sample Index

```text
sample_id
stock_id
signal_date
entry_date

universe_version
feature_version
label_version

eligible_at_signal
data_quality_flag
```

## 10.2 Feature Snapshot

```text
sample_id
<all feature columns>
```

所有值必須 point-in-time valid。

## 10.3 Canonical Outcome

```text
sample_id
benchmark_entry_price
entry_executable
entry_status

target_first_hit_day
stop_first_hit_day

event_type

mfe_3d
mfe_5d
mfe_10d

mae_3d
mae_5d
mae_10d

return_1d
return_3d
return_5d
return_10d
```

---

# 11. Versioning / Reproducibility

每次 Experiment 必須唯一追溯：

```text
Experiment = (
  DatasetVersion,
  FeatureVersion,
  LabelVersion,
  UniverseVersion,
  SplitVersion,
  ModelConfig
)
```

Production 額外保存：

```text
model_version
calibration_version
policy_version
code_commit
```

---

# 12. V1 Model Architecture

V1 不採複雜 Multi-task Neural Network。

採：

> **多個獨立 LightGBM 模型 + 共享 Feature / Dataset / Validation Pipeline**

## 12.1 Execution Model

```text
P(entry_executable = 1 | X_t)
```

輸出：

```text
p_executable
```

## 12.2 Target Models

三個 Binary Classification：

```text
p_target_3d
p_target_5d
p_target_10d
```

Label：

```text
target_hit_H = 1
iff +10% occurs within H days
and before -5%
```

## 12.3 Stop Models

三個 Binary Classification：

```text
p_stop_3d
p_stop_5d
p_stop_10d
```

Label：

```text
stop_hit_H = 1
iff -5% occurs within H days
and before +10%
```

`P(Target) + P(Stop) != 1`，因為存在 TIMEOUT。

## 12.4 MFE Models

三個 Regression：

```text
pred_mfe_3d
pred_mfe_5d
pred_mfe_10d
```

V1 優先採：

- LightGBM regression
- Huber / L1 類 robust loss
- 或依 empirical distribution 對 extreme MFE 做 winsorization

MFE cap 不預先硬寫死，需由 Development data 決定。

## 12.5 模型數量

```text
Execution  1
Target     3
Stop       3
MFE        3
----------------
Total     10 models
```

---

# 13. Baseline Models

每個 Target 至少比較：

1. Historical prevalence baseline
2. Logistic Regression
3. LightGBM challenger / main model

不得只比較 LightGBM 內部版本。

---

# 14. Calibration

Classifier raw probability 不可直接視為真實機率。

必須做 Calibration：

- Platt Scaling 或
- Isotonic Regression

Calibration 只能用 out-of-fold / validation prediction fit。

禁止：

```text
train predict -> fit calibration on same train prediction
```

驗證指標至少包含：

```text
Brier Score
Expected Calibration Error (ECE)
Reliability Curve
```

---

# 15. Multi-Horizon Consistency

獨立模型可能產生非法結果：

```text
P3D > P5D
```

需在 Calibration 後做 horizon consistency projection：

```text
P3D <= P5D <= P10D
```

V1 可採 isotonic / monotonic projection。

---

# 16. Prediction Vector

每日每檔股票至少輸出：

```text
stock_id
signal_date

p_executable

p_target_3d
p_target_5d
p_target_10d

p_stop_3d
p_stop_5d
p_stop_10d

pred_mfe_3d
pred_mfe_5d
pred_mfe_10d
```

此 Prediction Vector 為 Recommendation Engine 與未來 RL 的核心 ML input。

---

# 17. Recommendation Policy

正式採：

```text
Universe
  -> Candidate Gate
  -> Ranking
  -> Dynamic Top-K
  -> Recommendation / NO_TRADE
```

## 17.1 Candidate Gate

至少包含：

```text
Eligibility Gate
Execution Gate
Risk Gate
Alpha Gate
```

概念：

```text
candidate =
  eligible
  and p_executable >= theta_exec
  and p_stop_10d <= theta_risk
  and p_target_10d >= theta_alpha
```

Threshold 不手工寫死，需由 OOF Walk-forward Validation 決定。

## 17.2 Horizon Role

- 10D：判斷是否存在核心 Opportunity
- 5D：中短期 timing quality
- 3D：Fast Alpha

3D / 5D 預設不作第一層硬 Gate，以免排除 delayed alpha。

## 17.3 Ranking Score

通過 Gate 後才計算 scalar Recommendation Score。

Concept：

```text
Score = Alpha + Speed + Upside - Risk
```

建議以每日 cross-sectional percentile / rank 後再組合，避免不同模型 output scale 不一致。

Conceptual inputs：

```text
Q(p_target_10d)
Q(p_target_5d)
Q(p_target_3d)
Q(pred_mfe_10d)
-Q(p_stop_10d)
-Q(p_stop_5d)
```

權重可設 baseline，但最終權重必須由 OOF Validation 決定並 freeze。

## 17.4 Dynamic Top-K

```text
K_t = min(K_max, qualified_count_t)
```

不強迫每天固定推薦固定檔數。

需至少 evaluate：

```text
Top1
Top3
Top5
Top10
Top20
```

## 17.5 NO_TRADE

`NO_TRADE` 為一級正式輸出，不是空陣列。

至少支援：

```text
MARKET_NO_OPPORTUNITY
POLICY_NO_CANDIDATE
EXECUTION_RISK
DATA_HEALTH_FAIL
FEATURE_DRIFT
MODEL_HEALTH_FAIL
CALIBRATION_FAIL
MANUAL_HALT
```

市場沒有機會與系統失效必須分開。

## 17.6 保存全 Universe Prediction

不得只保存 Top-K。

對所有當日 Universe 股票保存：

```text
raw prediction
calibrated prediction
gate_pass
gate_failure_reason
recommendation_score
rank
recommended
```

---

# 18. Evaluation FRS

## 18.1 Primary Champion Objective

主 KPI：

```text
TargetLift@K
```

其中：

```text
TargetLift@K = HitRate@K / MarketBaseRate
```

至少報：

```text
Lift@1
Lift@3
Lift@5
Lift@10
```

## 18.2 Guardrails

Champion selection 不可只最大化 Lift。

至少同時約束：

```text
StopRate@K
Coverage
Calibration Error
Median MFE
MAE
```

Concept：

```text
maximize TargetLift@K
subject to
  StopRate <= threshold
  Coverage >= threshold
  CalibrationError <= threshold
  MedianMFE >= threshold
```

實際 threshold 待 Development Validation 決定。

## 18.3 Risk Metrics

至少：

```text
StopRate@K
MeanMAE@K
MedianMAE@K
```

可額外使用：

```text
NetHitRate@K = TargetRate@K - lambda * StopRate@K
```

`lambda` 為 evaluation utility 參數，需 versioned。

## 18.4 Upside Metrics

```text
MeanMFE@K
MedianMFE@K
```

Median 必須保留，避免極少數 extreme winner 撐高平均。

## 18.5 Timing Metrics

```text
P(Target <= 3D | Recommended)
P(Target <= 5D | Recommended)
P(Target <= 10D | Recommended)
MedianTimeToTarget
```

## 18.6 Coverage Metrics

```text
Coverage = trading_days_with_recommendation / total_trading_days
AvgRecommendationsPerDay
candidate_count distribution
recommendation_count distribution
```

## 18.7 Ranking Diagnostics

作為 Diagnostic，而非唯一 Champion KPI：

```text
Precision@K
Recall@K
NDCG@K
IC
AUC
```

## 18.8 Day-weighted Evaluation

核心 metric 必須同時提供：

```text
row-weighted
day-weighted
```

產品層優先重視 day-weighted。

## 18.9 Regime Breakdown

至少拆：

```text
Bull / Neutral / Bear
High Vol / Low Vol
High Breadth / Low Breadth
Large / Mid / Small Cap
Industry
```

目的：辨識 Edge 存在於哪些 regime，而非要求所有 regime 同質。

---

# 19. Backtest / Walk-forward Protocol

## 19.1 Split 原則

禁止：

```text
random train_test_split
```

正式採：

```text
Purged Walk-forward Validation
```

## 19.2 Rolling Training Window

V1 baseline：

```text
Rolling 3Y training window
```

後續 challenger 可比較：

```text
2Y rolling
5Y rolling
expanding
```

## 19.3 Validation Block

```text
6 months per validation fold
```

## 19.4 Purge

Max horizon = 10 trading days。

原則：

```text
training sample is valid only if label_available_date <= training cutoff
```

因此 purge 以 `label_available_date` 為最終準則，而非只依固定曆日。

## 19.5 Final Holdout

```text
Latest 12 months
```

Final Holdout 前必須 Freeze：

- Feature Set
- Label Spec
- Universe
- Model Hyperparameters
- Calibration
- Gate Thresholds
- Ranking Formula
- Ranking Weights
- K_max
- NO_TRADE Policy

若看過 Holdout 後再調整，該區段立即失去 Final Holdout 資格。

## 19.6 Retraining / Inference Cadence

```text
Inference: Daily
Retrain: Monthly (~20 trading days)
```

## 19.7 Recommendation Policy Tuning

Recommendation Engine 只能使用 OOF predictions 調參。

禁止使用 in-sample predictions 調：

- Gate threshold
- Ranking weights
- K_max
- Policy rules

## 19.8 Model Selection Across Folds

至少報：

```text
Mean
Median
Worst Fold
Std Dev
Positive Fold Ratio
```

優先選跨 Fold 穩定模型，不選只在少數 regime 爆強的模型。

---

# 20. Execution Layer Separation

ML Benchmark 與 Trading Execution 必須分離。

## 20.1 ML Benchmark

```text
benchmark_entry_price = Open[t+1]
```

只用於評估市場 Opportunity。

## 20.2 Trading Fill

真正策略 / RL 使用：

```text
simulated_fill_price
slippage_bps
fill_ratio
entry_status
```

Concept：

```text
FillPrice = f(Open, OrderSize, Liquidity, Gap, Volatility, ExecutionRule)
```

## 20.3 Entry Status

至少：

```text
FILLED
PARTIAL_FILL
BLOCKED
NO_LIQUIDITY
NO_MARKET_DATA
SUSPENDED
PRICE_LIMIT_CONSTRAINT
```

Derived：

```text
entry_executable = status in {FILLED, PARTIAL_FILL}
```

## 20.4 Capacity

需支援：

```text
ParticipationRate = OrderValue / ReferenceTurnover
```

資金規模與 liquidity capacity 屬 Execution / Portfolio Layer，不應永久從 ML Universe 排除 Alpha。

## 20.5 Slippage V1

若尚無可靠 historical fill data，不建立假精準 ML slippage model。

優先做 sensitivity analysis，例如：

```text
0 bps
10 bps
25 bps
50 bps
```

## 20.6 Cost Separation

```text
TradingCost = ExplicitCost + ImplicitCost
```

Explicit：手續費、稅費等。

Implicit：slippage / market impact。

---

# 21. ML Outcome vs Trading Outcome

## 21.1 ML Outcome

回答：

> 推薦本身是否具有 Alpha？

```text
TargetHit
StopHit
MFE
MAE
TimeToTarget
```

## 21.2 Trading Outcome

回答：

> 實際策略有沒有把 Alpha 轉成 PnL？

```text
realized_return
net_return
holding_days
entry_slippage
exit_slippage
fees
capital_usage
max_position_drawdown
```

兩組 Outcome 永遠分開。

---

# 22. Production Serving

## 22.1 Daily Pipeline

```text
Market Close t
    │
    ▼
Data Ingestion
    │
    ▼
Data Quality Gate
    │
    ▼
Feature Snapshot(t)
    │
    ▼
Feature Health Gate
    │
    ▼
Champion Model Inference
    │
    ▼
Calibration
    │
    ▼
Prediction Health Gate
    │
    ▼
Recommendation Policy
    │
    ├── Recommendation
    └── NO_TRADE
```

## 22.2 Immutable Run

每次 production inference 建立 immutable run：

```text
run_id
signal_date
as_of_timestamp

data_snapshot_id
universe_version
feature_version
model_version
calibration_version
policy_version
```

禁止日後重算覆蓋當時結果。

---

# 23. Production Monitoring

## 23.1 Data Quality

至少監控：

```text
Completeness
Freshness
Sanity
Coverage
```

需區分：

```text
Legitimate Missing
Systemic Missing
```

Systemic Missing 可觸發 `SYSTEM_NO_TRADE`。

## 23.2 Feature Drift

監控：

```text
PSI
KS
Mean / Std Shift
Percentile Shift
Missing Rate Shift
```

Cross-sectional rank feature 必須連同 underlying raw feature 一起監控。

## 23.3 Prediction Drift

至少保存 / 監控：

```text
mean
median
std
upper percentile
lower percentile
```

對象：

```text
p_target_*
p_stop_*
p_executable
pred_mfe_*
```

## 23.4 Recommendation Drift

至少：

```text
universe_count
qualified_count
recommendation_count
no_trade_flag
no_trade_reason
score_distribution
sector_distribution
market_cap_distribution
liquidity_distribution
```

Concentration 先監控，不預設強制 sector-neutral。

## 23.5 Delayed Outcome Monitoring

Immediate Health：當天可知。

Delayed Performance：需等待 3D / 5D / 10D outcome mature。

Rolling live metrics 建議至少：

```text
20D
60D
120D
```

例如：

```text
Lift@5_20D
Lift@5_60D
StopRate@5_60D
ECE_60D
```

---

# 24. Retraining / Promotion

## 24.1 Scheduled Retraining

```text
Monthly retraining
Rolling 3Y mature data
```

## 24.2 Drift 不直接等於 Promote

流程：

```text
Scheduled / Drift Trigger
       ↓
Train Challenger
       ↓
Walk-forward / Recent Validation
       ↓
Compare Champion
       ↓
Promote / Reject
```

Retrain 可自動；Promotion 必須通過 Champion criteria。

## 24.3 Champion / Challenger

Production 永遠區分：

```text
Champion
Challenger
```

新 Challenger 不可只因 AUC 提高而 Promotion。

必須通過：

- TargetLift
- StopRate
- Coverage
- Calibration
- 其他 Guardrails

## 24.4 Shadow Mode

新模型 Promotion 前支援：

```text
Champion -> live recommendation
Challenger -> shadow inference only
```

兩者 prediction / ranking / future outcome 都保存。

---

# 25. Version Separation

Serving Stack：

```text
ServingStack = Model + Calibration + Policy
```

三者獨立 version。

例如：

```text
model_version       = target10_lgbm_v7
calibration_version = target10_iso_v12
policy_version      = rec_policy_v5
```

Calibration 可以比 Model 更常更新。

---

# 26. Kill Switch / Fail-Closed

系統採 Fail-Closed。

可觸發 `SYSTEM_NO_TRADE`：

- 核心資料失敗
- Feature 大規模 drift
- Prediction distribution anomaly
- Calibration collapse
- Live performance 持續失效
- Manual halt

不得在系統 health 不可信時硬選股票。

---

# 27. Rollback / Audit

任一 Production Stack 必須可 rollback。

保存：

```text
model artifact
feature version
calibration artifact
policy config
training dataset reference
training code commit
metrics
promotion decision
```

任一歷史 Recommendation 必須可重建：

```text
Universe
 -> Features
 -> Raw Predictions
 -> Calibrated Predictions
 -> Gate Result
 -> Recommendation Score
 -> Rank
 -> Final Recommendation
```

產品層不要求 SHAP / 上榜理由，但後台必須具備 operational audit trail。

---

# 28. 未來 ML -> RL Interface（本 FRS 僅預留，不實作 RL）

ML 需輸出並保存下列欄位，供未來 RL State 使用：

```text
p_target_3d
p_target_5d
p_target_10d

p_stop_3d
p_stop_5d
p_stop_10d

pred_mfe_3d
pred_mfe_5d
pred_mfe_10d

p_executable
recommendation_score
rank
gate_pass
```

未來 RL State 預計由：

```text
MarketState
CandidateState
MLBelief
PositionState
PortfolioState
```

組成。

RL 不應重新承擔全市場 Alpha discovery。

---

# 29. V1 明確不做

V1 不做：

- 複雜 Multi-task Neural Network
- Transformer / Sequence Foundation Model
- Reinforcement Learning Trading Policy
- 高頻 tick execution model
- 新聞 / 社群 NLP
- 大量 TA permutation
- 固定每日必選 N 檔
- Random Split
- 使用 Final Holdout 調參
- In-sample Calibration
- In-sample Recommendation Policy tuning
- 將 Strategy PnL 當作唯一 ML Champion KPI

---

# 30. 尚未定案 / 必須由資料決定的參數

以下不得由 Claude Code 任意拍板，需做成 config 並由 Development / OOF Validation 決定：

```text
min_liquidity threshold
max_capacity_ratio

execution_gate_threshold
risk_gate_threshold
alpha_gate_threshold

recommendation_score weights
K_max

MFE winsorization / cap
class weights

LightGBM hyperparameters

calibration method (Platt vs Isotonic)

Champion guardrail thresholds
NetHit lambda

feature exact lookback set
```

---

# 31. 建議 Repository / Module Boundary

Claude Code 實作時，建議至少拆成：

```text
src/
  data/
    ingestion/
    point_in_time/
    universe/
    quality/

  features/
    price/
    volume/
    volatility/
    cross_sectional/
    regime/
    event/

  labels/
    canonical_outcome.py
    barriers.py
    multi_horizon.py

  datasets/
    sample_index.py
    builder.py
    weighting.py

  models/
    execution/
    target/
    stop/
    mfe/
    baselines/

  calibration/

  recommendation/
    gate.py
    scoring.py
    ranking.py
    policy.py

  validation/
    walk_forward.py
    purge.py
    metrics.py
    regime.py

  execution/
    benchmark.py
    simulator.py
    costs.py

  serving/
    daily_run.py
    snapshots.py

  monitoring/
    data_health.py
    feature_drift.py
    prediction_drift.py
    performance.py

  registry/
    versions.py
    champion_challenger.py

configs/
  universe/
  features/
  labels/
  models/
  policy/
  validation/
```

---

# 32. 最低驗收條件（Implementation Acceptance Criteria）

Claude Code 完成第一版時，至少應能：

1. 以指定 `signal_date` 建立當日全市場 PIT Universe。
2. 產生 deterministic Feature Snapshot。
3. 建立 `t+1 open` 基準之 Canonical Outcome。
4. 正確計算 target / stop first-hit ordering。
5. 產生 3D / 5D / 10D derived labels。
6. 建立 Purged Walk-forward split，禁止 label 跨 validation boundary。
7. 訓練 Execution / Target / Stop / MFE LightGBM models。
8. 產生 OOF predictions。
9. 執行 calibration 與 multi-horizon consistency correction。
10. 執行 Candidate Gate / Ranking / Dynamic Top-K / NO_TRADE。
11. 輸出完整 Evaluation Report，包括 Lift / StopRate / MFE / MAE / Coverage / Calibration。
12. 保存所有 Universe prediction，而非只保存 Top-K。
13. 任一 prediction run 可透過 version / snapshot 重建。
14. Final Holdout 與 Development dataset 在程式層有明確隔離。
15. Production Daily Run 遇到資料或模型 health failure 時可 Fail-Closed 成 `SYSTEM_NO_TRADE`。

---

# 33. 核心設計原則摘要

```text
1. 全台股找 Alpha，不只在注意股 / 高波動股找。
2. ML 的產品目標是「推薦進場」，不是單純預測價格。
3. Canonical Outcome 保存完整 future path，不把所有資訊壓成單一 label。
4. +10% / -5% 事件必須考慮先後順序。
5. 3D / 5D / 10D Multi-Horizon 同時建模。
6. Gate First, Rank Second。
7. 每天允許 NO_TRADE。
8. Recommendation Policy 本身也必須用 OOF Validation 調參。
9. 禁止 Random Split；採 Purged Walk-forward。
10. Benchmark Entry 與 Execution Fill 嚴格分離。
11. ML Alpha Evaluation 與 Trading PnL Evaluation 嚴格分離。
12. Production Fail-Closed。
13. 所有 Model / Calibration / Policy / Dataset / Feature / Label 必須 versioned。
14. 所有歷史推薦必須可重現。
15. ML output 必須能直接成為未來 RL State。
```

---

# 34. 建議 Claude Code 第一階段實作順序

```text
Phase 1
Point-in-time data contract
→ Universe builder
→ Sample index

Phase 2
Feature pipeline
→ Feature snapshot/versioning

Phase 3
Canonical outcome
→ Barrier / first-hit engine
→ 3D/5D/10D labels

Phase 4
Purged walk-forward dataset
→ OOF infrastructure

Phase 5
Baseline + LightGBM model stack
→ Calibration
→ Horizon consistency

Phase 6
Recommendation Gate / Ranking / Dynamic Top-K

Phase 7
Evaluation report
→ regime breakdown

Phase 8
Production snapshot / monitoring / champion-challenger skeleton

Phase 9
Execution simulator interface
→ 預留 RL integration
```

---

# 35. Implementation Constraint

若現有專案已有資料表、feature pipeline、model registry、backtest framework，應優先做 **adapter / migration**，不要為了符合本文件而平行重建第二套系統。

Claude Code 在開始大幅修改前，應先盤點：

```text
existing data schema
current universe definition
current label generation
current model training flow
current /app/level1 recommendation output
existing model registry / versioning
existing validation / backtest code
```

再提出 migration plan。

本 FRS 定義的是 **target architecture / behavioral contract**，不是要求無條件重寫現有 repository。

---

# 附錄 A. 實作決定（2026-09-30，依 §35 盤點後定案）

- 子專案拆分：A = §3–§11 + §19（Phase 1–4）；B = §12–§18（Phase 5–7）；C = §22–§27 + `/app/level1`（Phase 8–9）。
- 新套件 `backend/app/mlentry/`，YAML config 在 `backend/configs/mlentry/`，資料集 Parquet 在 `backend/data/mlentry/<dataset_version>/`。v2 `level1_predictions` 與 Level 2 `P5_live` 完全不動。
- 沿用：`research.level1.universe.eligible_ids`、`research.level1.prices`、`research.level2.costs.up_limit/down_limit`。
- 交易日曆 = `market_index` 日期集合；所有 t+k 皆為 calendar 位置運算。
- Universe eligibility 鏈：有 PIT 價格 → 歷史夠長（calendar 基準）→ 歷史無缺口（coverage ≥ 0.95）→ hard 品質規則 → 可選流動性（預設 null）。輸出 `eligible`、`eligibility_flags` bitmask、`primary_exclusion_reason`。處置股、注意股、波動度、成交量、市值、產業一律不篩。
- 品質 hard invalid 只用結構規則（OHLC 缺、high<low 等）；`ABNORMAL_RETURN`、`PRICE_LIMIT_VIOLATION`、`CORPORATE_ACTION_SUSPECT` 為 soft flag。
- Feature 函式只接受 `FeatureContext`，不得讀 DB（架構測試）；`max_feature_lookback` 由 registry 推導。
- 基本面 PIT：`first_seen` 為入庫觀測日，側表 2026-08-27 啟用；正確規則 `available_at = max(法定期限, first_seen)`，回補列退回法定期限並標 `pit_assumed`。v1 特徵不吃基本面、法人、融資。
- Barrier：k=1 為進場日本身；同日雙觸 = `STOP_AMBIGUOUS`；漲停開盤 = `PRICE_LIMIT_CONSTRAINT`，列保留、outcome NaN；`label_available_date` = 第 10 個路徑日。
