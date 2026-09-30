# /app/level1 UI 改版 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 依 spec `docs/superpowers/specs/2026-09-30-mlentry-level1-ui-redesign-design.md` 改版 `/app/level1` 三分頁：健康條判讀句、緊湊推薦表＋估算價、追蹤中量尺、Frozen vs Live 收斂對照表、系統狀態人讀化。

**Architecture:** 後端新增兩個純函式模組（`serving/presentation.py` 判讀句與估算價、`serving/tracking.py` 追蹤路徑）、擴充 `monitoring/performance.py`（Lift@1/3、收斂判定、逐日成熟），一支唯讀腳本把 B9 `metrics.json`＋ECE 轉存為 `frozen_stats.json`；`routes_mlentry.py` 只做組裝。前端改寫五個元件、新增一個。所有判定語意在後端，前端只 render。

**Tech Stack:** FastAPI + SQLAlchemy + pandas（backend/.venv）、pytest；React + TypeScript + TanStack Query + Tailwind（frontend，`npm run build` = `tsc --noEmit && vite build`，無前端單元測試框架）。

## Global Constraints

- 附錄 C 凍結：不改任何模型、feature、calibration、gate 門檻、ranking 權重、K、promotion 門檻、holdout；`configs/mlentry/*.yaml` 一律不改。
- 唯一寫入端改動：`feature_health_gate` 的 detail 多存 `drifted_psi`（不改判定）。
- barrier 語意只有一個來源：`app/mlentry/labels/barriers.run_barriers`；追蹤不得另寫 first-hit 邏輯。
- 升降單位只有一個來源：`app/research/level2/costs.tick_size` 系列。
- 前端不自帶判定門檻或解讀語意；收斂判定、判讀句、NO_TRADE 文案由後端提供。
- 「Research Shadow」徽章不得移除（tooltip：`未通過 promotion contract，非正式進場推薦`）。
- 台股色：Target／漲＝rose（紅）、Stop／跌＝emerald（綠）。
- 端點資料缺失一律回空結構，不回 500。
- 後端測試指令一律在 `backend/` 下：`.venv/Scripts/python.exe -m pytest ...`。
- Commit 訊息結尾：`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。

## Spec 修正（Task 0 一併提交）

- §3.2／§3.3：候選數／日的分布帶改為 **p5–p95**（B9 `metrics.json` 的 `qualified_count_quantiles` 只有 0.05／0.25／0.5／0.75／0.95）；Frozen 描述統計來源改為「B9 `metrics.json` 轉存＋OOF 計算 ECE」，不讀 per_day／per_row。
- §2.1：估算價改為重用 `app/research/level2/costs` 的取整函式（新增公開別名），不新建 `ticks.py`；函式放 `serving/presentation.py`。
- §1：OK 狀態的「候選數常態」比對對象為 Frozen OOF p5–p95（`recommendation_drift` 沒有偏離旗標，不另發明門檻）。

## File Structure

| 檔案 | 責任 |
|---|---|
| `backend/app/research/level2/costs.py` | 修改：新增 `round_down_tick`／`round_up_tick` 公開別名 |
| `backend/app/mlentry/serving/presentation.py` | 新增：`est_barrier_prices`、`build_verdict`（純函式） |
| `backend/app/mlentry/serving/tracking.py` | 新增：`track_paths`（純函式，重用 run_barriers）、`load_tracking`（DB 讀取組裝） |
| `backend/app/mlentry/monitoring/health.py` | 修改：`feature_health_gate` detail 加 `drifted_psi` |
| `backend/app/mlentry/monitoring/performance.py` | 修改：`load_matured` 多取 run 欄位；`rolling_live_metrics` 加 lift@1/3、coverage、候選數、NO_TRADE 率；新增 `daily_matured`、`convergence` |
| `backend/scripts/mlentry_frozen_stats.py` | 新增：B9 metrics.json＋ECE → `frozen_stats.json`（唯讀） |
| `backend/app/api/routes_mlentry.py` | 修改：verdict、估算價、gate_thresholds、live_progress、frozen_stats 合併、convergence、`/tracking` |
| `backend/tests/test_mlentry_presentation.py` | 新增 |
| `backend/tests/test_mlentry_tracking.py` | 新增 |
| `backend/tests/test_mlentry_performance_ui.py` | 新增 |
| `backend/tests/test_mlentry_serving.py` | 修改：drifted_psi 測試 |
| `backend/tests/test_mlentry_api.py` | 修改：新欄位與 `/tracking` |
| `frontend/src/api/client.ts` | 修改：型別與 `useMLEntryTracking` |
| `frontend/src/components/MLEntryStatusBanner.tsx` | 改寫：健康條 |
| `frontend/src/components/MLEntryBoard.tsx` | 改寫：緊湊表格＋展開列 |
| `frontend/src/components/MLEntryTracking.tsx` | 新增：追蹤中表格＋量尺 |
| `frontend/src/components/MLEntryHealth.tsx` | 改寫：進度條、收斂表、逐日成熟 |
| `frontend/src/components/MLEntrySystem.tsx` | 改寫：敘事、人讀 gate、run 歷史、折疊區 |
| `frontend/src/pages/MLEntryPage.tsx` | 修改：接線（tracking、健康條切頁） |

---

### Task 0: Spec 修正

**Files:**
- Modify: `docs/superpowers/specs/2026-09-30-mlentry-level1-ui-redesign-design.md`

- [ ] **Step 1: 修改 spec**

套用上方「Spec 修正」三點：
- §1「後端」段 OK detail 句改為：`Universe {u} → 通過 Gate {q} → Top-K {r}`，並比對 Frozen OOF 候選數 p5–p95：落在區間內附「候選數在 OOF 常態範圍內」，否則附「候選數超出 OOF 常態範圍（p5–p95 a–b）」。
- §2.1 估算價段落：`實作為 app/mlentry/serving/ticks.py` 改為 `重用 app/research/level2/costs 的 round_down_tick／round_up_tick，函式 est_barrier_prices 置於 app/mlentry/serving/presentation.py`。
- §3.2 第 5 列改為 `候選數／日（Frozen 中位數 + p5–p95）`；收斂判定句「分布型指標比對 p10–p90」改為「p5–p95」。
- §3.3 整段改為：`新增唯讀腳本 scripts/mlentry_frozen_stats.py：讀 data/mlentry/<ds>/policy/policy_baseline_v1/metrics.json（B9 報告，已含 Lift@1/3/5、Median MFE/MAE、coverage、NO_TRADE 率、候選數分位），另以 OOF prediction_vector＋development outcomes 計算 ECE（成熟可評估列、p_target_10d vs target_hit_10d），寫入同目錄 frozen_stats.json。`（其餘兩項 bullet 不變）
- §5 檔案表：刪 `ticks.py` 列，新增 `backend/app/mlentry/serving/presentation.py`（est_barrier_prices、build_verdict）與 `backend/app/research/level2/costs.py`（公開取整別名）兩列。
- §7 測試：`ticks.py` 改為 `est_barrier_prices`。

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-09-30-mlentry-level1-ui-redesign-design.md
git commit -m "docs(mlentry): UI spec 修正——候選數帶改 p5–p95、Frozen 統計改讀 B9 metrics.json、估算價重用 costs 取整

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 1: 估算價（costs 公開取整＋est_barrier_prices）

**Files:**
- Modify: `backend/app/research/level2/costs.py`（在 `_round_up_tick` 之後）
- Create: `backend/app/mlentry/serving/presentation.py`
- Test: `backend/tests/test_mlentry_presentation.py`

**Interfaces:**
- Produces: `costs.round_down_tick(price: float) -> float`、`costs.round_up_tick(price: float) -> float`；`presentation.est_barrier_prices(close: float | None, target_pct: float = 0.10, stop_pct: float = 0.05) -> tuple[float | None, float | None]`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_mlentry_presentation.py
"""presentation：估算價（台股升降單位、保守側取整）與健康條判讀句。"""

from __future__ import annotations

import pytest

from app.mlentry.serving.presentation import est_barrier_prices


@pytest.mark.parametrize("close, target, stop", [
    (16.35, 17.95, 15.55),      # 17.985 → 向下 0.05；15.5325 → 向上 0.05
    (43.15, 47.45, 41.00),      # 47.465 → 47.45；40.9925 → 41.00
    (9.50, 10.45, 9.03),        # 10.45 落 10–50 區（0.05）；9.025 落 <10 區（0.01）向上
    (100.0, 110.0, 95.0),       # 110 落 100–500（0.5）；95 落 50–100（0.1）
    (1000.0, 1100.0, 950.0),    # 1100 落 ≥1000（5）；950 落 500–1000（1）
])
def test_est_barrier_prices_rounds_conservatively(close, target, stop):
    assert est_barrier_prices(close) == (target, stop)


@pytest.mark.parametrize("close", [None, 0.0, -1.0, float("nan")])
def test_est_barrier_prices_invalid_close(close):
    assert est_barrier_prices(close) == (None, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_presentation.py -v`
Expected: FAIL（`ModuleNotFoundError: app.mlentry.serving.presentation`）

- [ ] **Step 3: Write minimal implementation**

`backend/app/research/level2/costs.py`，在 `_round_up_tick` 定義之後加：

```python
def round_down_tick(price: float) -> float:
    """公開版：向下貼齊升降單位（UI 估算價等唯讀用途；與 _round_down_tick 同一實作）。"""
    return _round_down_tick(price)


def round_up_tick(price: float) -> float:
    """公開版：向上貼齊升降單位。"""
    return _round_up_tick(price)
```

```python
# backend/app/mlentry/serving/presentation.py
"""/app/level1 呈現層純函式：估算價、健康條判讀句。

只做讀取與措辭，不含任何判定門檻（門檻一律來自 config／Frozen 統計）。
"""

from __future__ import annotations

import math

from app.research.level2.costs import round_down_tick, round_up_tick


def est_barrier_prices(close: float | None, target_pct: float = 0.10, stop_pct: float = 0.05
                       ) -> tuple[float | None, float | None]:
    """以收盤估算 +10%／−5% 價位；目標向下、停損向上取整（兩者皆取保守側）。
    實際 barrier 從明日開盤起算，這裡只是跟單參考。"""
    if close is None or not math.isfinite(close) or close <= 0:
        return None, None
    return round_down_tick(close * (1 + target_pct)), round_up_tick(close * (1 - stop_pct))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_presentation.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/research/level2/costs.py backend/app/mlentry/serving/presentation.py backend/tests/test_mlentry_presentation.py
git commit -m "feat(mlentry): est_barrier_prices——收盤估算 +10%/−5% 價位，重用 costs 升降單位並取保守側

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 監控明細 `drifted_psi`

**Files:**
- Modify: `backend/app/mlentry/monitoring/health.py:58-86`（`feature_health_gate`）
- Test: `backend/tests/test_mlentry_serving.py`（檔尾新增）

**Interfaces:**
- Produces: `feature_health_gate(...).detail["drifted_psi"]: dict[str, {"psi": float, "thr": float}]`（僅 PSI 型漂移特徵，最多 20 個；日級超出範圍的特徵不在此字典）

- [ ] **Step 1: Write the failing test**

在 `backend/tests/test_mlentry_serving.py` 檔尾加：

```python
def test_feature_health_gate_records_drifted_psi():
    import numpy as np
    import pandas as pd
    from app.mlentry.monitoring.health import feature_health_gate

    rng = np.random.default_rng(0)
    ref_q = {str(q): float(v) for q, v in zip((0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9),
                                               np.quantile(rng.normal(0, 1, 5000), (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)))}
    feature_ref = {"f_ok": {"q": ref_q, "missing_rate": 0.0, "psi_p99": 0.1},
                   "f_shift": {"q": ref_q, "missing_rate": 0.0, "psi_p99": 0.1}}
    snap = pd.DataFrame({"f_ok": rng.normal(0, 1, 2000), "f_shift": rng.normal(3, 1, 2000)})
    cfg = {"psi_hard": 0.25, "max_features_drifted": 8, "max_missing_rate_shift": 0.2}
    res = feature_health_gate(snap, feature_ref, cfg)
    d = res.detail
    assert d["drifted"] == ["f_shift"]
    assert set(d["drifted_psi"]) == {"f_shift"}
    assert d["drifted_psi"]["f_shift"]["thr"] == 0.25
    assert d["drifted_psi"]["f_shift"]["psi"] > 0.25
    assert res.ok is True                      # 1 個漂移 ≤ 8：判定不變
```

先確認 `psi()` 接受的 `ref["q"]` 格式與上面一致：

Run: `.venv/Scripts/python.exe -c "import inspect,app.mlentry.monitoring.health as h;print(inspect.getsource(h.psi))"`
若 `q` 的鍵格式不同（例如 list 或不同 quantile 點），依實際格式調整測試裡的 `ref_q` 建構，斷言不變。

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_serving.py::test_feature_health_gate_records_drifted_psi -v`
Expected: FAIL（`KeyError: 'drifted_psi'`）

- [ ] **Step 3: Write minimal implementation**

`feature_health_gate` 內：在 `drifted, miss_shift, psis, out_of_range = [], [], {}, []` 同一行後加 `drifted_psi: dict[str, dict] = {}`；把

```python
        if np.isfinite(v) and v > thr:
            drifted.append(name)
```

改為

```python
        if np.isfinite(v) and v > thr:
            drifted.append(name)
            if len(drifted_psi) < 20:
                drifted_psi[name] = {"psi": round(float(v), 4), "thr": round(float(thr), 4)}
```

並把 `det = {...}` 加入 `"drifted_psi": drifted_psi`。

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_serving.py -v`
Expected: 全數 PASS（含新測試）

- [ ] **Step 5: Commit**

```bash
git add backend/app/mlentry/monitoring/health.py backend/tests/test_mlentry_serving.py
git commit -m "feat(mlentry): feature health detail 保存漂移特徵 PSI 與門檻（drifted_psi），判定不變

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 健康條判讀句 `build_verdict`

**Files:**
- Modify: `backend/app/mlentry/serving/presentation.py`
- Test: `backend/tests/test_mlentry_presentation.py`（新增測試）

**Interfaces:**
- Consumes: Task 2 的 `health["feature_health"]["drifted_psi"]`（可缺）
- Produces: `build_verdict(status: str, reason: str | None, reason_text: str | None, universe: int, qualified: int, recommended: int, health: dict, candidate_band: tuple[float, float] | None) -> dict`，回傳 `{"headline": str, "detail": str, "tone": "ok" | "quiet" | "fail"}`

- [ ] **Step 1: Write the failing tests**

在 `backend/tests/test_mlentry_presentation.py` 加：

```python
from app.mlentry.serving.presentation import build_verdict


def test_verdict_ok_within_band():
    v = build_verdict("OK", None, None, 1721, 6, 5, {}, (3.0, 28.6))
    assert v == {"headline": "正常出單 5 檔", "tone": "ok",
                 "detail": "Universe 1721 → 通過 Gate 6 → Top-K 5 ｜ 候選數在 OOF 常態範圍內"}


def test_verdict_ok_outside_band():
    v = build_verdict("OK", None, None, 1721, 40, 5, {}, (3.0, 28.6))
    assert v["detail"].endswith("候選數超出 OOF 常態範圍（p5–p95 3–28.6）")


def test_verdict_ok_without_band():
    v = build_verdict("OK", None, None, 1721, 6, 5, {}, None)
    assert v["detail"] == "Universe 1721 → 通過 Gate 6 → Top-K 5"


def test_verdict_market_no_trade():
    v = build_verdict("NO_TRADE", "POLICY_NO_CANDIDATE", "今日沒有股票同時通過 Alpha 與 Risk Gate（市場無機會，非系統故障）",
                      1700, 0, 0, {}, (3.0, 28.6))
    assert v["headline"] == "今日不出單：市場無機會" and v["tone"] == "quiet"
    assert "非系統故障" in v["detail"]


def test_verdict_feature_drift_with_psi():
    health = {"feature_health": {"ok": False, "n_drifted": 12, "drifted": ["a", "b", "c", "d"],
                                 "drifted_psi": {"a": {"psi": 0.41, "thr": 0.25}, "b": {"psi": 0.33, "thr": 0.25}}}}
    v = build_verdict("SYSTEM_NO_TRADE", "FEATURE_DRIFT", "特徵分布漂移超過門檻，系統依 fail-closed 不出單", 1721, 0, 0, health, None)
    assert v["headline"] == "系統暫停出單（fail-closed）" and v["tone"] == "fail"
    assert v["detail"] == "特徵漂移：12 個特徵超過門檻（a PSI 0.41>0.25、b PSI 0.33>0.25、c…）。系統依 fail-closed 不出單。"


def test_verdict_feature_drift_legacy_run_without_psi():
    health = {"feature_health": {"ok": False, "n_drifted": 2, "drifted": ["a", "b"]}}
    v = build_verdict("SYSTEM_NO_TRADE", "FEATURE_DRIFT", None, 1721, 0, 0, health, None)
    assert v["detail"] == "特徵漂移：2 個特徵超過門檻（a、b）。系統依 fail-closed 不出單。"


def test_verdict_other_system_reason_uses_text():
    v = build_verdict("SYSTEM_NO_TRADE", "DATA_HEALTH_FAIL", "資料品質未通過", 900, 0, 0, {}, None)
    assert v == {"headline": "系統暫停出單（fail-closed）", "detail": "資料品質未通過", "tone": "fail"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_presentation.py -v`
Expected: 新 7 個 FAIL（`ImportError: cannot import name 'build_verdict'`）

- [ ] **Step 3: Write minimal implementation**

在 `presentation.py` 檔尾加：

```python
def _fmt_band(x: float) -> str:
    return f"{x:g}"


def _drift_detail(fh: dict) -> str:
    names = list(fh.get("drifted", []))
    n = int(fh.get("n_drifted", len(names)))
    psi = fh.get("drifted_psi", {}) or {}
    parts = []
    for name in names[:3]:
        p = psi.get(name)
        parts.append(f"{name} PSI {p['psi']:.2f}>{p['thr']:.2f}" if p and p.get("psi") is not None else name)
    more = "…" if n > 3 else ""
    return f"特徵漂移：{n} 個特徵超過門檻（{'、'.join(parts)}{more}）。系統依 fail-closed 不出單。"


def build_verdict(status: str, reason: str | None, reason_text: str | None, universe: int, qualified: int,
                  recommended: int, health: dict, candidate_band: tuple[float, float] | None) -> dict:
    """健康條一句話判讀。tone：ok＝正常出單、quiet＝市場面不出單（非故障）、fail＝系統 fail-closed。"""
    if status == "SYSTEM_NO_TRADE":
        if reason == "FEATURE_DRIFT":
            detail = _drift_detail(health.get("feature_health", {}) or {})
        else:
            detail = reason_text or reason or ""
        return {"headline": "系統暫停出單（fail-closed）", "detail": detail, "tone": "fail"}
    if status != "OK" or recommended == 0:
        return {"headline": "今日不出單：市場無機會", "detail": reason_text or reason or "", "tone": "quiet"}
    detail = f"Universe {universe} → 通過 Gate {qualified} → Top-K {recommended}"
    if candidate_band is not None:
        lo, hi = candidate_band
        if lo <= qualified <= hi:
            detail += " ｜ 候選數在 OOF 常態範圍內"
        else:
            detail += f" ｜ 候選數超出 OOF 常態範圍（p5–p95 {_fmt_band(lo)}–{_fmt_band(hi)}）"
    return {"headline": f"正常出單 {recommended} 檔", "detail": detail, "tone": "ok"}
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_presentation.py -v`
Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/mlentry/serving/presentation.py backend/tests/test_mlentry_presentation.py
git commit -m "feat(mlentry): build_verdict——健康條判讀句（正常出單／市場無機會／fail-closed＋漂移特徵 PSI）

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 追蹤路徑 `track_paths`（純函式，重用 run_barriers）

**Files:**
- Create: `backend/app/mlentry/serving/tracking.py`
- Test: `backend/tests/test_mlentry_tracking.py`

**Interfaces:**
- Consumes: `app.mlentry.labels.barriers.run_barriers(m, cfg)`、`EntryStatus`、`Event`；`app.mlentry.config.LabelConfig`
- Produces: `track_paths(m: dict[str, pd.DataFrame], items: list[tuple[str, str]], cfg: LabelConfig, ledger: dict[tuple[str, str], int] | None = None) -> list[dict]`；每列 `{"signal_date": str, "stock_id": str, "day_index": int, "horizon": int, "status": str, "hit_day": int | None, "ret_now": float | None, "mfe": float | None, "mae": float | None}`；`status ∈ {"PENDING_ENTRY","LIVE","TARGET","STOP","STOP_AMBIGUOUS","TIMEOUT","NOT_ENTERED","DATA_MISSING"}`。`summarize(rows) -> dict`：`{"n","target","stop","timeout","live","pending"}`（`stop` 含 STOP_AMBIGUOUS；`pending` = PENDING_ENTRY）。

設計重點：`m` 的列為交易日曆（字串日期、升冪），第一列須 ≤ 最早 signal_date。在尾端補 `max_horizon` 列 NaN，使每個 signal 在 barrier 引擎內都「成熟」，便可直接讀 first-hit／MFE／MAE／return_10d；再依 `day_index` 決定未觸者是 `LIVE` 還是 `TIMEOUT`。

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_mlentry_tracking.py
"""追蹤中：以 barrier 引擎計算近 10 日推薦的即時路徑；與成熟引擎語意一致。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.mlentry.config import LabelConfig
from app.mlentry.labels.barriers import run_barriers
from app.mlentry.serving.tracking import summarize, track_paths

CFG = LabelConfig()


def _mats(paths: dict[str, list[tuple[float, float, float, float]]], n_days: int) -> dict[str, pd.DataFrame]:
    """paths：stock → [(open, high, low, close), ...] 從第 0 列（signal_date）起；不足列補 NaN。"""
    idx = pd.Index([f"2026-09-{d:02d}" for d in range(1, n_days + 1)], name="date")
    out = {}
    for j, col in enumerate(("open", "high", "low", "close")):
        data = {}
        for sid, rows in paths.items():
            vals = [r[j] for r in rows] + [np.nan] * (n_days - len(rows))
            data[sid] = vals
        out[col] = pd.DataFrame(data, index=idx, dtype=float)
    return out


FLAT = (100.0, 101.0, 99.0, 100.0)


def test_pending_entry_on_last_day():
    m = _mats({"A": [FLAT]}, 1)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    assert r["status"] == "PENDING_ENTRY" and r["day_index"] == 0 and r["ret_now"] is None


def test_live_path_reports_ret_mfe_mae():
    # 第 0 列 signal；第 1 列開 100 進場，高 104 低 98 收 103；第 2 列高 105 低 101 收 102
    m = _mats({"A": [FLAT, (100, 104, 98, 103), (102, 105, 101, 102)]}, 3)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    assert r["status"] == "LIVE" and r["day_index"] == 2 and r["hit_day"] is None
    assert r["ret_now"] == pytest.approx(0.02, abs=1e-6)
    assert r["mfe"] == pytest.approx(0.05, abs=1e-6)
    assert r["mae"] == pytest.approx(-0.02, abs=1e-6)


def test_target_hit_day():
    m = _mats({"A": [FLAT, (100, 103, 99, 102), (102, 111, 101, 110)]}, 3)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    assert r["status"] == "TARGET" and r["hit_day"] == 2


def test_stop_hit_day():
    m = _mats({"A": [FLAT, (100, 101, 94.9, 95)]}, 2)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    assert r["status"] == "STOP" and r["hit_day"] == 1


def test_same_day_double_touch_is_stop_ambiguous():
    m = _mats({"A": [FLAT, (100, 110.5, 94.0, 100)]}, 2)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    assert r["status"] == "STOP_AMBIGUOUS" and r["hit_day"] == 1


def test_timeout_after_ten_days():
    rows = [FLAT] + [(100, 102, 98, 100)] * 10
    m = _mats({"A": rows}, 11)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    assert r["status"] == "TIMEOUT" and r["day_index"] == 10


def test_limit_up_open_is_not_entered():
    # 前收 100 → 漲停 110；隔日開在 110 → PRICE_LIMIT_CONSTRAINT
    m = _mats({"A": [FLAT, (110, 110, 110, 110)]}, 2)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    assert r["status"] == "NOT_ENTERED" and r["ret_now"] is None


def test_missing_stock_is_data_missing():
    m = _mats({"A": [FLAT, FLAT]}, 2)
    (r,) = track_paths(m, [("2026-09-01", "ZZZZ")], CFG)
    assert r["status"] == "DATA_MISSING"


def test_ledger_event_overrides():
    m = _mats({"A": [FLAT, (100, 101, 99, 100)]}, 2)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG, ledger={("2026-09-01", "A"): 4})
    assert r["status"] == "TIMEOUT"


def test_parity_with_matured_engine():
    """完整 10 日路徑：track_paths 的 status／hit_day／mfe 必須與 run_barriers 直接跑的成熟結果一致。"""
    rng = np.random.default_rng(1)
    rows = [FLAT]
    px = 100.0
    for _ in range(10):
        o = px * (1 + rng.normal(0, 0.01)); c = o * (1 + rng.normal(0, 0.03))
        rows.append((o, max(o, c) * 1.01, min(o, c) * 0.99, c)); px = c
    rows.append(FLAT)                                      # 第 11 列讓第 0 列在原生引擎中成熟
    m = _mats({"A": rows}, 12)
    direct = run_barriers(m, CFG)
    (r,) = track_paths(m, [("2026-09-01", "A")], CFG)
    ev = int(direct["event_type"].iloc[0, 0])
    expect = {1: "TARGET", 2: "STOP", 3: "STOP_AMBIGUOUS", 4: "TIMEOUT"}[ev]
    assert r["status"] == expect
    assert r["mfe"] == pytest.approx(float(direct["mfe_10d"].iloc[0, 0]), abs=1e-6)
    assert r["mae"] == pytest.approx(float(direct["mae_10d"].iloc[0, 0]), abs=1e-6)


def test_summarize_counts():
    rows = [{"status": s} for s in ("TARGET", "STOP", "STOP_AMBIGUOUS", "LIVE", "LIVE", "PENDING_ENTRY", "TIMEOUT", "NOT_ENTERED")]
    assert summarize(rows) == {"n": 8, "target": 1, "stop": 2, "timeout": 1, "live": 2, "pending": 1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_tracking.py -v`
Expected: FAIL（`ModuleNotFoundError: app.mlentry.serving.tracking`）

- [ ] **Step 3: Write implementation**

```python
# backend/app/mlentry/serving/tracking.py
"""追蹤中（/app/level1 榜單頁）：近 N 個交易日推薦的即時路徑。

語意唯一來源：labels.barriers.run_barriers。作法：在價格矩陣尾端補 max_horizon 列 NaN，讓每個 signal
在引擎內都「成熟」，即可讀 first-hit／MFE／MAE／return_10d；未觸者依已走天數判定 LIVE 或 TIMEOUT。
已成熟且 ledger 有 event_type 者以 ledger 為準（與體檢頁同一份事實）。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..config import LabelConfig
from ..labels.barriers import EntryStatus, Event, run_barriers

_EVENT_STATUS = {int(Event.TARGET): "TARGET", int(Event.STOP): "STOP",
                 int(Event.STOP_AMBIGUOUS): "STOP_AMBIGUOUS", int(Event.TIMEOUT): "TIMEOUT",
                 int(Event.NOT_ENTERED): "NOT_ENTERED"}


def _f(x) -> float | None:
    x = float(x)
    return None if math.isnan(x) else x


def track_paths(m: dict[str, pd.DataFrame], items: list[tuple[str, str]], cfg: LabelConfig,
                ledger: dict[tuple[str, str], int] | None = None) -> list[dict]:
    K = cfg.max_horizon
    close = m["close"]
    dates = [str(d) for d in close.index]
    pos = {d: i for i, d in enumerate(dates)}
    last = len(dates) - 1
    pad = pd.Index([f"~pad{i:02d}" for i in range(K)], name=close.index.name)
    padded = {k: pd.concat([m[k].set_axis(dates, axis=0),
                            pd.DataFrame(np.nan, index=pad, columns=m[k].columns)])
              for k in ("open", "high", "low", "close")}
    out = run_barriers(padded, cfg)
    ledger = ledger or {}
    rows = []
    for sd, sid in items:
        base = {"signal_date": sd, "stock_id": sid, "horizon": K, "hit_day": None,
                "ret_now": None, "mfe": None, "mae": None}
        if sd not in pos or sid not in close.columns:
            rows.append({**base, "day_index": 0, "status": "DATA_MISSING"}); continue
        day_index = min(last - pos[sd], K)
        if day_index == 0:
            rows.append({**base, "day_index": 0, "status": "PENDING_ENTRY"}); continue
        st = int(out["entry_status"].at[sd, sid])
        if st != int(EntryStatus.FILLED):
            status = "NOT_ENTERED" if st == int(EntryStatus.PRICE_LIMIT_CONSTRAINT) else "DATA_MISSING"
            rows.append({**base, "day_index": day_index, "status": status}); continue
        ev = int(out["event_type"].at[sd, sid])
        tday = _f(out["target_first_hit_day"].at[sd, sid]); sday = _f(out["stop_first_hit_day"].at[sd, sid])
        if ev == int(Event.TARGET):
            status, hit = "TARGET", tday
        elif ev == int(Event.STOP):
            status, hit = "STOP", sday
        elif ev == int(Event.STOP_AMBIGUOUS):
            status, hit = "STOP_AMBIGUOUS", tday
        else:
            status, hit = ("TIMEOUT" if day_index >= K else "LIVE"), None
        led = ledger.get((sd, sid))
        if led is not None and led in _EVENT_STATUS:
            status = _EVENT_STATUS[led]
        rows.append({**base, "day_index": day_index, "status": status,
                     "hit_day": int(hit) if hit is not None else None,
                     "ret_now": _f(out[f"return_{K}d"].at[sd, sid]),
                     "mfe": _f(out[f"mfe_{K}d"].at[sd, sid]), "mae": _f(out[f"mae_{K}d"].at[sd, sid])})
    return rows


def summarize(rows: list[dict]) -> dict:
    s = [r["status"] for r in rows]
    return {"n": len(s), "target": s.count("TARGET"), "stop": s.count("STOP") + s.count("STOP_AMBIGUOUS"),
            "timeout": s.count("TIMEOUT"), "live": s.count("LIVE"), "pending": s.count("PENDING_ENTRY")}
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_tracking.py -v`
Expected: 11 passed。若 `test_limit_up_open_is_not_entered` 失敗，檢查 `limits_from_prev_close` 對 prev=100 的漲停價（應 110.0）與 `limit_tolerance`；不得改引擎，只可調整測試路徑數值使其確實觸及漲停開盤。

- [ ] **Step 5: Commit**

```bash
git add backend/app/mlentry/serving/tracking.py backend/tests/test_mlentry_tracking.py
git commit -m "feat(mlentry): track_paths——追蹤中即時路徑（補 NaN 讓 run_barriers 成熟、ledger 優先、parity 測試）

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 追蹤資料載入 `load_tracking`

**Files:**
- Modify: `backend/app/mlentry/serving/tracking.py`（檔尾）
- Test: `backend/tests/test_mlentry_tracking.py`（新增 DB 測試）

**Interfaces:**
- Consumes: `track_paths`、`summarize`（Task 4）；`app.mlentry.data.calendar.load_calendar(con)`、`TradingCalendar`；`app.mlentry.data.prices.load_matrices(con, cal, columns)`；`models.MLEntryRun`、`models.MLEntryPrediction`、`models.Stock`
- Produces: `load_tracking(con, session, days: int = 10, cfg: LabelConfig | None = None) -> dict`，回傳 `{"as_of": str | None, "summary": dict, "items": list[dict]}`；每個 item 為 `track_paths` 列再加 `"name": str | None`，排序 signal_date 新→舊、同日依 rank。

- [ ] **Step 1: Write the failing test**

在 `test_mlentry_tracking.py` 加（使用真實 DB 與日曆；只驗結構與排序，不依賴特定行情）：

```python
def test_load_tracking_shape_on_real_db():
    import sqlite3
    from app.config import get_settings
    from app.mlentry.serving.tracking import load_tracking
    from app.storage.database import init_db, session_scope

    init_db()
    con = sqlite3.connect(str(get_settings().db_path))
    try:
        with session_scope() as s:
            res = load_tracking(con, s, days=10)
    finally:
        con.close()
    assert set(res) == {"as_of", "summary", "items"}
    assert set(res["summary"]) == {"n", "target", "stop", "timeout", "live", "pending"}
    assert res["summary"]["n"] == len(res["items"])
    dates = [it["signal_date"] for it in res["items"]]
    assert dates == sorted(dates, reverse=True)
    for it in res["items"]:
        assert {"signal_date", "stock_id", "name", "day_index", "status", "ret_now", "mfe", "mae", "hit_day"} <= set(it)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_tracking.py::test_load_tracking_shape_on_real_db -v`
Expected: FAIL（`ImportError: cannot import name 'load_tracking'`）

- [ ] **Step 3: Write implementation**

在 `tracking.py` 頂部 import 區加：

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.storage import models

from ..config import load_config
from ..data import prices
from ..data.calendar import TradingCalendar, load_calendar
```

檔尾加：

```python
def load_tracking(con, session: Session, days: int = 10, cfg: LabelConfig | None = None) -> dict:
    cfg = cfg or load_config().labels
    cal = load_calendar(con)
    if len(cal) == 0:
        return {"as_of": None, "summary": summarize([]), "items": []}
    window = [str(d) for d in cal.dates[-days:]]
    R, P = models.MLEntryRun, models.MLEntryPrediction
    runs = session.execute(select(R.run_id, R.signal_date).where(R.signal_date >= pd.Timestamp(window[0]).date())
                           .order_by(R.signal_date, R.run_id)).all()
    latest: dict[str, str] = {}
    for run_id, sd in runs:                                   # 同日多 run 取 run_id 最大者
        latest[str(sd)] = run_id
    if not latest:
        return {"as_of": window[-1], "summary": summarize([]), "items": []}
    preds = session.execute(
        select(P.run_id, P.signal_date, P.stock_id, P.rank, P.event_type, P.matured_at, models.Stock.name)
        .outerjoin(models.Stock, models.Stock.id == P.stock_id)
        .where(P.run_id.in_(list(latest.values())), P.recommended.is_(True))
    ).all()
    if not preds:
        return {"as_of": window[-1], "summary": summarize([]), "items": []}
    items = [(str(p.signal_date), str(p.stock_id)) for p in preds]
    ledger = {(str(p.signal_date), str(p.stock_id)): int(p.event_type)
              for p in preds if p.matured_at is not None and p.event_type is not None}
    start = min(sd for sd, _ in items)
    sub = TradingCalendar(cal.dates[cal.pos(start):])
    cols = pd.Index(sorted({sid for _, sid in items}), name="stock_id")
    m = prices.load_matrices(con, sub, cols)
    rows = track_paths(m, items, cfg, ledger)
    meta = {(str(p.signal_date), str(p.stock_id)): (p.name, p.rank) for p in preds}
    for r in rows:
        r["name"], rank = meta[(r["signal_date"], r["stock_id"])]
        r["_rank"] = rank if rank is not None else 999
    rows.sort(key=lambda r: (r["signal_date"], -r["_rank"]), reverse=True)
    for r in rows:
        r.pop("_rank")
    return {"as_of": window[-1], "summary": summarize(rows), "items": rows}
```

注意：`cal.pos(start)` 的參數型別需與 `TradingCalendar.pos` 一致（字串日期）；若 `pos` 對不在日曆的日期拋錯，`start` 一定來自 run 的 signal_date（皆為交易日），不需額外處理。

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_tracking.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/mlentry/serving/tracking.py backend/tests/test_mlentry_tracking.py
git commit -m "feat(mlentry): load_tracking——近 N 交易日推薦（每日最後 run）載入日線並計算追蹤列

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Live 指標擴充＋收斂判定＋逐日成熟

**Files:**
- Modify: `backend/app/mlentry/monitoring/performance.py`
- Test: `backend/tests/test_mlentry_performance_ui.py`

**Interfaces:**
- Produces:
  - `load_matured` 回傳欄位新增 `qualified_count`（run 層）。
  - `rolling_live_metrics(df, windows, k, cost_rt)` 每個 window item 新增：`"lift": {"1": float|None, "3": float|None, "5": float|None}`、`"coverage": float`、`"candidates_median": float`、`"no_trade_rate": float`（既有鍵不變）。
  - `daily_matured(df, k: int = 5, cost_rt: float = 0.00585, limit: int = 60) -> list[dict]`：`{"signal_date": str, "n_rec": int, "target": int, "stop": int, "timeout": int, "lift": float|None, "net10": float|None}`，新→舊。
  - `convergence(frozen: dict, live: dict, windows: tuple[int, ...] = (20, 60)) -> list[dict]`：每列 `{"key","label","fmt","frozen","band","band_kind","live": {"20": v, "60": v},"verdict": {"20": str, "60": str}}`；`fmt ∈ {"x","ratio","pct","num","num3"}`；`band_kind ∈ {"ci","dist",None}`；verdict ∈ `{"CI 內","CI 外","分布內","分布外","參考","累積中"}`。
  - `frozen` 參數的鍵（由 routes 合併 champion `frozen_validation` 與 `frozen_stats.json` 而來）：`target_lift_at_5, ci_target_lift, stop_ratio_at_5, ci_stop_ratio, mean_net10, ci_net10, coverage, lift_at_1, lift_at_3, candidates_median, candidates_p05, candidates_p95, no_trade_rate, ece_target_10d, median_mfe_10d, median_mae_10d`（缺鍵視為 None）。

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_mlentry_performance_ui.py
"""體檢頁：Live 指標擴充、收斂判定、逐日成熟紀錄。"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from app.mlentry.monitoring.performance import convergence, daily_matured, rolling_live_metrics


def _df(n_days: int) -> pd.DataFrame:
    """每日 10 檔：rank 1–5 推薦；rank1 必中 target、rank2 必中 stop；其餘未推薦列全 0。"""
    rows = []
    d0 = date(2026, 1, 1)
    for i in range(n_days):
        sd = d0 + timedelta(days=i)
        for j in range(10):
            rec = j < 5
            rows.append({"run_id": f"r{i}", "signal_date": sd, "stock_id": f"S{j}", "recommended": rec,
                         "rank": j + 1 if rec else None, "p_target_10d": 0.2, "p_stop_10d": 0.3,
                         "target_hit_10d": 1 if j in (0, 5) else 0, "stop_hit_10d": 1 if j in (1, 6, 7) else 0,
                         "return_10d": 0.05 if j == 0 else 0.0, "mfe_10d": 0.1, "mae_10d": -0.02,
                         "event_type": 1 if j in (0, 5) else (2 if j in (1, 6, 7) else 4),
                         "status": "OK", "no_trade": False, "qualified_count": 8})
    return pd.DataFrame(rows)


def test_live_lift_at_k():
    out = rolling_live_metrics(_df(20), windows=(20,), k=5)
    w = out["windows"]["20"]
    # 市場 target 基率 = 2/10；rank≤1 命中率 1 → lift 5；rank≤3 命中率 1/3 → 1.667；rank≤5 → 1/5 → 1.0
    assert w["lift"]["1"] == pytest.approx(5.0)
    assert w["lift"]["3"] == pytest.approx(5 / 3)
    assert w["lift"]["5"] == pytest.approx(1.0)
    assert w["coverage"] == 1.0 and w["candidates_median"] == 8 and w["no_trade_rate"] == 0.0


def test_daily_matured_newest_first():
    days = daily_matured(_df(3), k=5)
    assert [d["signal_date"] for d in days] == ["2026-01-03", "2026-01-02", "2026-01-01"]
    assert days[0] == {"signal_date": "2026-01-03", "n_rec": 5, "target": 1, "stop": 1, "timeout": 3,
                       "lift": pytest.approx(1.0), "net10": pytest.approx(0.01 - 0.00585)}


FROZEN = {"target_lift_at_5": 1.23, "ci_target_lift": [1.07, 1.40], "stop_ratio_at_5": 0.75, "ci_stop_ratio": [0.68, 0.81],
          "mean_net10": 0.0066, "ci_net10": [-0.003, 0.0154], "coverage": 0.992, "lift_at_1": 1.4, "lift_at_3": 1.3,
          "candidates_median": 9.5, "candidates_p05": 3.0, "candidates_p95": 28.6, "no_trade_rate": 0.008,
          "ece_target_10d": 0.012, "median_mfe_10d": 0.034, "median_mae_10d": -0.027}


def _row(rows, key):
    return next(r for r in rows if r["key"] == key)


def test_convergence_accumulating_when_short():
    live = {"matured_days": 5, "windows": {"20": {"days": 5, "lift": {"5": 1.2}}}}
    rows = convergence(FROZEN, live)
    r = _row(rows, "lift_at_5")
    assert r["verdict"] == {"20": "累積中", "60": "累積中"} and r["live"]["20"] == 1.2 and r["live"]["60"] is None


def test_convergence_ci_and_dist():
    live = {"matured_days": 20, "windows": {"20": {"days": 20, "lift": {"1": 2.0, "3": 1.5, "5": 1.15}, "stop_ratio": 0.92,
                                                   "mean_net10": 0.002, "coverage": 0.95, "candidates_median": 40.0,
                                                   "no_trade_rate": 0.05, "ece_target_10d": 0.03,
                                                   "median_mfe": 0.03, "median_mae": -0.03}}}
    rows = convergence(FROZEN, live)
    assert [r["key"] for r in rows] == ["lift_at_1", "lift_at_3", "lift_at_5", "stop_ratio_at_5", "net10", "coverage",
                                        "candidates_per_day", "no_trade_rate", "ece_target_10d", "median_mfe_10d", "median_mae_10d"]
    assert _row(rows, "lift_at_5")["verdict"]["20"] == "CI 內"
    assert _row(rows, "stop_ratio_at_5")["verdict"]["20"] == "CI 外"
    assert _row(rows, "net10")["verdict"]["20"] == "CI 內"
    assert _row(rows, "candidates_per_day")["verdict"]["20"] == "分布外"
    assert _row(rows, "candidates_per_day")["band"] == [3.0, 28.6] and _row(rows, "candidates_per_day")["band_kind"] == "dist"
    assert _row(rows, "coverage")["verdict"]["20"] == "參考"
    assert _row(rows, "lift_at_5")["verdict"]["60"] == "累積中"


def test_convergence_missing_frozen_stats_is_none():
    rows = convergence({"target_lift_at_5": 1.23, "ci_target_lift": [1.07, 1.40]}, {"matured_days": 0, "windows": {}})
    assert _row(rows, "lift_at_1")["frozen"] is None
    assert _row(rows, "lift_at_5")["frozen"] == 1.23
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_performance_ui.py -v`
Expected: FAIL（`ImportError: cannot import name 'convergence'`）

- [ ] **Step 3: Write implementation**

`load_matured`：select 加 `R.qualified_count`，`cols` 末尾加 `"qualified_count"`。

`rolling_live_metrics` 迴圈內，在 `item = {...}` 之後、`if len(rec):` 之前加：

```python
        live_day = d[d["status"] != "SYSTEM_NO_TRADE"]
        item["lift"] = {}
        for kk in (1, 3, 5):
            rk = live_day[live_day["recommended"] & (live_day["rank"] <= kk)]
            item["lift"][str(kk)] = float(rk["target_hit_10d"].mean() / base_t) if len(rk) and base_t > 0 else None
        per_day = d.groupby("signal_date").agg(n_rec=("recommended", "sum"), q=("qualified_count", "first"),
                                                nt=("no_trade", "first"), st=("status", "first"))
        item["coverage"] = float(((per_day["n_rec"] > 0) & (per_day["st"] != "SYSTEM_NO_TRADE")).mean())
        item["candidates_median"] = float(per_day["q"].median())
        item["no_trade_rate"] = float(per_day["nt"].astype(bool).mean())
```

檔尾新增：

```python
def daily_matured(df: pd.DataFrame, k: int = 5, cost_rt: float = 0.00585, limit: int = 60) -> list[dict]:
    if df.empty:
        return []
    out = []
    for sd, d in sorted(df.groupby("signal_date"), key=lambda x: x[0], reverse=True)[:limit]:
        rec = d[d["recommended"] & (d["rank"] <= k) & (d["status"] != "SYSTEM_NO_TRADE")]
        base = d["target_hit_10d"].mean()
        out.append({"signal_date": pd.Timestamp(sd).date().isoformat(), "n_rec": int(len(rec)),
                    "target": int((rec["event_type"] == 1).sum()), "stop": int(rec["event_type"].isin([2, 3]).sum()),
                    "timeout": int((rec["event_type"] == 4).sum()),
                    "lift": float(rec["target_hit_10d"].mean() / base) if len(rec) and base > 0 else None,
                    "net10": float((rec["return_10d"] - cost_rt).mean()) if len(rec) else None})
    return out


# (key, label, fmt, frozen 值鍵, band 鍵（ci）或 (lo, hi) 鍵（dist）, live window 取值函式)
_CONV_ROWS = (
    ("lift_at_1", "Lift@1", "x", "lift_at_1", None, lambda w: w.get("lift", {}).get("1")),
    ("lift_at_3", "Lift@3", "x", "lift_at_3", None, lambda w: w.get("lift", {}).get("3")),
    ("lift_at_5", "Lift@5", "x", "target_lift_at_5", "ci_target_lift", lambda w: w.get("lift", {}).get("5")),
    ("stop_ratio_at_5", "StopRatio@5", "ratio", "stop_ratio_at_5", "ci_stop_ratio", lambda w: w.get("stop_ratio")),
    ("net10", "Net10／筆", "pct", "mean_net10", "ci_net10", lambda w: w.get("mean_net10")),
    ("coverage", "Coverage", "pct", "coverage", None, lambda w: w.get("coverage")),
    ("candidates_per_day", "候選數／日", "num", "candidates_median", ("candidates_p05", "candidates_p95"),
     lambda w: w.get("candidates_median")),
    ("no_trade_rate", "NO_TRADE 率", "pct", "no_trade_rate", None, lambda w: w.get("no_trade_rate")),
    ("ece_target_10d", "ECE Target10", "num3", "ece_target_10d", None, lambda w: w.get("ece_target_10d")),
    ("median_mfe_10d", "Median MFE 10D", "pct", "median_mfe_10d", None, lambda w: w.get("median_mfe")),
    ("median_mae_10d", "Median MAE 10D", "pct", "median_mae_10d", None, lambda w: w.get("median_mae")),
)


def _judge(v, days: int, w: int, band, kind) -> str:
    if v is None or days < w:
        return "累積中"
    if band is None:
        return "參考"
    inside = band[0] <= v <= band[1]
    if kind == "ci":
        return "CI 內" if inside else "CI 外"
    return "分布內" if inside else "分布外"


def convergence(frozen: dict, live: dict, windows: tuple[int, ...] = (20, 60)) -> list[dict]:
    """Frozen OOF vs Live 的描述性比對（附錄 C：20 成熟日只看不決策、60 為判斷點）。不寫入任何狀態。"""
    rows = []
    for key, label, fmt, fkey, bkey, live_fn in _CONV_ROWS:
        if isinstance(bkey, tuple):
            lo, hi = frozen.get(bkey[0]), frozen.get(bkey[1])
            band, kind = ([float(lo), float(hi)] if lo is not None and hi is not None else None), "dist"
        elif bkey is not None:
            ci = frozen.get(bkey)
            band, kind = ([float(ci[0]), float(ci[1])] if isinstance(ci, (list, tuple)) and len(ci) == 2 else None), "ci"
        else:
            band, kind = None, None
        fv = frozen.get(fkey)
        lv, vd = {}, {}
        for w in windows:
            item = live.get("windows", {}).get(str(w))
            v = live_fn(item) if item else None
            lv[str(w)] = float(v) if v is not None else None
            vd[str(w)] = _judge(lv[str(w)], int(item["days"]) if item else 0, w, band, kind)
        rows.append({"key": key, "label": label, "fmt": fmt, "frozen": float(fv) if fv is not None else None,
                     "band": band, "band_kind": kind if band is not None else None, "live": lv, "verdict": vd})
    return rows
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_performance_ui.py tests/test_mlentry_serving.py tests/test_mlentry_api.py -v`
Expected: 全數 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/mlentry/monitoring/performance.py backend/tests/test_mlentry_performance_ui.py
git commit -m "feat(mlentry): live 指標加 Lift@1/3・coverage・候選數・NO_TRADE 率；convergence 描述性比對；daily_matured

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Frozen 描述統計轉存腳本

**Files:**
- Create: `backend/scripts/mlentry_frozen_stats.py`
- Test: `backend/tests/test_mlentry_performance_ui.py`（新增）

**Interfaces:**
- Consumes: B9 `data/mlentry/<ds>/policy/<policy>/metrics.json`（結構：`at_k["1"|"3"|"5"]["point"]["policy"]` 含 `target_lift`、`median_mfe`、`median_mae`；`sensitivity` 含 `coverage`、`no_trade_pct`、`median_candidates_per_day`、`qualified_count_quantiles["0.05"|"0.95"]`；頂層 `policy`）；`scripts.mlentry_policy_report.load_frame(ds_dir)`；`app.mlentry.evaluation.model_metrics.ece(y, p)`
- Produces: `build_frozen_stats(metrics: dict, ece_value: float | None, expected_policy: str) -> dict`（純函式，policy 不符拋 `ValueError`）；檔案 `frozen_stats.json`，鍵：`policy_name, dataset_version, generated_at, lift_at_1, lift_at_3, candidates_median, candidates_p05, candidates_p95, no_trade_rate, ece_target_10d, median_mfe_10d, median_mae_10d`

- [ ] **Step 1: Write the failing test**

```python
def test_build_frozen_stats_maps_b9_metrics():
    from scripts.mlentry_frozen_stats import build_frozen_stats
    metrics = {"policy": "policy_baseline_v1", "dataset_version": "ds_x",
               "at_k": {"1": {"point": {"policy": {"target_lift": 1.4}}}, "3": {"point": {"policy": {"target_lift": 1.3}}},
                        "5": {"point": {"policy": {"target_lift": 1.23, "median_mfe": 0.034, "median_mae": -0.027}}}},
               "sensitivity": {"coverage": 0.99, "no_trade_pct": 0.008, "median_candidates_per_day": 9.5,
                               "qualified_count_quantiles": {"0.05": 3.0, "0.95": 28.6}}}
    out = build_frozen_stats(metrics, 0.012, "policy_baseline_v1")
    assert out["lift_at_1"] == 1.4 and out["lift_at_3"] == 1.3
    assert (out["candidates_median"], out["candidates_p05"], out["candidates_p95"]) == (9.5, 3.0, 28.6)
    assert out["no_trade_rate"] == 0.008 and out["ece_target_10d"] == 0.012
    assert out["median_mfe_10d"] == 0.034 and out["median_mae_10d"] == -0.027
    assert out["policy_name"] == "policy_baseline_v1" and out["dataset_version"] == "ds_x"
    with pytest.raises(ValueError):
        build_frozen_stats(metrics, 0.012, "policy_other")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_performance_ui.py::test_build_frozen_stats_maps_b9_metrics -v`
Expected: FAIL（`ModuleNotFoundError: scripts.mlentry_frozen_stats`）

- [ ] **Step 3: Write implementation**

```python
# backend/scripts/mlentry_frozen_stats.py
"""Frozen 描述統計轉存（/app/level1 體檢頁收斂對照表用）。唯讀：不重跑 OOF、不重算 policy、不寫回 champion。

    python -m scripts.mlentry_frozen_stats [--dataset DIR]

來源：data/mlentry/<ds>/policy/<policy>/metrics.json（B9 報告）＋ OOF 成熟可評估列的 ECE（p_target_10d vs target_hit_10d）。
輸出：同目錄 frozen_stats.json。policy 必須與 champion 相同。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.mlentry.datasets import api  # noqa: E402
from app.mlentry.evaluation.model_metrics import ece  # noqa: E402
from app.mlentry.registry.versions import load_champion  # noqa: E402


def build_frozen_stats(metrics: dict, ece_value: float | None, expected_policy: str) -> dict:
    if metrics.get("policy") != expected_policy:
        raise ValueError(f"metrics.json policy={metrics.get('policy')} != champion policy={expected_policy}")
    at = metrics["at_k"]; sens = metrics["sensitivity"]; q = sens.get("qualified_count_quantiles", {})
    p5 = at["5"]["point"]["policy"]
    return {"policy_name": metrics["policy"], "dataset_version": metrics.get("dataset_version"),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "lift_at_1": at["1"]["point"]["policy"]["target_lift"], "lift_at_3": at["3"]["point"]["policy"]["target_lift"],
            "candidates_median": sens["median_candidates_per_day"], "candidates_p05": q.get("0.05"), "candidates_p95": q.get("0.95"),
            "no_trade_rate": sens["no_trade_pct"], "ece_target_10d": ece_value,
            "median_mfe_10d": p5.get("median_mfe"), "median_mae_10d": p5.get("median_mae")}


def _oof_ece(ds_dir: Path) -> float:
    from scripts.mlentry_policy_report import load_frame
    df = load_frame(ds_dir)
    ev = df[df["target"].notna() & (df["matured"] == 1)]
    return float(ece(ev["target"].to_numpy(dtype=float), ev["p_target_10d"].to_numpy(dtype=float)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--dataset", default=None)
    args = ap.parse_args(argv)
    champ = load_champion()
    if champ is None:
        print("no champion; abort"); return 1
    ds_dir = Path(args.dataset) if args.dataset else api.latest_dataset_dir()
    pdir = ds_dir / "policy" / champ.policy_name
    metrics = json.loads((pdir / "metrics.json").read_text(encoding="utf-8"))
    out = build_frozen_stats(metrics, _oof_ece(ds_dir), champ.policy_name)
    (pdir / "frozen_stats.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test, then run the script on real data**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_performance_ui.py -v`
Expected: 全數 PASS

Run: `.venv/Scripts/python.exe -m scripts.mlentry_frozen_stats`
Expected: 印出 JSON，`lift_at_1`／`lift_at_3` 為數值、`candidates_p05`=3.0、`candidates_p95`=28.6、`ece_target_10d` 為 0～0.1 之間的數。同時確認 champion 的 `dataset_version` 與 `api.latest_dataset_dir()` 同名（`ds_2026-09-29_6613b41a35737d0ca763aaa2`）；不同名則改用 `--dataset data/mlentry/<champion dataset_version>`。`frozen_stats.json` 位於 gitignored 的 `backend/data/mlentry/`，不 commit。

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/mlentry_frozen_stats.py backend/tests/test_mlentry_performance_ui.py
git commit -m "feat(mlentry): mlentry_frozen_stats——B9 metrics.json＋OOF ECE 轉存 frozen_stats.json（唯讀，policy 對齊 champion）

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: API 組裝（board／status／health／tracking）

**Files:**
- Modify: `backend/app/api/routes_mlentry.py`
- Test: `backend/tests/test_mlentry_api.py`

**Interfaces:**
- Consumes: `est_barrier_prices`、`build_verdict`（Task 1、3）；`load_tracking`（Task 5）；`rolling_live_metrics`、`daily_matured`、`convergence`、`load_matured`（Task 6）；`frozen_stats.json`（Task 7）
- Produces（前端依賴的 JSON）：
  - `RunInfo` 新增 `verdict: {headline, detail, tone}`；health 白名單加 `drifted_psi`、`missing_shift`、`psi_max`。
  - `BoardItem` 新增 `est_target_price: float | None`、`est_stop_price: float | None`、`pred_mfe_3d`、`pred_mfe_5d`。
  - `Board` 新增 `gate_thresholds: {target_vn_min: float | None, stop_vn_max: float | None}`。
  - `/status` 新增 `live_progress: {matured_days: int, observe_at: 20, decide_at: 60}`。
  - `/health`：`frozen_validation.metrics` 合併 frozen_stats；新增 `convergence: list`、`live.days: list`；`history` 每列加 `n_drifted: int | None`。
  - 新端點 `GET /mlentry/tracking?days=10` → `load_tracking` 結果。

- [ ] **Step 1: Write the failing tests**

在 `backend/tests/test_mlentry_api.py` fixture 的 `health_json` 改為：

```python
health_json=json.dumps({"data_quality": {"ok": True}, "feature_health": {"ok": True, "n_drifted": 0, "drifted_psi": {}},
                        "recommendation": {"qualified_count": 2}})
```

檔尾加：

```python
def test_board_new_fields(client, monkeypatch):
    monkeypatch.setattr(routes_mlentry, "load_champion", lambda: None)
    j = client.get(f"/api/mlentry/board?signal_date={SIGNAL.isoformat()}").json()
    it = j["items"][0]
    assert "est_target_price" in it and "est_stop_price" in it and "pred_mfe_5d" in it
    assert set(j["gate_thresholds"]) == {"target_vn_min", "stop_vn_max"}
    v = j["run"]["verdict"]
    assert v["tone"] == "ok" and v["headline"] == "正常出單 1 檔"
    assert "drifted_psi" in j["run"]["health"]["feature_health"]


def test_status_live_progress(client, monkeypatch):
    monkeypatch.setattr(routes_mlentry, "load_champion", lambda: None)
    j = client.get("/api/mlentry/status").json()
    assert j["live_progress"]["observe_at"] == 20 and j["live_progress"]["decide_at"] == 60
    assert isinstance(j["live_progress"]["matured_days"], int)
    assert j["last_run"]["verdict"]["headline"]


def test_health_convergence_and_days(client, monkeypatch):
    monkeypatch.setattr(routes_mlentry, "load_champion", lambda: None)
    j = client.get("/api/mlentry/health?limit=5").json()
    keys = [r["key"] for r in j["convergence"]]
    assert keys[:3] == ["lift_at_1", "lift_at_3", "lift_at_5"]
    assert isinstance(j["live"]["days"], list)
    assert all("n_drifted" in h for h in j["history"])


def test_tracking_shape(client):
    r = client.get("/api/mlentry/tracking?days=10"); assert r.status_code == 200
    j = r.json()
    assert set(j) == {"as_of", "summary", "items"} and j["summary"]["n"] == len(j["items"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_api.py -v`
Expected: 新 4 個 FAIL（KeyError／404）

- [ ] **Step 3: Write implementation**

在 `routes_mlentry.py`：

1. import 區加：

```python
import sqlite3
from pathlib import Path

from ..config import get_settings
from ..mlentry.datasets import api as ds_api
from ..mlentry.serving.presentation import build_verdict, est_barrier_prices
from ..mlentry.serving.tracking import load_tracking
```

（若 `app/config.py` 的 `get_settings` 相對路徑不同，以 `scripts/mlentry_daily.py` 的 `from app.config import get_settings` 為準，改成 `from ..config import get_settings` 對應的實際模組；routes 位於 `app/api/`，故 `..config` 指 `app/config.py`。）

2. 新增 helpers（放在 `_stack_info` 之後）：

```python
def _frozen_stats(s) -> dict:
    """讀 data/mlentry/<champion dataset>/policy/<policy>/frozen_stats.json；缺檔回 {}。"""
    if s is None:
        return {}
    try:
        base = Path(ds_api.latest_dataset_dir()).parent / s.dataset_version / "policy" / s.policy_name / "frozen_stats.json"
        return json.loads(base.read_text(encoding="utf-8")) if base.exists() else {}
    except Exception:                                   # 缺資料夾／JSON 壞：降級為 —
        return {}


def _candidate_band(fs: dict) -> tuple[float, float] | None:
    lo, hi = fs.get("candidates_p05"), fs.get("candidates_p95")
    return (float(lo), float(hi)) if lo is not None and hi is not None else None


def _gate_thresholds(s) -> dict:
    try:
        g = load_yaml(s.policy_name)["gate"] if s else {}
    except Exception:
        g = {}
    return {"target_vn_min": g.get("theta_alpha_pct"), "stop_vn_max": g.get("theta_risk_pct")}
```

3. `RunInfo` 加欄位 `verdict: dict`；`_run_info(r)` 改為 `_run_info(r, band=None)`：health 白名單 tuple 加 `"drifted_psi", "missing_shift"`（`psi_max` 已在），並在建構時帶：

```python
verdict=build_verdict(r.status, r.no_trade_reason, NO_TRADE_TEXT.get(r.no_trade_reason or ""), r.universe_count,
                      r.qualified_count, r.recommendation_count, health, band)
```

（`health` 為 `json.loads` 後的原始 dict，在白名單過濾前傳入。）

4. `BoardItem` 加 `pred_mfe_3d: float | None`、`pred_mfe_5d: float | None`、`est_target_price: float | None`、`est_stop_price: float | None`；`item()` 內：

```python
        et, es = est_barrier_prices(close)
        return BoardItem(..., pred_mfe_3d=p.pred_mfe_3d, pred_mfe_5d=p.pred_mfe_5d, est_target_price=et, est_stop_price=es)
```

`Board` 加 `gate_thresholds: dict`；`board()` 內 `s = load_champion()` 移到函式開頭，`band = _candidate_band(_frozen_stats(s))`，`run=_run_info(r, band)`，回傳時帶 `gate_thresholds=_gate_thresholds(s)`（`r is None` 分支也要帶 `gate_thresholds=_gate_thresholds(s)`）。

5. `/status`：

```python
    mon = load_yaml("monitoring")
    live = performance.rolling_live_metrics(performance.load_matured(session), windows=tuple(mon["live_metrics"]["windows"]),
                                            k=int(mon["live_metrics"]["k"]))
    band = _candidate_band(_frozen_stats(s))
    return {..., "last_run": _run_info(r, band).model_dump() if r else None,
            "live_progress": {"matured_days": int(live["matured_days"]), "observe_at": 20, "decide_at": 60}}
```

6. `/health`：

```python
    matured = performance.load_matured(session)
    live = performance.rolling_live_metrics(matured, windows=..., k=...)     # 同原本
    live["days"] = performance.daily_matured(matured, k=int(mon["live_metrics"]["k"]))
    fs = _frozen_stats(s)
    fv = {**(s.frozen_validation if s else {}), **{k: v for k, v in fs.items() if k not in ("policy_name", "generated_at")}}
    ...
    hist 每列加 "n_drifted": (json.loads(r.health_json).get("feature_health", {}) or {}).get("n_drifted") if r.health_json else None
    frozen = {"metrics": fv, ...}                                             # 其餘不變
    return {..., "convergence": performance.convergence(fv, live)}
```

7. 新端點：

```python
@router.get("/tracking", response_model=dict)
def tracking(days: int = Query(10, ge=1, le=30), session: Session = Depends(get_session)):
    con = sqlite3.connect(str(get_settings().db_path))
    try:
        return load_tracking(con, session, days=days)
    finally:
        con.close()
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_api.py tests/test_mlentry_presentation.py tests/test_mlentry_tracking.py tests/test_mlentry_performance_ui.py tests/test_mlentry_serving.py -v`
Expected: 全數 PASS

另跑一次實機端點（8000 在跑時）：

```bash
curl -s "http://127.0.0.1:8000/api/mlentry/tracking?days=10"
curl -s "http://127.0.0.1:8000/api/mlentry/status"
```

Expected: 兩者 200；status 的 `last_run.verdict.headline` 為中文判讀句。若 8000 需重啟才載入新程式，用 `start.bat` 既有方式重啟（見記憶「Windows 網站模式」），不要另起伺服器。

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes_mlentry.py backend/tests/test_mlentry_api.py
git commit -m "feat(mlentry): API——verdict・估算價・gate 門檻・前瞻進度・收斂對照・逐日成熟・/tracking

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: 前端型別與 hooks

**Files:**
- Modify: `frontend/src/api/client.ts:1360-1436`

**Interfaces:**
- Produces（供 Task 10–14）：`MLEntryVerdict`、`MLEntryRun.verdict`、`MLEntryItem.est_target_price/est_stop_price/pred_mfe_3d/pred_mfe_5d`、`MLEntryBoard.gate_thresholds`、`MLEntryStatus.live_progress`、`MLEntryConvergenceRow`、`MLEntryMaturedDay`、`MLEntryHealth.convergence`、`MLEntryHealth.live.days`、`MLEntryHealth.history[].n_drifted`、`MLEntryTrackingItem`、`MLEntryTracking`、`useMLEntryTracking()`

- [ ] **Step 1: 修改型別**

在 `MLEntryRun` 之前加：

```ts
export type MLEntryVerdict = { headline: string; detail: string; tone: "ok" | "quiet" | "fail" };
```

`MLEntryRun` 加 `verdict: MLEntryVerdict;`。
`MLEntryItem` 加 `pred_mfe_3d: number | null; pred_mfe_5d: number | null; est_target_price: number | null; est_stop_price: number | null;`。
`MLEntryBoard` 加 `gate_thresholds: { target_vn_min: number | null; stop_vn_max: number | null };`。
`MLEntryLiveWindow` 加 `lift?: Record<string, number | null>; coverage?: number; candidates_median?: number; no_trade_rate?: number;`。

在 `MLEntryHealth` 之前加：

```ts
export type MLEntryConvergenceRow = {
  key: string; label: string; fmt: "x" | "ratio" | "pct" | "num" | "num3";
  frozen: number | null; band: [number, number] | null; band_kind: "ci" | "dist" | null;
  live: Record<string, number | null>; verdict: Record<string, string>;
};
export type MLEntryMaturedDay = {
  signal_date: string; n_rec: number; target: number; stop: number; timeout: number;
  lift: number | null; net10: number | null;
};
```

`MLEntryHealth`：`live` 改為 `{ matured_days: number; windows: Record<string, MLEntryLiveWindow>; days: MLEntryMaturedDay[] }`；`history` 元素加 `n_drifted: number | null`；新增 `convergence: MLEntryConvergenceRow[];`。
`MLEntryStatus` 加 `live_progress: { matured_days: number; observe_at: number; decide_at: number };`。

在 `useMLEntryStatus` 之後加：

```ts
export type MLEntryTrackingStatus =
  "PENDING_ENTRY" | "LIVE" | "TARGET" | "STOP" | "STOP_AMBIGUOUS" | "TIMEOUT" | "NOT_ENTERED" | "DATA_MISSING";
export type MLEntryTrackingItem = {
  signal_date: string; stock_id: string; name: string | null; day_index: number; horizon: number;
  status: MLEntryTrackingStatus; hit_day: number | null;
  ret_now: number | null; mfe: number | null; mae: number | null;
};
export type MLEntryTracking = {
  as_of: string | null;
  summary: { n: number; target: number; stop: number; timeout: number; live: number; pending: number };
  items: MLEntryTrackingItem[];
};
export function useMLEntryTracking() {
  return useQuery({
    queryKey: ["mlentry-tracking"],
    queryFn: () => getJson<MLEntryTracking>("/mlentry/tracking?days=10"),
    staleTime: 5 * 60_000,
  });
}
```

- [ ] **Step 2: Typecheck**

Run（在 `frontend/`）：`npx tsc --noEmit`
Expected: 無錯誤（既有元件只讀舊欄位，新欄位皆為新增）。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/client.ts
git commit -m "feat(level1-ui): mlentry 型別——verdict・估算價・收斂列・逐日成熟・tracking hook

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: 健康條（取代 MLEntryStatusBanner）

**Files:**
- Modify: `frontend/src/components/MLEntryStatusBanner.tsx`（整檔改寫）
- Modify: `frontend/src/pages/MLEntryPage.tsx`

**Interfaces:**
- Consumes: `MLEntryStack`、`MLEntryRun`（含 `verdict`）、`MLEntryStatus["live_progress"]`
- Produces: `MLEntryStatusBanner({ stack, run, progress, onGateClick }: { stack?: MLEntryStack; run?: MLEntryRun | null; progress?: { matured_days: number; decide_at: number }; onGateClick: (gate: string) => void })`

- [ ] **Step 1: 改寫元件**

```tsx
import type { MLEntryRun, MLEntryStack } from "../api/client";

// 今日健康條（spec §1）：徽章＋判讀句（後端 verdict）＋四顆 gate 燈號＋前瞻進度。
// 判讀語意全在後端；前端只 render。Research Shadow 徽章不得移除。

export const GATES: { key: string; label: string }[] = [
  { key: "data_quality", label: "資料" },
  { key: "feature_health", label: "特徵" },
  { key: "prediction_health", label: "預測" },
  { key: "recommendation", label: "推薦分布" },
];

function dotClass(g: Record<string, unknown> | undefined): string {
  if (!g || g.ok === undefined) return g ? "bg-emerald-400" : "bg-gray-600";   // recommendation 無 ok 欄：有資料即綠
  return g.ok ? "bg-emerald-400" : "bg-rose-400";
}

export function MLEntryStatusBanner({ stack, run, progress, onGateClick }: {
  stack?: MLEntryStack; run?: MLEntryRun | null;
  progress?: { matured_days: number; decide_at: number };
  onGateClick: (gate: string) => void;
}) {
  if (!stack) return null;
  const promoted = stack.model_status === "PROMOTED";
  const v = run?.verdict;
  const tone = v?.tone ?? "quiet";
  const frame = tone === "fail" ? "border-rose-800 bg-rose-950/30" : "border-gray-800 bg-gray-900/60";
  return (
    <div className={`rounded-lg border px-4 py-3 ${frame}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span title={promoted ? "已通過 promotion contract" : "未通過 promotion contract，非正式進場推薦"}
              className={`rounded px-1.5 py-0.5 text-[11px] font-semibold ${
                promoted ? "bg-emerald-900 text-emerald-200" : "bg-amber-900/70 text-amber-200"}`}>
          {promoted ? "Promoted" : stack.model_status_label}
        </span>
        <span className={`text-base font-bold ${tone === "fail" ? "text-rose-300" : "text-gray-100"}`}>
          {run ? `${run.signal_date.slice(5)} ${v?.headline ?? run.status}` : "尚無 run"}
        </span>
        {progress && (
          <span className="ml-auto text-xs tabular-nums text-gray-500">
            前瞻 {progress.matured_days}/{progress.decide_at}
          </span>
        )}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-xs text-gray-400">
          {run ? v?.detail : "每日 21:30 MLEntryDailyStep 執行後產生"}
        </span>
        {run && (
          <span className="ml-auto flex items-center gap-1.5">
            {GATES.map((g) => {
              const gv = run.health?.[g.key] as Record<string, unknown> | undefined;
              return (
                <button key={g.key} onClick={() => onGateClick(g.key)} title={`${g.label}：${
                  gv?.ok === false ? "未通過" : gv ? "通過" : "未執行"}`}
                        className={`h-2.5 w-2.5 rounded-full ${dotClass(gv)}`} />
              );
            })}
            {tone === "fail" && (
              <button onClick={() => onGateClick("feature_health")} className="ml-2 text-xs text-sky-300 hover:underline">
                看原因 →
              </button>
            )}
          </span>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 接線 MLEntryPage**

`MLEntryPage.tsx`：`const [focusGate, setFocusGate] = useState<string | null>(null);`；橫幅改為：

```tsx
<MLEntryStatusBanner stack={stack} run={board?.run ?? status?.last_run} progress={status?.live_progress}
                     onGateClick={(g) => { setFocusGate(g); setTab("system"); }} />
```

`MLEntrySystemView` 呼叫處加 `focusGate={focusGate}`（Task 14 實作該 prop；本步先在 `MLEntrySystem.tsx` 的 props 型別加 `focusGate?: string | null` 並忽略，確保 tsc 通過）。

- [ ] **Step 3: Build**

Run（`frontend/`）：`npm run build`
Expected: 成功

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/MLEntryStatusBanner.tsx frontend/src/pages/MLEntryPage.tsx frontend/src/components/MLEntrySystem.tsx
git commit -m "feat(level1-ui): 今日健康條——徽章＋後端判讀句＋四 gate 燈號（點擊切系統狀態）＋前瞻進度

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: 今日推薦緊湊表格

**Files:**
- Modify: `frontend/src/components/MLEntryBoard.tsx`（整檔改寫）

**Interfaces:**
- Consumes: `MLEntryBoard`（`items`、`candidates`、`market_base`、`gate_thresholds`、`run`）
- Produces: `MLEntryBoardView({ board, isLoading })`（簽名不變）

- [ ] **Step 1: 改寫元件**

```tsx
import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import type { MLEntryBoard as BoardT, MLEntryItem } from "../api/client";

// 今日推薦（spec §2.1，mockup card-layout B）：緊湊表格＋點列展開。
// 估算價由後端依台股升降單位、保守側取整；實際 barrier 從明日開盤起算。漲紅跌綠。

const pct = (v: number | null | undefined, d = 1) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);
const vn = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(2).replace(/^0/, ""));
const px = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(2));
const ratio = (v: number | null, base: number | null) => (v != null && base ? `${(v / base).toFixed(2)}×` : "");

function Head({ withPrice }: { withPrice: boolean }) {
  return (
    <thead className="bg-gray-900 text-left text-xs text-gray-400">
      <tr>
        <th className="px-3 py-2">#</th><th className="px-3 py-2">股票</th>
        <th className="px-3 py-2 text-right">Target 10D</th><th className="px-3 py-2 text-right">Stop 10D</th>
        <th className="px-3 py-2 text-right">MFE</th><th className="px-3 py-2 text-right">vn T/S</th>
        {withPrice && <th className="px-3 py-2 text-right">目標／停損*</th>}
      </tr>
    </thead>
  );
}

function Row({ it, board, withPrice, open, onToggle }: {
  it: MLEntryItem; board: BoardT; withPrice: boolean; open: boolean; onToggle: () => void;
}) {
  const b = board.market_base; const g = board.gate_thresholds;
  return (
    <Fragment>
      <tr onClick={onToggle} className="cursor-pointer border-t border-gray-800/60 hover:bg-gray-800/30">
        <td className="px-3 py-2 tabular-nums text-gray-400">{it.rank ?? "—"}</td>
        <td className="px-3 py-2">
          <Link to={`/stocks/${it.stock_id}`} onClick={(e) => e.stopPropagation()} className="text-sky-300 hover:underline">
            {it.stock_id} {it.name ?? ""}
          </Link>
        </td>
        <td className="px-3 py-2 text-right tabular-nums text-rose-300">
          {pct(it.p_target_10d)} <span className="text-xs text-gray-500">{ratio(it.p_target_10d, b.market_target_rate)}</span>
        </td>
        <td className="px-3 py-2 text-right tabular-nums text-emerald-300">
          {pct(it.p_stop_10d)} <span className="text-xs text-gray-500">{ratio(it.p_stop_10d, b.market_stop_rate)}</span>
        </td>
        <td className="px-3 py-2 text-right tabular-nums">{pct(it.pred_mfe_10d)}</td>
        <td className="px-3 py-2 text-right tabular-nums"
            title={g.target_vn_min != null ? `Gate：Target vn ≥ ${g.target_vn_min}、Stop vn ≤ ${g.stop_vn_max}（同日同 ATR 十分位內百分位）` : ""}>
          {vn(it.p_target_vn)}/{vn(it.p_stop_vn)}
        </td>
        {withPrice && (
          <td className="px-3 py-2 text-right tabular-nums">
            <span className="text-rose-300">{px(it.est_target_price)}</span>
            <span className="text-gray-500"> / </span>
            <span className="text-emerald-300">{px(it.est_stop_price)}</span>
          </td>
        )}
      </tr>
      {open && (
        <tr className="bg-gray-900/70">
          <td colSpan={withPrice ? 7 : 6} className="px-3 py-2 text-xs tabular-nums text-gray-400">
            時序 T {pct(it.p_target_3d)}→{pct(it.p_target_5d)}→{pct(it.p_target_10d)}
            <span className="mx-2 text-gray-600">｜</span>
            S {pct(it.p_stop_3d)}→{pct(it.p_stop_5d)}→{pct(it.p_stop_10d)}
            <span className="mx-2 text-gray-600">｜</span>MFE {pct(it.pred_mfe_3d)}→{pct(it.pred_mfe_5d)}→{pct(it.pred_mfe_10d)}
            <span className="mx-2 text-gray-600">｜</span>ATR {pct(it.atr_pct)}
            <span className="mx-2 text-gray-600">｜</span>Score {it.recommendation_score?.toFixed(3) ?? "—"}
            <span className="mx-2 text-gray-600">｜</span>P exec {pct(it.p_executable)}
            <span className="mx-2 text-gray-600">｜</span>收 {px(it.close)}
            <Link to={`/stocks/${it.stock_id}`} className="ml-3 text-sky-300 hover:underline">個股頁 →</Link>
          </td>
        </tr>
      )}
    </Fragment>
  );
}

export function MLEntryBoardView({ board, isLoading }: { board: BoardT | undefined; isLoading: boolean }) {
  const [open, setOpen] = useState<string | null>(null);
  if (isLoading) return <div className="py-8 text-center text-gray-500">載入中…</div>;
  if (!board?.run) return <div className="py-8 text-center text-gray-500">尚無 run——每日盤後 MLEntryDailyStep 執行後產生。</div>;
  const run = board.run; const b = board.market_base;
  const toggle = (id: string) => setOpen((o) => (o === id ? null : id));
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 text-xs text-gray-500">
        <span className="text-sm font-semibold text-gray-300">今日推薦</span>
        {b.market_target_rate != null && (
          <span>基率 Target {pct(b.market_target_rate)}・Stop {pct(b.market_stop_rate)}（Frozen 期間）</span>
        )}
      </div>
      {!run.no_trade && board.items.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full text-sm">
            <Head withPrice />
            <tbody>
              {board.items.map((it) => (
                <Row key={it.stock_id} it={it} board={board} withPrice open={open === it.stock_id} onToggle={() => toggle(it.stock_id)} />
              ))}
            </tbody>
          </table>
          <div className="border-t border-gray-800/60 px-3 py-1.5 text-[11px] text-gray-500">
            * 以收盤估算並依升降單位取保守側；實際 barrier 從明日開盤 ×1.10／×0.95 起算。點列展開時序。
          </div>
        </div>
      )}
      {board.candidates.length > 0 && (
        <details className="rounded-lg border border-gray-800">
          <summary className="cursor-pointer px-4 py-2 text-sm text-gray-300">通過 Gate 但未入 Top-K（{board.candidates.length}）</summary>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <Head withPrice={false} />
              <tbody>
                {board.candidates.map((c) => (
                  <Row key={c.stock_id} it={c} board={board} withPrice={false} open={open === c.stock_id} onToggle={() => toggle(c.stock_id)} />
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Build**

Run（`frontend/`）：`npm run build`
Expected: 成功

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/MLEntryBoard.tsx
git commit -m "feat(level1-ui): 今日推薦改緊湊表格——10D 主欄＋倍率・vn 證據・估算目標／停損價・點列展開時序

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: 追蹤中表格＋量尺

**Files:**
- Create: `frontend/src/components/MLEntryTracking.tsx`
- Modify: `frontend/src/pages/MLEntryPage.tsx`

**Interfaces:**
- Consumes: `useMLEntryTracking()`、`MLEntryTracking`、`MLEntryTrackingItem`（Task 9）
- Produces: `MLEntryTrackingView({ data, isLoading }: { data?: MLEntryTracking; isLoading: boolean })`

- [ ] **Step 1: 建立元件**

```tsx
import { Link } from "react-router-dom";
import type { MLEntryTracking, MLEntryTrackingItem } from "../api/client";

// 追蹤中（spec §2.3，mockup tracking B）：近 10 交易日推薦的即時路徑。
// 量尺 −5%～0～+10%：白棒＝目前報酬；灰帶＝期間 [MAE, MFE]。報酬以 barrier 基準（推薦隔日開盤）起算。

const LO = -0.05, HI = 0.10;
const pos = (v: number) => `${((Math.min(Math.max(v, LO), HI) - LO) / (HI - LO)) * 100}%`;
const pct = (v: number | null, d = 1) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(d)}%`);

const CHIP: Record<string, { text: (it: MLEntryTrackingItem) => string; cls: string }> = {
  PENDING_ENTRY: { text: () => "待進場", cls: "bg-gray-800 text-gray-300" },
  LIVE: { text: () => "進行中", cls: "bg-gray-800 text-gray-200" },
  TARGET: { text: (it) => `TARGET D${it.hit_day ?? "?"}`, cls: "bg-rose-950 text-rose-300" },
  STOP: { text: (it) => `STOP D${it.hit_day ?? "?"}`, cls: "bg-emerald-950 text-emerald-300" },
  STOP_AMBIGUOUS: { text: (it) => `同日雙觸 D${it.hit_day ?? "?"}`, cls: "bg-amber-950 text-amber-300" },
  TIMEOUT: { text: () => "TIMEOUT", cls: "bg-gray-800 text-gray-400" },
  NOT_ENTERED: { text: () => "無法進場", cls: "bg-gray-800 text-gray-500" },
  DATA_MISSING: { text: () => "資料缺漏", cls: "bg-gray-800 text-gray-500" },
};

function Gauge({ it }: { it: MLEntryTrackingItem }) {
  if (it.ret_now == null || it.mfe == null || it.mae == null) {
    return <span className="text-xs text-gray-500">{it.status === "PENDING_ENTRY" ? "待明日開盤" : "—"}</span>;
  }
  const tip = `MFE ${pct(it.mfe)} ／ MAE ${pct(it.mae)} ／ 距目標 ${((HI - it.ret_now) * 100).toFixed(1)}pp ／ 距停損 ${((it.ret_now - LO) * 100).toFixed(1)}pp`;
  return (
    <div title={tip} className="relative h-2.5 w-36 rounded-full"
         style={{ background: "linear-gradient(90deg, rgb(6 78 59 / .7) 0, rgb(31 41 55) 33.3%, rgb(31 41 55) 33.3%, rgb(76 5 25 / .7) 100%)" }}>
      <div className="absolute top-[3px] h-1 rounded bg-gray-400/40" style={{ left: pos(it.mae), width: `calc(${pos(it.mfe)} - ${pos(it.mae)})` }} />
      <div className="absolute -top-0.5 -bottom-0.5 w-px bg-gray-500" style={{ left: pos(0) }} />
      <div className="absolute -top-1 h-[18px] w-1 -translate-x-1/2 rounded-sm bg-gray-50" style={{ left: pos(it.ret_now) }} />
    </div>
  );
}

export function MLEntryTrackingView({ data, isLoading }: { data?: MLEntryTracking; isLoading: boolean }) {
  if (isLoading) return <div className="py-4 text-center text-sm text-gray-500">載入中…</div>;
  const s = data?.summary;
  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-baseline gap-3">
        <span className="text-sm font-semibold text-gray-300">追蹤中</span>
        {s && s.n > 0 && (
          <span className="text-xs text-gray-500">
            近 10 日 {s.n} 檔 · <span className="text-rose-300">TARGET {s.target}</span> · <span className="text-emerald-300">STOP {s.stop}</span>
            {" "}· 進行中 {s.live}{s.pending ? ` · 待進場 ${s.pending}` : ""}{s.timeout ? ` · TIMEOUT ${s.timeout}` : ""}
          </span>
        )}
      </div>
      {!data || data.items.length === 0 ? (
        <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-3 text-sm text-gray-500">近 10 個交易日沒有推薦。</div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full text-sm">
            <thead className="bg-gray-900 text-left text-xs text-gray-400">
              <tr>
                <th className="px-3 py-2">推薦日</th><th className="px-3 py-2">股票</th>
                <th className="px-3 py-2 text-right">天數</th><th className="px-3 py-2 text-right">目前報酬</th>
                <th className="px-3 py-2">−5% ─ 0 ── +10%</th><th className="px-3 py-2">狀態</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((it) => {
                const chip = CHIP[it.status];
                return (
                  <tr key={`${it.signal_date}-${it.stock_id}`} className="border-t border-gray-800/60">
                    <td className="px-3 py-2 tabular-nums text-gray-400">{it.signal_date.slice(5)}</td>
                    <td className="px-3 py-2">
                      <Link to={`/stocks/${it.stock_id}`} className="text-sky-300 hover:underline">{it.stock_id} {it.name ?? ""}</Link>
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-gray-400">{it.day_index}/{it.horizon}</td>
                    <td className={`px-3 py-2 text-right tabular-nums ${
                      it.ret_now == null ? "text-gray-500" : it.ret_now >= 0 ? "text-rose-300" : "text-emerald-300"}`}>
                      {pct(it.ret_now)}
                    </td>
                    <td className="px-3 py-2"><Gauge it={it} /></td>
                    <td className="px-3 py-2">
                      <span className={`rounded px-1.5 py-0.5 text-[11px] ${chip.cls}`}>{chip.text(it)}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="border-t border-gray-800/60 px-3 py-1.5 text-[11px] text-gray-500">
            白棒＝目前報酬；灰帶＝期間最低～最高（MAE～MFE）。報酬自推薦隔日開盤起算，與模型 barrier 同基準。游標移上看精確距離。
          </div>
        </div>
      )}
    </section>
  );
}
```

- [ ] **Step 2: 接線**

`MLEntryPage.tsx`：import `useMLEntryTracking` 與 `MLEntryTrackingView`；`const { data: tracking, isLoading: trackingLoading } = useMLEntryTracking();`；board 分頁改為：

```tsx
{tab === "board" && (
  <div className="space-y-6">
    <MLEntryBoardView board={board} isLoading={boardLoading} />
    <MLEntryTrackingView data={tracking} isLoading={trackingLoading} />
  </div>
)}
```

- [ ] **Step 3: Build**

Run（`frontend/`）：`npm run build`
Expected: 成功

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/MLEntryTracking.tsx frontend/src/pages/MLEntryPage.tsx
git commit -m "feat(level1-ui): 追蹤中——近 10 日推薦・目前報酬・−5%～+10% 量尺（MAE～MFE 帶）・狀態籤

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: 模型體檢頁（進度條＋收斂表＋逐日成熟）

**Files:**
- Modify: `frontend/src/components/MLEntryHealth.tsx`（整檔改寫）

**Interfaces:**
- Consumes: `MLEntryHealth`（`convergence`、`live.matured_days`、`live.days`、`frozen_validation.promotion_result`、`frozen_validation.note`）
- Produces: `MLEntryHealthView({ health, isLoading })`（簽名不變）；不再渲染 run 歷史（移至 Task 14）

- [ ] **Step 1: 改寫元件**

```tsx
import type { MLEntryConvergenceRow, MLEntryHealth as HealthT } from "../api/client";

// 模型體檢（spec §3，mockup convergence A）：前瞻進度 → Frozen vs Live 收斂對照 → 逐日成熟紀錄。
// 收斂判定由後端 convergence() 提供（描述性；20 成熟日只看不決策、60 為判斷點）。

function fmt(v: number | null, f: MLEntryConvergenceRow["fmt"]): string {
  if (v == null) return "—";
  if (f === "x") return `${v.toFixed(2)}×`;
  if (f === "ratio") return v.toFixed(2);
  if (f === "pct") return `${(v * 100).toFixed(2)}%`;
  if (f === "num3") return v.toFixed(3);
  return v % 1 === 0 ? String(v) : v.toFixed(1);
}

const VERDICT_CLS: Record<string, string> = {
  "CI 內": "text-emerald-300", "分布內": "text-emerald-300", "CI 外": "text-amber-300", "分布外": "text-amber-300",
  "參考": "text-gray-500", "累積中": "text-gray-500",
};

function Progress({ n, observe = 20, decide = 60 }: { n: number; observe?: number; decide?: number }) {
  const w = Math.min(n / decide, 1) * 100;
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-3">
      <div className="flex justify-between text-sm"><b>前瞻觀察進度</b><span className="tabular-nums text-gray-400">mature_days {n} / {decide}</span></div>
      <div className="relative mt-2 h-2 rounded bg-gray-800">
        <div className="absolute inset-y-0 left-0 rounded bg-sky-400" style={{ width: `${w}%` }} />
        <div className="absolute -top-1 h-4 w-0.5 bg-gray-400" style={{ left: `${(observe / decide) * 100}%` }} />
      </div>
      <div className="relative mt-1 h-4 text-[11px] text-gray-500">
        <span className="absolute -translate-x-1/2" style={{ left: `${(observe / decide) * 100}%` }}>{observe}：首個觀察點（只看不決策）</span>
        <span className="absolute right-0">{decide}：判斷點</span>
      </div>
    </div>
  );
}

export function MLEntryHealthView({ health, isLoading }: { health: HealthT | undefined; isLoading: boolean }) {
  if (isLoading || !health) return <div className="py-8 text-center text-gray-500">載入中…</div>;
  const fv = health.frozen_validation;
  return (
    <div className="space-y-6">
      <Progress n={health.live.matured_days} />

      <section>
        <div className="mb-2 flex flex-wrap items-baseline gap-3">
          <h2 className="text-base font-semibold">Frozen OOF vs 前瞻實績</h2>
          <span className={`rounded px-2 py-0.5 text-xs font-semibold ${fv.promotion_result === "PASS" ? "bg-emerald-900 text-emerald-200" : "bg-rose-900 text-rose-200"}`}>
            Promotion = {fv.promotion_result}
          </span>
          <span className="text-xs text-gray-500">{fv.note}</span>
        </div>
        <div className="overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full text-sm">
            <thead className="bg-gray-900 text-left text-xs text-gray-400">
              <tr>
                <th className="px-3 py-2">指標</th><th className="px-3 py-2 text-right">Frozen OOF</th>
                <th className="px-3 py-2 text-right">Frozen 區間</th><th className="px-3 py-2 text-right">Live 20D</th>
                <th className="px-3 py-2 text-right">Live 60D</th><th className="px-3 py-2">收斂（20D／60D）</th>
              </tr>
            </thead>
            <tbody>
              {health.convergence.map((r) => (
                <tr key={r.key} className="border-t border-gray-800/60">
                  <td className="px-3 py-2">{r.label}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{fmt(r.frozen, r.fmt)}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-xs text-gray-500">
                    {r.band ? `${r.band_kind === "ci" ? "CI" : "p5–p95"} [${fmt(r.band[0], r.fmt)}, ${fmt(r.band[1], r.fmt)}]` : "—"}
                  </td>
                  {(["20", "60"] as const).map((w) => (
                    <td key={w} className="px-3 py-2 text-right tabular-nums">
                      {r.verdict[w] === "累積中" && r.live[w] == null ? <span className="text-gray-500">累積中</span> : fmt(r.live[w], r.fmt)}
                    </td>
                  ))}
                  <td className="px-3 py-2 text-xs">
                    <span className={VERDICT_CLS[r.verdict["20"]] ?? ""}>{r.verdict["20"]}</span>
                    <span className="text-gray-600"> ／ </span>
                    <span className={VERDICT_CLS[r.verdict["60"]] ?? ""}>{r.verdict["60"]}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="border-t border-gray-800/60 px-3 py-1.5 text-[11px] text-gray-500">
            「收斂」為描述性比對；20 成熟日只看不決策，60 成熟日為判斷點（附錄 C）。
          </div>
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-base font-semibold">逐日成熟紀錄（Top-5）</h2>
        <div className="overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full text-sm">
            <thead className="bg-gray-900 text-left text-xs text-gray-400">
              <tr>
                <th className="px-3 py-2">推薦日</th><th className="px-3 py-2 text-right">推薦數</th>
                <th className="px-3 py-2 text-right">TARGET</th><th className="px-3 py-2 text-right">STOP</th>
                <th className="px-3 py-2 text-right">TIMEOUT</th><th className="px-3 py-2 text-right">當日 Lift</th>
                <th className="px-3 py-2 text-right">當日 Net10</th>
              </tr>
            </thead>
            <tbody>
              {health.live.days.length === 0 && (
                <tr><td colSpan={7} className="px-3 py-6 text-center text-gray-500">尚無成熟日；第一個 10D 成熟日約在首個 run 後 11 個交易日。</td></tr>
              )}
              {health.live.days.map((d) => (
                <tr key={d.signal_date} className="border-t border-gray-800/60 tabular-nums">
                  <td className="px-3 py-2 text-gray-400">{d.signal_date}</td>
                  <td className="px-3 py-2 text-right">{d.n_rec}</td>
                  <td className="px-3 py-2 text-right text-rose-300">{d.target}</td>
                  <td className="px-3 py-2 text-right text-emerald-300">{d.stop}</td>
                  <td className="px-3 py-2 text-right text-gray-400">{d.timeout}</td>
                  <td className="px-3 py-2 text-right">{d.lift == null ? "—" : `${d.lift.toFixed(2)}×`}</td>
                  <td className={`px-3 py-2 text-right ${d.net10 == null ? "" : d.net10 >= 0 ? "text-rose-300" : "text-emerald-300"}`}>
                    {d.net10 == null ? "—" : `${(d.net10 * 100).toFixed(2)}%`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
```

- [ ] **Step 2: Build**

Run（`frontend/`）：`npm run build`
Expected: 成功

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/MLEntryHealth.tsx
git commit -m "feat(level1-ui): 模型體檢——前瞻進度條（20/60）・Frozen vs Live 收斂對照表・逐日成熟紀錄

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: 系統狀態頁（敘事＋人讀 gate＋run 歷史＋折疊區）

**Files:**
- Modify: `frontend/src/components/MLEntrySystem.tsx`（整檔改寫）

**Interfaces:**
- Consumes: `MLEntryStatus`（`stack`、`last_run.verdict`、`last_run.health`）、`MLEntryHealth`（`history[].n_drifted`、`monitoring_thresholds`）、`focusGate`（Task 10）
- Produces: `MLEntrySystemView({ status, health, isLoading, focusGate })`

- [ ] **Step 1: 改寫元件**

```tsx
import { useEffect, useRef } from "react";
import type { MLEntryHealth as HealthT, MLEntryStatus } from "../api/client";

// 系統狀態（spec §4）：本次 run 敘事 → 四個人讀 gate → 近 60 日 run 歷史 → 折疊（stack／門檻）。
// 欄位中文標籤只是標籤，判定來自後端 health gate。

type G = Record<string, unknown> | undefined;
const num = (v: unknown, d = 0) => (typeof v === "number" ? v.toFixed(d) : "—");

const GATE_VIEW: { key: string; title: string; rows: (g: NonNullable<G>) => [string, string][] }[] = [
  { key: "data_quality", title: "資料品質", rows: (g) => [
    ["Universe 檔數", `${num(g.universe_count)}（參考中位 ${num(g.ref_median)}）`],
    ["核心缺值欄位", Array.isArray(g.bad) && g.bad.length ? (g.bad as string[]).join("、") : "無"],
  ] },
  { key: "feature_health", title: "特徵健康", rows: (g) => [
    ["漂移特徵數", num(g.n_drifted)],
    ["PSI 最大值", num(g.psi_max, 3)],
    ["日級超出範圍", Array.isArray(g.out_of_range_day_level) && g.out_of_range_day_level.length ? (g.out_of_range_day_level as string[]).join("、") : "無"],
  ] },
  { key: "prediction_health", title: "預測健康", rows: (g) => [["判定說明", typeof g.why === "string" ? g.why : "正常"]] },
  { key: "recommendation", title: "推薦分布（只監控）", rows: (g) => [
    ["通過 Gate", `${num(g.qualified_count)}（近 60 日中位 ${num(g.qualified_median_60d, 1)}）`],
    ["近 60 日 NO_TRADE 率", typeof g.no_trade_rate_60d === "number" ? `${(g.no_trade_rate_60d * 100).toFixed(1)}%` : "—"],
  ] },
];

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 border-t border-gray-800/60 py-1.5 text-sm">
      <span className="text-gray-500">{k}</span><span className="text-right font-mono text-xs text-gray-200">{v}</span>
    </div>
  );
}

export function MLEntrySystemView({ status, health, isLoading, focusGate }: {
  status: MLEntryStatus | undefined; health: HealthT | undefined; isLoading: boolean; focusGate?: string | null;
}) {
  const refs = useRef<Record<string, HTMLDivElement | null>>({});
  useEffect(() => {
    if (focusGate) refs.current[focusGate]?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [focusGate]);
  if (isLoading || !status) return <div className="py-8 text-center text-gray-500">載入中…</div>;
  const s = status.stack; const r = status.last_run; const v = r?.verdict;
  const fh = (r?.health?.feature_health ?? {}) as Record<string, unknown>;
  const psi = (fh.drifted_psi ?? {}) as Record<string, { psi: number; thr: number }>;
  const drifted = (fh.drifted ?? []) as string[];
  return (
    <div className="space-y-5">
      <section className={`rounded-lg border p-4 ${v?.tone === "fail" ? "border-rose-800 bg-rose-950/30" : "border-gray-800 bg-gray-900/60"}`}>
        <h2 className="text-base font-semibold">{r ? `${r.signal_date}：${v?.headline ?? r.status}` : "尚無 run"}</h2>
        {r && <p className="mt-1 text-sm text-gray-300">{v?.detail}</p>}
        {r && <p className="mt-1 text-xs text-gray-500">run_id {r.run_id}・Universe {r.universe_count}・通過 Gate {r.qualified_count}・推薦 {r.recommendation_count}・commit {r.code_commit}</p>}
        {drifted.length > 0 && (
          <table className="mt-3 w-full max-w-md text-xs">
            <thead className="text-left text-gray-500"><tr><th className="py-1">漂移特徵</th><th className="py-1 text-right">PSI</th><th className="py-1 text-right">門檻</th></tr></thead>
            <tbody>
              {drifted.map((n) => (
                <tr key={n} className="border-t border-gray-800/60 font-mono">
                  <td className="py-1">{n}</td>
                  <td className="py-1 text-right">{psi[n] ? psi[n].psi.toFixed(3) : "—"}</td>
                  <td className="py-1 text-right text-gray-500">{psi[n] ? psi[n].thr.toFixed(3) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {GATE_VIEW.map((gv) => {
          const g = r?.health?.[gv.key] as G;
          const ok = g?.ok as boolean | undefined;
          return (
            <div key={gv.key} ref={(el) => { refs.current[gv.key] = el; }}
                 className={`rounded-lg border bg-gray-900/60 p-3 ${focusGate === gv.key ? "border-sky-500" : "border-gray-800"}`}>
              <div className="flex items-center gap-2 text-sm">
                <span className={`inline-block h-2.5 w-2.5 rounded-full ${!g ? "bg-gray-600" : ok === false ? "bg-rose-400" : "bg-emerald-400"}`} />
                <span className="font-semibold">{gv.title}</span>
                <span className="text-xs text-gray-500">{!g ? "未執行" : ok === false ? "未通過（fail-closed）" : "通過"}</span>
              </div>
              {g && <div className="mt-1">{gv.rows(g).map(([k, val]) => <Row key={k} k={k} v={val} />)}</div>}
            </div>
          );
        })}
      </section>

      <section>
        <h2 className="mb-2 text-base font-semibold">近 60 日 run 歷史</h2>
        <div className="overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full text-sm">
            <thead className="bg-gray-900 text-left text-xs text-gray-400">
              <tr><th className="px-3 py-2">日期</th><th className="px-3 py-2">狀態</th><th className="px-3 py-2">原因</th>
                  <th className="px-3 py-2 text-right">Universe</th><th className="px-3 py-2 text-right">通過 Gate</th>
                  <th className="px-3 py-2 text-right">推薦</th><th className="px-3 py-2 text-right">漂移特徵數</th></tr>
            </thead>
            <tbody>
              {(health?.history ?? []).map((h) => (
                <tr key={h.signal_date} className="border-t border-gray-800/60 tabular-nums">
                  <td className="px-3 py-2">{h.signal_date}</td>
                  <td className={`px-3 py-2 ${h.status === "OK" ? "text-gray-200" : h.status === "SYSTEM_NO_TRADE" ? "text-rose-300" : "text-gray-400"}`}>{h.status}</td>
                  <td className="px-3 py-2 text-gray-400">{h.no_trade_reason ?? ""}</td>
                  <td className="px-3 py-2 text-right">{h.universe_count}</td>
                  <td className="px-3 py-2 text-right">{h.qualified_count}</td>
                  <td className="px-3 py-2 text-right">{h.recommendation_count}</td>
                  <td className="px-3 py-2 text-right text-gray-400">{h.n_drifted ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <details className="rounded-lg border border-gray-800 bg-gray-900/60 p-4">
        <summary className="cursor-pointer text-sm font-semibold">Serving stack 版本</summary>
        <div className="mt-2">
          <Row k="model_version" v={s.model_version ?? "—"} />
          <Row k="calibration_version" v={s.calibration_version ?? "—"} />
          <Row k="policy_version" v={`${s.policy_version ?? "—"}（${s.policy_name ?? "—"}）`} />
          <Row k="feature_version" v={s.feature_version ?? "—"} />
          <Row k="label_version" v={s.label_version ?? "—"} />
          <Row k="dataset_version" v={s.dataset_version ?? "—"} />
          <Row k="trained_through" v={s.trained_through ?? "—"} />
          <Row k="model_status" v={s.model_status} />
          <Row k="deployment_mode" v={s.deployment_mode} />
          <Row k="promotion_eligible" v={String(s.promotion_eligible)} />
          <Row k="final_holdout_access" v={String(status.final_holdout_access)} />
        </div>
      </details>
      {health?.monitoring_thresholds && (
        <details className="rounded-lg border border-gray-800 bg-gray-900/60 p-4">
          <summary className="cursor-pointer text-sm font-semibold">監控門檻（configs/mlentry/monitoring.yaml）</summary>
          <pre className="mt-2 overflow-x-auto text-xs text-gray-400">{JSON.stringify(health.monitoring_thresholds, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Build**

Run（`frontend/`）：`npm run build`
Expected: 成功

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/MLEntrySystem.tsx
git commit -m "feat(level1-ui): 系統狀態——run 敘事＋漂移特徵 PSI 表・人讀 gate 卡・run 歷史（含漂移數）・stack／門檻折疊

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: 瀏覽器實測與收尾

**Files:**
- 無新檔（若發現 bug，修在對應 Task 的檔案並另 commit）

- [ ] **Step 1: 全部後端測試**

Run（`backend/`）：`.venv/Scripts/python.exe -m pytest tests/test_mlentry_api.py tests/test_mlentry_presentation.py tests/test_mlentry_tracking.py tests/test_mlentry_performance_ui.py tests/test_mlentry_serving.py tests/test_mlentry_labels.py -q`
Expected: 全數 PASS

- [ ] **Step 2: 重建前端並重載 8000**

Run（`frontend/`）：`npm run build`。依既有方式讓 8000 服務新 build（見記憶「Windows 網站模式」：start.bat）；若 8000 已在跑且直接服務 `frontend/dist`，只需重新整理頁面。

- [ ] **Step 3: 瀏覽器檢查三分頁**

用內建瀏覽器開 `http://127.0.0.1:8000/app/level1`：
- 健康條：徽章、headline、四燈號、`前瞻 N/60`；點燈號切到系統狀態並框起對應 gate。
- 今日榜單：5 列表格，Target／Stop 倍率、vn、估算價（例：收 16.35 → 17.95／15.55）；點列展開時序；下方追蹤中表格有量尺或「待明日開盤」。
- 模型體檢：進度條、收斂表 11 列（Frozen 欄 Lift@1/3、ECE 有值 ⇒ frozen_stats.json 已讀到）、逐日成熟空狀態文案。
- 系統狀態：敘事、四 gate 卡、run 歷史含「漂移特徵數」，其中 2026-09-29 FEATURE_DRIFT run 若存在於歷史應顯示漂移數。
- `read_console_messages` 無錯誤。
- `resize_window` 360px 寬：表格可橫捲、頁面不破版；檢查後 `preset: "desktop"` 還原。

- [ ] **Step 4: 截圖留證並回報**

對三分頁各截一張；回報時附上。發現與 spec 不符的地方列出並修正（修正各自 commit）。
