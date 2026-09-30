# Observation Hardening v1 — Spec A：線上觀測（§23 監控補強＋§22 稽核 metadata）

- 日期：2026-09-30
- 分支：`feature/observation-hardening-v1`
- 定位：**純觀測，不動 ML／policy。** mlentry 處於 Prospective Observation freeze（serving stack 釘死 `mlentry_lgbm_6613b41a_20250814` + c_033de279 + policy_baseline_v1）。本 spec 只增加「這次 run 用了什麼」（audit）與「這次 run 看起來有沒有異常」（diagnostics）的記錄與顯示。
- 姊妹 spec（另寫）：Spec B「離線診斷＋lifecycle 骨架」（§18 唯讀 evaluation diagnostics、§24 challenger registry／promotion audit，全 disabled）。

## 0. 硬規則（Global Constraints）

1. 四個既有 health gate（`data_quality_gate`、`feature_health_gate`、`prediction_health_gate`、`recommendation_drift`）與 `monitoring.yaml` 的 `data_quality / feature_drift / prediction_health / live_metrics / manual_halt` 區**一字不改**。diagnostics 永不觸發 `SYSTEM_NO_TRADE`，永不改 run status。
2. `configs/mlentry/` 只允許新增 `monitoring.yaml` 的 `diagnostics:` 區。模型、feature、calibration、gate 門檻、ranking、K、promotion、holdout 不碰。
3. diagnostics 與 audit 在 policy **之後**執行；任一例外只記 log、寫進 envelope、run 照常落地。
4. `audit_json`（provenance）與 `health_json.diagnostics`（異常觀測）分開存；audit 不放診斷，診斷不放 provenance。
5. UI 的 attention 只換顏色（琥珀），**絕不**影響健康條四個 gate 燈號、徽章或判讀句。
6. 不額外讀 `daily_prices` 全表；診斷只用 `run_daily` 已載入的矩陣與一次 `company_profile` 查詢。
7. 所有 content fingerprint 統一 `sha256(canonical_json).hexdigest()[:12]`；`code_commit` 沿用 git hash。
8. 例外訊息只進 log；API／UI 只看得到 `error_type`。
9. **不得原地修改被觀測的 frozen champion artifact 目錄內任何既有檔案**（`stack.json`、`artifacts.json`、`feature_reference.json`、模型與 calibrator 檔）。新增 metadata 一律用 sidecar 新檔；只有未來新訓練的 stack 才原生帶新 schema。

## 1. Diagnostic envelope（四個診斷共用）

```json
正常評估   {"evaluated": true,  "attention": false|true, ...values}
無法評估   {"evaluated": false, "attention": false, "reason": "THRESHOLD_NOT_CONFIGURED" | "NO_DATA" | "NOT_APPLICABLE"}
例外       {"evaluated": false, "attention": false, "error_type": "ValueError"}
```

- `attention` 只在 `evaluated == true` 時可能為 true。
- 門檻缺鍵 → 仍計算數值並回傳，但 `evaluated:false, reason:THRESHOLD_NOT_CONFIGURED`。
- `attention_count = count(attention == true)`；run 完全沒有 `diagnostics` 鍵（舊 run）才回 `null`。

## 2. `monitoring/diagnostics.py`（純函式）

### 2.1 `freshness(as_of, cal, sources, cfg) -> dict`

來源分兩種 mode，由 yaml 逐來源指定：

| 來源 | mode | 量測 | 預設 `max_lag_days` |
|---|---|---|---|
| `daily_prices` | `business_date` | 已載入 `m["close"]` 最後一列有值的日期 vs `as_of`，差以交易日計 | 0 |
| `market_index` | `business_date` | `mkt` series 最後有值日期 vs `as_of` | 0 |
| `attention_listings` | `ingestion_watermark` | 最近一筆 `pipeline_runs`（`status != running`）其 `steps` 內 `name == "attention" and status == "ok"` 的 `trading_date` vs `as_of`，交易日差 | 1 |

- `ingestion_watermark` 用 1 的理由：`pipeline_runs.steps` 在整條 pipeline 結束才落地，MLEntry step 執行時看不到自己這一輪，落後 1 個交易日是常態，2 以上代表前一日 ingest 失敗。不改 scheduler。
- `fundamentals`／`flows` 只在 champion feature families 含這些家族時才列（目前不含 → 不列）。
- 回傳：`{"evaluated", "attention", "sources": {name: {...}}}`；任一來源 `lag_days > max_lag_days` → 整體 attention。每來源欄位：
  - `business_date` 模式：`{"mode", "max_date", "lag_days", "attention"}`。
  - `ingestion_watermark` 模式：`{"mode", "watermark_pipeline_run_id", "watermark_business_date", "watermark_completed_at", "lag_trading_days", "attention"}`（`lag_days` 即 `lag_trading_days`，欄名保留後者），讓 `attention=true` 時能直接指出是哪一次 ingest 落後。
- 來源查不到（空表）→ 該來源 `max_date: null, lag_days: null, attention: true`（缺資料本身值得提醒）。

### 2.2 `sanity_summary(elig_flags_row, hard_flags_row, cfg) -> dict`

- 輸入：`build_universe` 已算好的當日 `EligFlag` 列，與 `quality.hard_flags` 當日列（daily_run 已計算，傳入即可，不重算）。
- 輸出：`{"universe_present": n, "elig": {NO_PRICE: n, HISTORY_TOO_SHORT: n, HISTORY_GAPPED: n, DATA_QUALITY: n, LOW_LIQUIDITY: n}, "hard": {flag_name: n}, "hard_flag_count": n, "hard_flag_ratio": r}`。
- attention 只看結構性髒資料：`hard_flag_count >= min_hard_flag_count (5)` **且** `hard_flag_ratio > max_hard_flag_ratio (0.005)`。停牌、無成交、HISTORY_* 不算。
- 分母 = 當日 present（有價格列）的股票數。

### 2.3 `feature_shift(snapshot, feature_ref, cfg) -> dict`

- 只評估 `monitor_mode == "continuous"` 的特徵；`"skip"` 者列入 `skipped`。`monitor_mode` 由 §2.6 的 loader 提供，並在輸出附 `"monitor_mode_source": "explicit" | "legacy_day_level_fallback"`。
- 每特徵：
  - `mean_z = (mean_now − ref.mean) / ref.std`；`ref.std < 1e-12` → `mean_z: null, reason: "REFERENCE_STD_ZERO"`，不用 epsilon。
  - `std_ratio = std_now / ref.std`（同樣零除 → null）。
  - `quantile_cdf_gap_7pt = max_i |ECDF_now(q_i) − p_i|`，`q_i` 為參考 7 分位、`p_i ∈ {0.01,…,0.99}`。**這不是 two-sample KS**；欄位名固定，UI 文案固定為「7-point reference-quantile ECDF gap」。
- 彙總：`n_evaluated, n_mean_z_gt, n_gap_gt, top` （依 gap 排序前 10：`{name, mean_z, std_ratio, quantile_cdf_gap_7pt}`）、`skipped: [names]`。
- attention：`n_mean_z_gt >= max_features_mean_shift (8)` 或 `n_gap_gt >= max_features_gap (8)`；單特徵門檻 `mean_z_threshold 3.0`、`gap_threshold 0.2`。用 `>=`，與 config 名稱一致。

### 2.4 `recommendation_distribution(df, sector_map, mcap, turnover20, cfg) -> dict`

- `df` = policy 後全 U_t 表（含 `gate_pass`, `recommended`, `recommendation_score`）。
- `score_q`：qualified（`gate_pass`）列的 `recommendation_score` p10/p50/p90；`qualified_count == 0` → `null`。
- `sector`：`{"recommended": {sector_id: n}, "qualified": {sector_id: n}, "universe_share": {sector_id: share}, "max_sector": {"sector_id", "recommendation_share", "universe_share", "overweight"}}`；`recommendation_count == 0` → recommended 為空 dict、`max_sector: null`。
- `mcap_tercile`／`liquidity_tercile`：以全 U_t 有效值算三分位切點，回推薦落在 `{low, mid, high}` 的計數；分母用有效值數，另回 `missing_count`。
  - 市值 = `close(as_of) × company_profile.issued_shares`（run 當下查一次）；結果附 `"mcap_basis": "current_company_profile_at_run_time"`、`"issued_shares_missing_count"`。**不是 PIT 市值**，只供診斷；日後有 PIT shares 再升版。
  - 流動性 = `m["turnover"]` 最後 20 列均值。
- attention：`recommendation_count >= min_recommendations_for_concentration (3)` 且 `max_sector.recommendation_share > max_sector_share (0.60)` 且 `overweight > min_sector_overweight (2.0)`（overweight = recommendation_share / universe_share）。

### 2.5 `monitoring.yaml` 新增區（預設值）

```yaml
diagnostics:                      # §23 只記錄不阻擋；缺鍵 → evaluated:false
  freshness:
    daily_prices:       {mode: business_date,       max_lag_days: 0}
    market_index:       {mode: business_date,       max_lag_days: 0}
    attention_listings: {mode: ingestion_watermark, max_lag_days: 1, step: attention}
  sanity:
    min_hard_flag_count: 5
    max_hard_flag_ratio: 0.005
  feature_shift:
    mean_z_threshold: 3.0
    gap_threshold: 0.2
    max_features_mean_shift: 8
    max_features_gap: 8
  recommendation:
    min_recommendations_for_concentration: 3
    max_sector_share: 0.60
    min_sector_overweight: 2.0
```

### 2.6 `monitor_mode`：sidecar 為正式來源，`day_level` 只是 legacy fallback

`monitor_mode ∈ {continuous, skip}` 是 distribution-monitoring 語意；`day_level` 是資料粒度。兩者不同維度，不得互相定義。

- **Frozen champion 不動**（規則 9）。新增 sidecar `feature_reference.monitoring.json` 於 stack 目錄：
  ```json
  {"monitoring_schema_version": 1,
   "source_feature_reference_hash": "<sha256/12 of canonical feature_reference.json>",
   "features": {"ret_5d": {"monitor_mode": "continuous"}, "is_attention_stock": {"monitor_mode": "skip"}, ...}}
  ```
- 新增腳本 `scripts/mlentry_feature_monitoring_sidecar.py`（唯讀於 artifact；只寫 sidecar，冪等）：對 champion 每個特徵產生**明確**的 `monitor_mode`。mapping 來源 = 腳本內固定的 validated 名單 `SKIP_FEATURES`（19 個，依語意分三組）：
  - binary（5）：`is_attention_stock, is_disposition_stock, limit_up_today, limit_down_today, large_gap`
  - 離散計數／比例（6）：`limit_up_count_20d, limit_down_count_20d, consecutive_up_days, consecutive_down_days, positive_day_ratio, negative_day_ratio`
  - 日級／類股級（8）：`market_ret_1d, market_ret_5d, market_ret_20d, market_volatility, industry_ret_5d, industry_ret_20d, industry_strength_rank, breadth_ma20`

  任何未列入的特徵為 continuous（38 個，含 `dist_limit_up`）。名單以語意判定，恰與現行 champion 的 `day_level` 集合相同是巧合而非規則。腳本印出完整 mapping 供人工檢視，測試釘住兩組具體名單。
- Loader（`diagnostics.load_monitor_modes(stack_dir, feature_ref)`）：sidecar 存在且 `source_feature_reference_hash` 與現行 `feature_reference.json` 相符 → 用 sidecar，`monitor_mode_source="explicit"`；sidecar 不存在或 hash 不符 → `day_level → skip, 否則 continuous`，`monitor_mode_source="legacy_day_level_fallback"`，並 log warning。fallback 只為向後相容，不是分類規則。
- 未來新 stack：`train_stack.build_feature_reference` 原生在 `feature_reference.json` 每個特徵寫 `monitor_mode`（同一 validated 規則），loader 優先讀 artifact 內建值（`monitor_mode_source="explicit"`），其次 sidecar，最後 fallback。

## 3. `serving/audit.py`

`build_audit(con, requested_as_of, stack, feature_snapshot, sources_meta) -> dict`：

```json
{
  "requested_as_of": "2026-09-29",
  "feature_snapshot_as_of": "2026-09-29",
  "data_snapshot_id": "a1b2c3d4e5f6",
  "sources": {
    "daily_prices":       {"max_business_date": "...", "max_available_at": null, "rows_visible_at_as_of": 1783},
    "market_index":       {"max_business_date": "...", "max_available_at": null, "rows_visible_at_as_of": 1},
    "attention_listings": {"max_business_date": "...", "max_available_at": "<pipeline finished_at>", "rows_visible_at_as_of": 163}
  },
  "serving_stack_hash": "…12",
  "config_hashes": {"monitoring": "…12", "policy": "…12", "features": "…12"},
  "runtime": {"python": "3.x", "lightgbm": "…", "pandas": "…", "numpy": "…", "hostname": "…"},
  "code_commit": "4c723e6"
}
```

- `sources` 走 **run 實際可見的 view**：daily_prices／market_index 用已載入矩陣（`as_of` 列有值的股票數、最後日期）；attention 用 `pit.event_mask` 的 windows（announce ≤ as_of）計數；沒有 `available_at` 語意的來源填 `null`，不偽造。
- `data_snapshot_id = sha256(canonical_json(sources))[:12]`：比對用指紋，**不宣稱可重建資料**。
- `feature_snapshot_as_of = str(feature_snapshot 的 as_of 日期)`（由 `FeatureContext.as_of` 與 snapshot 產生時的日曆最後列取得）；與 `requested_as_of` 不同即為 audit 證據，但不由 audit 決定 fail-closed。
- `serving_stack_hash = sha256(canonical_json(stack.json 內容) + canonical_json(artifacts.json 內容))[:12]`；canonical = `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`。
- `config_hashes` 對三個 yaml 檔的**解析後物件**做 canonical hash（縮排、註解不影響）。
- 存 `MLEntryRun.audit_json`（新 TEXT 欄，`_COLUMN_ADDITIONS["mlentry_runs"] = {"audit_json": "TEXT"}`）。

## 4. `daily_run` 整合

```text
… policy → status 判定 → gates["recommendation"] = recommendation_drift(...)   （既有，不動）
→ gates["diagnostics"] = _safe(diagnostics.*)   # 四鍵，各自 try/except → envelope
→ audit = _safe(build_audit)                     # 失敗 → {"error_type": "..."}，仍存
→ run_row["health_json"] = json.dumps(gates); run_row["audit_json"] = json.dumps(audit)
```

- `_safe` 只 `log.exception` 並回 envelope；不回傳 message。
- diagnostics 需要的額外輸入（`hard_flags` 當日列、`elig_flags` 當日列、sector_map、issued_shares）在 run 內取得；`issued_shares` 一次 `SELECT stock_id, issued_shares FROM company_profile`。
- 既有測試 fixture 的 gates 內容在改動前後 canonical JSON 相同（見 §7）。

## 5. API

- `RunInfo` 新增 `diagnostics: dict | None`（原樣透傳，舊 run 為 `null`）與 `audit: dict | None`（**白名單** `AuditSummary`：`requested_as_of, feature_snapshot_as_of, data_snapshot_id, serving_stack_hash, code_commit, as_of_mismatch: bool`；hostname、runtime、sources、config_hashes 不出 API）。
- `/health.history[]` 加 `attention_count: int | null`。
- 例外 envelope 只含 `error_type`；後端在組 `RunInfo` 前再過一次 `_strip_messages`（防呆：移除任何 `error`/`message` 鍵）。
- 所有新欄位缺失 → `null`，不 500。

## 6. 前端（`/app/level1` 系統狀態頁）

- `client.ts`：`MLEntryDiagnostic`（envelope 型）、`MLEntryDiagnostics`（四鍵）、`MLEntryAuditSummary`；`MLEntryRun.diagnostics`, `.audit`；`history[].attention_count`。
- 系統狀態頁在四張 gate 卡之後新增區塊「觀測診斷（只記錄，不影響出單）」：四張卡（資料新鮮度／結構檢查／特徵分布位移／推薦分布），卡頭燈號：`evaluated:false` 灰、`attention:true` 琥珀、否則暗綠；各卡列 3–6 個關鍵數；特徵卡副標固定「7-point reference-quantile ECDF gap（非 KS）」；推薦卡標「市值＝run 當下股本×收盤，非 PIT」。
- run 敘事下加一行：`snapshot {data_snapshot_id}・stack {serving_stack_hash}・commit {code_commit}`；`as_of_mismatch` 時以 rose 顯示「特徵快照日 ≠ 請求日」。
- run 歷史表加欄「提醒數」（`attention_count`，null 顯示「—」）。
- 健康條、徽章、四燈號、判讀句不變。

## 7. 測試

- `tests/test_mlentry_diagnostics.py`（純函式）：每診斷的正常／attention／缺門檻（`evaluated:false, THRESHOLD_NOT_CONFIGURED`）／空輸入；`ref_std=0 → mean_z null + REFERENCE_STD_ZERO`；`monitor_mode` 缺鍵退回 day_level；skip 特徵不進 `top`；gap 對同分布 < 0.05、對平移 1σ 的常態 > 0.2；sector attention 三條件缺一即 false；watermark 用假 `pipeline_runs` 列（前一日 ok → lag 1 不 attention；前兩日 → attention；step failed → 找更早的 ok）。
- `tests/test_mlentry_audit.py`：canonical hash 對 key 重排／縮排不變、改值即變；`max_available_at` null；`feature_snapshot_as_of` 來自 snapshot；AuditSummary 白名單不含 hostname。
- `tests/test_mlentry_serving.py`（既有 env fixture）：
  - 改動前先把 `run_daily` 產生的 `gates` 四個 gate 子樹 canonical JSON 存成 fixture 常數；改後斷言相同。
  - `health_json.diagnostics` 四鍵齊、每鍵有 `evaluated`／`attention`；`audit_json` 可 parse 且含 `data_snapshot_id`。
  - monkeypatch `diagnostics.feature_shift` 拋 `ValueError("secret path")` → run status 不變、該鍵 `{"evaluated": false, "attention": false, "error_type": "ValueError"}`、JSON 內不含 "secret path"。
- `tests/test_mlentry_api.py`：新欄位出現；舊 run（`audit_json` NULL、`health_json` 無 diagnostics）→ `audit: null, diagnostics: null, attention_count: null`，200。
- `scripts/mlentry_feature_monitoring_sidecar.py` 測試：只寫 sidecar、artifact 目錄其他檔案逐 byte 不變（改前後 sha256 比對）；冪等；`source_feature_reference_hash` 正確；38 continuous／19 skip 的具體名單釘住。
- `load_monitor_modes` 測試：sidecar 存在且 hash 符 → explicit；hash 不符或缺檔 → legacy fallback＋warning；artifact 內建 `monitor_mode` 優先於 sidecar。
- 前端 `npm run build`＋瀏覽器實測（:8001 backend-verify）。

## 8. 檔案清單

| 檔案 | 動作 |
|---|---|
| `backend/configs/mlentry/monitoring.yaml` | 新增 `diagnostics:` 區（其他區不動） |
| `backend/app/mlentry/monitoring/diagnostics.py` | 新增 |
| `backend/app/mlentry/serving/audit.py` | 新增 |
| `backend/app/mlentry/serving/daily_run.py` | policy 後掛 diagnostics／audit；`audit_json` 寫入 |
| `backend/app/mlentry/serving/train_stack.py` | `build_feature_reference` 為**未來新 stack** 原生寫 `monitor_mode`（不觸碰現行 champion） |
| `backend/scripts/mlentry_feature_monitoring_sidecar.py` | 新增：只寫 `feature_reference.monitoring.json` sidecar，artifact 既有檔案不動 |
| `backend/data/mlentry/serving/<champion>/feature_reference.monitoring.json` | 執行腳本產生（gitignored data 目錄） |
| `backend/app/storage/models.py`、`database.py` | `MLEntryRun.audit_json`；`_COLUMN_ADDITIONS` |
| `backend/app/api/routes_mlentry.py` | `RunInfo.diagnostics/audit`（AuditSummary 白名單）、`history[].attention_count` |
| `frontend/src/api/client.ts`、`components/MLEntrySystem.tsx` | 型別、診斷區、audit 摘要、提醒數欄 |
| tests 如 §7 | 新增／修改 |

## 9. 明確不做

- 任何新的 fail-closed 條件；任何 gate 門檻調整。
- 完整樣本 two-sample KS；discrete 特徵的 value_rate 診斷（下版）。
- PIT 市值；scheduler 改動（in-process watermark）。
- 任何對 frozen champion artifact 既有檔案的寫入（含「只加 metadata」）。
- 歷史 run 回填診斷。
- §18 evaluation diagnostics 與 §24 lifecycle（Spec B）。
