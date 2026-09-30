# Observation Hardening v1 — Spec B：§18 唯讀 evaluation diagnostics＋§24 lifecycle 骨架

- 日期：2026-10-01
- 分支：`feature/observation-hardening-v1`（接在 Spec A 之後）
- 定位：**純觀測＋骨架，不動 ML／policy。** Spec A 的所有硬規則沿用（見該 spec §0），本文件只列新增規則。
- 前置：Spec A 已完成（`fingerprint.py`、`audit.py`、`health_json.diagnostics`、`/health` 端點結構）。

## 0. 硬規則（新增於 Spec A §0 之上）

1. §18 全部輸出標 `"diagnostic_only": true, "not_used_for_policy": true`；任何 regime 結果**不得**回頭改 Gate／Feature／Policy／門檻。UI 每張表固定標籤 `Diagnostic only · Not used for policy selection`。
2. §18 只算 **Frozen dev OOF**；Live 側本 spec 不算，UI Live 欄顯示「等待 60D（目前 N）」。不讀 holdout，不跑 policy grid，不寫回任何 artifact 或 dataset 檔案；唯一輸出是 `policy/<policy>/diagnostics_frozen.json`。
3. Regime 切點由 dev 資料分位算出並寫進輸出 JSON；程式碼不硬寫任何門檻。樣本 `n < 30` 的格子值為 `null`。
4. §24 骨架全部 **disabled**：`observation_freeze: true`、`auto_retrain: false`、`auto_promote: false`；凍結中任何 champion 變更（set_champion／promote／rollback）一律拒絕並寫 audit。解凍只有人為改 yaml，沒有自動條件。
5. `versions.py` 只允許在 `set_champion` 與 `promote` 各加一行閘門呼叫、`set_champion` 多寫 `previous_model_version`；`load_champion` 與 `daily_run` 行為不變。
6. 沒有排程、沒有自動 retrain、沒有 challenger 評估流程、沒有 rollback CLI／API 寫入端、沒有 DB 表。
7. 市值分組用 **現行** `company_profile.issued_shares × 當日 close`（非 PIT），輸出附 `mcap_basis: "current_company_profile"`。

## 1. §18 唯讀 evaluation diagnostics

### 1.1 模組 `app/mlentry/evaluation/diagnostics_frozen.py`（純函式）

輸入 `df`：`scripts.mlentry_policy_report.load_frame(ds_dir)` 的評估列（`target.notna() & matured == 1`）再 merge `policy/<policy>/per_row.parquet`（`p_target_vn, p_stop_vn, gate_pass, recommendation_score, rank, recommended`），並由 `datasets.api.load_development(outcome_columns=[..., "target_first_hit_day"], feature_columns=["atr_pct", "market_ret_20d", "market_volatility", "breadth_ma20"])` 補欄。另需 `sector_map: pd.Series`（stock_id → sector_id）、`mcap: pd.Series`（sample_id → close × issued_shares）。

| 函式 | 輸出 |
|---|---|
| `lift_at_k(df, ks=(1,3,5,10)) -> dict` | 對 `gate_pass` 列依 `recommendation_score` 每日 Top-K（`volatility_control.daily_topk`）；每 K：`{"row_weighted": {"target_rate","market_target_rate","target_lift","stop_rate","stop_ratio","n","days_with_rec"}, "day_weighted": {同鍵，每日先算比率再取平均}}`。row-weighted 沿用 `policy_metrics.daily_aggregates + point_estimates`。 |
| `timing(df, k=5) -> dict` | Top-K 推薦列：`p_target_le_3d, p_target_le_5d, p_target_le_10d`（分母＝推薦列數）、`median_time_to_target`（只算 `event_type == TARGET` 的 `target_first_hit_day`）、`n`。 |
| `ranking_diagnostics(df, k=5) -> dict` | `precision_at_k`（＝Top-K target rate）、`recall_at_k`（每日 Top-K 命中的 target 數 ÷ 當日 gate_pass 列的 target 總數，日均，當日 target 為 0 的日子略過）、`ndcg_at_k`（binary relevance＝target，理想序＝當日 target 列排前，日均）、`ic`：每日 Spearman(recommendation_score, return_10d) 在 gate_pass 列上的 `mean, std, positive_share, days`（當日 gate_pass < 5 列略過）。 |
| `regime_breakdown(df, k=5) -> dict` | 五種拆法，每組 `{"lift_at_5", "stop_ratio_at_5", "n", "days"}`，`n < 30` 全為 null：`market` = `market_ret_20d` dev 三分位 `bear/neutral/bull`；`volatility` = `market_volatility` 中位數 `low/high`；`breadth` = `breadth_ma20` 中位數 `low/high`；`mcap` = 三分位 `small/mid/large`；`industry` = `sector_id`，只列推薦列數 ≥ 30 的類股，依 n 排序。輸出附 `"cuts": {...}` 切點與 `"mcap_basis"`。 |
| `build_diagnostics(df, sector_map, mcap, policy_name, dataset_version) -> dict` | 組合上四者，加 `generated_at`、`policy_name`、`dataset_version`、`k`、`n_eval_rows`、`n_days`、`diagnostic_only: true`、`not_used_for_policy: true`。 |

規則：regime 分組的基率用**該組內**的 market target／stop rate（組內 lift）；`recommendation_score` 為 NaN 的列不進 Top-K；`gate_pass` 列為 0 的日子 Top-K 為空。

### 1.2 腳本 `scripts/mlentry_frozen_diagnostics.py`

`python -m scripts.mlentry_frozen_diagnostics [--dataset DIR] [--k 5]`：
- champion 由 `load_champion()` 取得；dataset 預設 `DEFAULT_ROOT / champ.dataset_version`；`metrics.json` 的 `policy` 必須等於 `champ.policy_name`，否則拒絕。
- 讀取全部唯讀；寫入只有 `policy/<policy>/diagnostics_frozen.json`。**不讀** `holdout/`（不呼叫 `load_final_holdout`）。
- 印出摘要（Lift@1/3/5/10、timing、IC、每個 regime 的 n）。

### 1.3 API

`/api/mlentry/health` 新增：
- `diagnostics_frozen: dict | null`（讀 `data/mlentry/<champion ds>/policy/<policy>/diagnostics_frozen.json`，缺檔或壞檔 → null，不 500；讀法與 Spec A `_frozen_stats` 相同）。
- `live_gate: {"mature_days": int, "decide_at": 60, "live_unlocked": bool}`（`live_unlocked = mature_days >= 60`；`decide_at` 與 Spec A `live_progress.decide_at` 同一常數）。

`/api/mlentry/status` 新增 `lifecycle`（見 §2.5）。

### 1.4 UI（體檢頁）

最下方新增 `<details>` 折疊區「診斷（只看，不用於 policy）」，預設收合。四張表：Lift@K（K 列 × row/day-weighted 欄）、Timing、Ranking、Regime（五個分組各一小表）。每表右上固定 `Diagnostic only · Not used for policy selection`；Live 欄整欄顯示「等待 60D（目前 {mature_days}）」；`null` 顯示「—」並在 hover 顯示 `n<30`。`diagnostics_frozen` 為 null 時顯示「尚未產生：執行 `python -m scripts.mlentry_frozen_diagnostics`」。

## 2. §24 lifecycle 骨架

### 2.1 `monitoring.yaml` 新增區（只新增）

```yaml
lifecycle:
  observation_freeze: true          # 觀察期：拒絕任何 champion 變更；解凍只由人改此值
  freeze_until_mature_days: 60      # 宣告用，不自動解凍
  auto_retrain: false
  auto_promote: false
```

### 2.2 `registry/lifecycle.py`（新模組）

```python
class FreezeError(PermissionError): ...

def lifecycle_config() -> dict                      # load_yaml("monitoring").get("lifecycle") or {}；缺鍵 → observation_freeze 視為 True（fail-closed）
def freeze_state(session=None) -> dict             # {"observation_freeze", "freeze_until_mature_days", "auto_retrain", "auto_promote", "mature_days": int|None}
def guard_champion_change(action: str, actor: str | None, model_version: str | None, root=SERVING_ROOT) -> None
    # observation_freeze → append audit {"event": "refuse", "action": action, ...} → raise FreezeError
def append_audit(event: dict, root=SERVING_ROOT) -> None      # promotion_audit.jsonl append-only，自動加 "at"（UTC ISO）與 freeze_state 快照
def read_audit(root=SERVING_ROOT, limit=100) -> list[dict]
def register_challenger(stack: ServingStack, evaluation: dict | None, note: str = "", actor: str | None = None, root=SERVING_ROOT) -> dict
    # challengers.json（schema_version 1）；同 model_version 更新（冪等）；status "registered"；寫 audit "register_challenger"；不碰 champion.json
def list_challengers(root=SERVING_ROOT) -> list[dict]
def mark_challenger(model_version, status: Literal["registered","rejected","promoted"], note, actor, root=SERVING_ROOT) -> dict   # 寫 audit
def rollback(actor: str, reason: str, root=SERVING_ROOT) -> ServingStack
    # guard_champion_change("rollback") → champion.json.previous_model_version 必須存在且該 stack 目錄存在 → set_champion(previous) → audit "rollback"
```

`challengers.json` schema：
```json
{"schema_version": 1, "challengers": [{"model_version": "...", "registered_at": "...", "registered_by": null,
  "dataset_version": "...", "feature_version": "...", "policy_name": "...", "code_commit": "...",
  "status": "registered", "evaluation": {"target_lift_at_5": null, "stop_ratio_at_5": null, "coverage": null, "worst_fold_lift_at_5": null},
  "promotion_check": {}, "note": ""}]}
```

`promotion_audit.jsonl` 每行：`{"at", "event": "promote|rollback|refuse|register_challenger|mark_challenger", "action": str|null, "actor", "model_version", "from_model_version", "reason", "freeze_state": {...}}`。

### 2.3 `registry/versions.py` 的最小改動

- `set_champion(stack, root, *, actor=None)`：第一行 `guard_champion_change("set_champion", actor, stack.model_version, root)`；寫 champion.json 時多存 `"previous_model_version": <現行 champion.json 的 model_version 或 null>`；成功後 `append_audit({"event": "set_champion", ...})`。
- `promote(...)`：第一行 `guard_champion_change("promote", approved_by, stack.model_version, root)`；其餘不變（仍要求 `eligible` 與 `approved_by`）；成功後 audit `"promote"`。
- `load_champion`、`ServingStack` 不變。

### 2.4 `serving/train_stack.py`

`train_stack(..., register_as: Literal["champion", "challenger"] = "champion", actor: str | None = None)`：`"champion"` → 原行為（凍結時 `set_champion` 會 raise `FreezeError`，artifact 已存檔，例外向上拋）；`"challenger"` → `register_challenger(stack, evaluation=stack.frozen_validation, actor=actor)`，不呼叫 `set_champion`。CLI `scripts/mlentry_train_stack.py` 加 `--as-challenger` 旗標（若該 CLI 存在；不存在則不新增）。

### 2.5 API／UI（唯讀）

`/api/mlentry/status` 新增：
```json
"lifecycle": {"observation_freeze": true, "freeze_until_mature_days": 60, "mature_days": 0,
              "auto_retrain": false, "auto_promote": false,
              "challengers_count": 0, "previous_model_version": null, "last_audit_event": {...}|null}
```
系統狀態頁「Serving stack 版本」折疊區內多列：`observation_freeze`（true 時琥珀「觀察凍結中，promotion 已鎖」）、`freeze_until_mature_days`、`auto_retrain`、`auto_promote`、`challengers_count`、`previous_model_version`、`last_audit_event`（事件＋時間）。

## 3. 測試

- `tests/test_mlentry_diagnostics_frozen.py`（合成 df）：Lift@K row/day-weighted 手算相符；timing 的 `p_target_le_H` 與中位數；recall／NDCG 在已知小例子上的值；IC 對完美排序＝1、反序＝−1；regime 切點寫進輸出、`n<30` → null、industry 只列 ≥30；`diagnostic_only`／`not_used_for_policy` 恆為 true；`build_diagnostics` 對 champion 真實 OOF 跑一次（smoke，只檢查結構與 K 鍵）。
- 腳本測試：policy 不符 → 拒絕；輸出只寫 `diagnostics_frozen.json`（dataset 目錄其他檔案 sha256 不變）；不觸碰 `holdout_access.log`。
- `tests/test_mlentry_lifecycle.py`（`tmp_path` 當 serving root，monkeypatch `load_yaml`）：凍結時 `set_champion`／`promote`／`rollback` 皆 `FreezeError` 且 audit 多一筆 `refuse`；解凍時 `promote` 仍需 `eligible` 與 `approved_by`；`set_champion` 寫 `previous_model_version`；`rollback` 切回並寫 audit；`register_challenger` 冪等、不改 champion.json；`mark_challenger` 狀態流轉；`lifecycle_config` 缺鍵 → freeze True。
- `test_mlentry_serving.py` env fixture：`load_yaml` monkeypatch 加 `lifecycle: {observation_freeze: false, ...}` 讓既有 `train_stack` 測試照常；新增一案例凍結為 true → `train_stack(register_as="champion")` raise `FreezeError` 但 artifact 目錄存在；`register_as="challenger"` 成功且 champion.json 不變。
- API：`/health` 有 `diagnostics_frozen`（缺檔 null）與 `live_gate`；`/status` 有 `lifecycle`；舊環境無 challengers.json／audit → `challengers_count 0`、`last_audit_event null`。
- 前端 `npm run build`＋瀏覽器：診斷折疊區四表、Live 欄「等待 60D」、lifecycle 列。

## 4. 檔案清單

| 檔案 | 動作 |
|---|---|
| `backend/app/mlentry/evaluation/diagnostics_frozen.py` | 新增 |
| `backend/scripts/mlentry_frozen_diagnostics.py` | 新增 |
| `backend/app/mlentry/registry/lifecycle.py` | 新增 |
| `backend/app/mlentry/registry/versions.py` | `set_champion`／`promote` 各加閘門一行；`previous_model_version`；audit |
| `backend/app/mlentry/serving/train_stack.py` | `register_as` 參數 |
| `backend/configs/mlentry/monitoring.yaml` | 新增 `lifecycle:` 區 |
| `backend/app/api/routes_mlentry.py` | `/health` `diagnostics_frozen`、`live_gate`；`/status` `lifecycle` |
| `frontend/src/api/client.ts`、`MLEntryHealth.tsx`、`MLEntrySystem.tsx` | 型別、診斷折疊區、lifecycle 列 |
| `backend/data/mlentry/<ds>/policy/<policy>/diagnostics_frozen.json` | 腳本產生（gitignored） |
| `backend/data/mlentry/serving/challengers.json`、`promotion_audit.jsonl` | 首次寫入時建立（gitignored） |
| tests 如 §3 | 新增／修改 |

## 5. 明確不做

- Live 側 §18 指標（60D 後另開 spec）。
- 任何門檻／規則由 regime 結果推導；任何自動解凍。
- 排程、自動 retrain、challenger 評估流程、rollback CLI／API 寫入端、DB 表。
- PIT 市值。
