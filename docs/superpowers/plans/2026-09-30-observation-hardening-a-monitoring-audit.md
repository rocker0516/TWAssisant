# Observation Hardening v1 — Spec A（§23 診斷＋§22 audit）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 依 spec `docs/superpowers/specs/2026-09-30-observation-hardening-a-monitoring-audit-design.md`，在不動任何 ML／policy／gate 的前提下，為每日 run 加上四個「只記錄」診斷（freshness／sanity／feature_shift／recommendation distribution，含 attention 換色）與 provenance audit（`audit_json`），並在 `/app/level1` 系統狀態頁顯示。

**Architecture:** 新增純函式模組 `monitoring/diagnostics.py`（四個診斷＋共用 envelope）、`monitoring/monitor_modes.py`（monitor_mode 名單、sidecar 建置與載入）、`fingerprint.py`（canonical JSON 與 SHA-256/12）、`serving/audit.py`（provenance 組裝＋API 白名單）。`daily_run` 在 policy 之後掛上這些呼叫，每個呼叫包在 `safe()` 內，失敗只寫 envelope。`MLEntryRun` 新增 `audit_json` 欄；API `RunInfo` 加 `diagnostics`／`audit`；前端只 render。

**Tech Stack:** FastAPI + SQLAlchemy + pandas/numpy（backend/.venv）、pytest；React + TypeScript + Tailwind（frontend，`npm run build` = `tsc --noEmit && vite build`）。

## Global Constraints

- 四個既有 health gate 函式與 `monitoring.yaml` 的 `data_quality / feature_drift / prediction_health / live_metrics / manual_halt` 區**一字不改**；診斷永不觸發 `SYSTEM_NO_TRADE`、永不改 run status。
- `configs/mlentry/` 只允許在 `monitoring.yaml` **新增** `diagnostics:` 區。
- **不得寫入 frozen champion artifact 目錄的任何既有檔案**（`stack.json`、`artifacts.json`、`feature_reference.json`、`*.lgbm.txt`、`*.calibrator.json`）；只能新增 sidecar `feature_reference.monitoring.json`。
- 診斷與 audit 在 policy 之後執行；例外一律 `log.exception` 並回 envelope；例外訊息不得進 DB JSON、API、UI（只留 `error_type`）。
- Diagnostic envelope 固定：正常 `{"evaluated": true, "attention": bool, ...}`；無法評估 `{"evaluated": false, "attention": false, "reason": "THRESHOLD_NOT_CONFIGURED"|"NO_DATA"|"NOT_APPLICABLE"}`；例外 `{"evaluated": false, "attention": false, "error_type": "<ClassName>"}`。
- 指紋統一 `sha256(canonical_json).hexdigest()[:12]`，canonical = `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)`。
- `audit_json` 只放 provenance；`health_json["diagnostics"]` 只放診斷。API 的 `audit` 只透出白名單 `requested_as_of, feature_snapshot_as_of, data_snapshot_id, serving_stack_hash, code_commit, as_of_mismatch`。
- 欄位名固定 `quantile_cdf_gap_7pt`；UI 文案固定「7-point reference-quantile ECDF gap（非 KS）」。市值診斷附 `mcap_basis: "current_company_profile_at_run_time"`。
- UI attention 只換顏色（琥珀），不影響健康條四燈號、徽章、判讀句。
- 不額外讀 `daily_prices` 全表。
- 後端測試一律在 `backend/` 下：`.venv/Scripts/python.exe -m pytest ...`。
- Commit 訊息結尾：`Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。

## File Structure

| 檔案 | 責任 |
|---|---|
| `backend/app/mlentry/fingerprint.py` | 新增：`canonical_json`、`canonical_hash` |
| `backend/app/mlentry/monitoring/monitor_modes.py` | 新增：`SKIP_FEATURES`、`monitor_mode_for`、`build_sidecar`、`load_monitor_modes` |
| `backend/app/mlentry/monitoring/diagnostics.py` | 新增：envelope helpers、`safe`、`freshness`、`sanity_summary`、`feature_shift`、`recommendation_distribution` |
| `backend/app/mlentry/serving/audit.py` | 新增：`serving_stack_hash`、`config_hashes`、`runtime_info`、`build_audit`、`audit_summary` |
| `backend/scripts/mlentry_feature_monitoring_sidecar.py` | 新增：CLI，只寫 sidecar |
| `backend/configs/mlentry/monitoring.yaml` | 新增 `diagnostics:` 區 |
| `backend/app/mlentry/serving/train_stack.py` | `build_feature_reference` 為新 stack 寫 `monitor_mode` |
| `backend/app/storage/models.py`、`database.py` | `MLEntryRun.audit_json`；`_COLUMN_ADDITIONS` |
| `backend/app/mlentry/serving/daily_run.py` | policy 後掛診斷與 audit；寫 `audit_json` |
| `backend/app/api/routes_mlentry.py` | `RunInfo.diagnostics/audit`、`_strip_messages`、history `attention_count` |
| `frontend/src/api/client.ts`、`components/MLEntrySystem.tsx` | 型別、診斷區、audit 摘要、提醒數欄 |
| tests | `test_mlentry_fingerprint.py`、`test_mlentry_monitor_modes.py`、`test_mlentry_diagnostics.py`、`test_mlentry_audit.py`、`test_mlentry_serving.py`（修改）、`test_mlentry_api.py`（修改）、`tests/fixtures/mlentry_gates_baseline.json` |

---

### Task 1: 指紋工具 `fingerprint.py`

**Files:**
- Create: `backend/app/mlentry/fingerprint.py`
- Test: `backend/tests/test_mlentry_fingerprint.py`

**Interfaces:**
- Produces: `canonical_json(obj: Any) -> str`、`canonical_hash(obj: Any) -> str`（12 hex）

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_mlentry_fingerprint.py
"""canonical JSON 指紋：key 順序／縮排不影響，值改變即變。"""

from __future__ import annotations

import json

from app.mlentry.fingerprint import canonical_hash, canonical_json


def test_canonical_hash_ignores_key_order_and_formatting():
    a = {"b": 1, "a": {"y": [1, 2], "x": "中"}}
    b = json.loads(json.dumps({"a": {"x": "中", "y": [1, 2]}, "b": 1}, indent=4))
    assert canonical_hash(a) == canonical_hash(b)
    assert len(canonical_hash(a)) == 12 and all(c in "0123456789abcdef" for c in canonical_hash(a))


def test_canonical_hash_changes_on_value_change():
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})


def test_canonical_json_is_compact_sorted_and_keeps_unicode():
    assert canonical_json({"b": 1, "a": "中"}) == '{"a":"中","b":1}'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_fingerprint.py -v`
Expected: FAIL（`ModuleNotFoundError: app.mlentry.fingerprint`）

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/mlentry/fingerprint.py
"""Content fingerprint（Spec A §0 規則 7）：全專案只保留一種指紋規格。

canonical JSON = sort_keys、無空白、保留 unicode、非 JSON 型別以 str() 序列化；hash = sha256[:12]。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def canonical_hash(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()[:12]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_fingerprint.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/mlentry/fingerprint.py backend/tests/test_mlentry_fingerprint.py
git commit -m "feat(mlentry): fingerprint——canonical JSON 與 sha256/12 內容指紋（audit／sidecar 共用）

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `monitor_modes.py`（名單、sidecar 建置、載入順序）

**Files:**
- Create: `backend/app/mlentry/monitoring/monitor_modes.py`
- Test: `backend/tests/test_mlentry_monitor_modes.py`

**Interfaces:**
- Consumes: `fingerprint.canonical_hash`
- Produces: `SKIP_FEATURES: frozenset[str]`（19 名）、`monitor_mode_for(name: str) -> str`、`build_sidecar(ref_full: dict) -> dict`、`load_monitor_modes(stack_dir: Path, ref_full: dict) -> tuple[dict[str, str], str]`（第二值 ∈ `{"explicit", "legacy_day_level_fallback"}`）、`SIDECAR_NAME = "feature_reference.monitoring.json"`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_mlentry_monitor_modes.py
"""monitor_mode：明確名單為正式來源；day_level 只是 legacy fallback 且要標記。"""

from __future__ import annotations

import json

from app.mlentry.fingerprint import canonical_hash
from app.mlentry.monitoring.monitor_modes import (SIDECAR_NAME, SKIP_FEATURES, build_sidecar, load_monitor_modes,
                                                   monitor_mode_for)

BINARY = {"is_attention_stock", "is_disposition_stock", "limit_up_today", "limit_down_today", "large_gap"}
DISCRETE = {"limit_up_count_20d", "limit_down_count_20d", "consecutive_up_days", "consecutive_down_days",
            "positive_day_ratio", "negative_day_ratio"}
DAY_LEVEL = {"market_ret_1d", "market_ret_5d", "market_ret_20d", "market_volatility", "industry_ret_5d",
             "industry_ret_20d", "industry_strength_rank", "breadth_ma20"}


def _ref(names, day_level=()):
    return {"features": {n: {"q": {}, "mean": 0.0, "std": 1.0, "day_level": n in day_level} for n in names},
            "predictions": {}, "universe_daily": {}}


def test_skip_list_is_exactly_the_19_semantic_names():
    assert SKIP_FEATURES == frozenset(BINARY | DISCRETE | DAY_LEVEL)
    assert monitor_mode_for("is_attention_stock") == "skip"
    assert monitor_mode_for("ret_5d") == "continuous"
    assert monitor_mode_for("dist_limit_up") == "continuous"


def test_build_sidecar_has_hash_and_modes():
    ref = _ref(["ret_5d", "is_attention_stock"])
    side = build_sidecar(ref)
    assert side["monitoring_schema_version"] == 1
    assert side["source_feature_reference_hash"] == canonical_hash(ref)
    assert side["features"] == {"ret_5d": {"monitor_mode": "continuous"}, "is_attention_stock": {"monitor_mode": "skip"}}


def test_load_prefers_artifact_native_monitor_mode(tmp_path):
    ref = _ref(["ret_5d", "x"])
    ref["features"]["ret_5d"]["monitor_mode"] = "skip"          # 人為：artifact 內建優先於名單
    ref["features"]["x"]["monitor_mode"] = "continuous"
    modes, src = load_monitor_modes(tmp_path, ref)
    assert src == "explicit" and modes == {"ret_5d": "skip", "x": "continuous"}


def test_load_uses_sidecar_when_hash_matches(tmp_path):
    ref = _ref(["ret_5d", "is_attention_stock"], day_level=())
    (tmp_path / SIDECAR_NAME).write_text(json.dumps(build_sidecar(ref)), encoding="utf-8")
    modes, src = load_monitor_modes(tmp_path, ref)
    assert src == "explicit" and modes == {"ret_5d": "continuous", "is_attention_stock": "skip"}


def test_load_falls_back_to_day_level_when_sidecar_missing_or_stale(tmp_path, caplog):
    ref = _ref(["ret_5d", "market_ret_1d"], day_level={"market_ret_1d"})
    modes, src = load_monitor_modes(tmp_path, ref)
    assert src == "legacy_day_level_fallback" and modes == {"ret_5d": "continuous", "market_ret_1d": "skip"}
    stale = build_sidecar(_ref(["other"]))
    (tmp_path / SIDECAR_NAME).write_text(json.dumps(stale), encoding="utf-8")
    with caplog.at_level("WARNING"):
        modes2, src2 = load_monitor_modes(tmp_path, ref)
    assert src2 == "legacy_day_level_fallback" and modes2 == modes
    assert any("monitoring.json" in m for m in caplog.messages)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_monitor_modes.py -v`
Expected: FAIL（`ModuleNotFoundError: app.mlentry.monitoring.monitor_modes`）

- [ ] **Step 3: Write implementation**

```python
# backend/app/mlentry/monitoring/monitor_modes.py
"""特徵分布監控模式 monitor_mode ∈ {continuous, skip}（Spec A §2.6）。

正式來源優先序：artifact 內建 monitor_mode（未來新 stack）→ sidecar feature_reference.monitoring.json（hash 須符）
→ legacy fallback（day_level → skip）。fallback 只為向後相容，不是分類規則；輸出會標 monitor_mode_source。
名單以「語意」判定（binary／離散計數比例／日級類股級），與現行 champion 的 day_level 集合相同是巧合。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..fingerprint import canonical_hash

log = logging.getLogger(__name__)

SIDECAR_NAME = "feature_reference.monitoring.json"
SCHEMA_VERSION = 1

_BINARY = ("is_attention_stock", "is_disposition_stock", "limit_up_today", "limit_down_today", "large_gap")
_DISCRETE = ("limit_up_count_20d", "limit_down_count_20d", "consecutive_up_days", "consecutive_down_days",
             "positive_day_ratio", "negative_day_ratio")
_DAY_LEVEL = ("market_ret_1d", "market_ret_5d", "market_ret_20d", "market_volatility",
              "industry_ret_5d", "industry_ret_20d", "industry_strength_rank", "breadth_ma20")
SKIP_FEATURES: frozenset[str] = frozenset(_BINARY + _DISCRETE + _DAY_LEVEL)


def monitor_mode_for(name: str) -> str:
    return "skip" if name in SKIP_FEATURES else "continuous"


def build_sidecar(ref_full: dict) -> dict:
    """由明確名單產生 sidecar；source hash 綁定當下 feature_reference.json 內容。"""
    return {"monitoring_schema_version": SCHEMA_VERSION,
            "source_feature_reference_hash": canonical_hash(ref_full),
            "features": {n: {"monitor_mode": monitor_mode_for(n)} for n in ref_full["features"]}}


def load_monitor_modes(stack_dir: Path, ref_full: dict) -> tuple[dict[str, str], str]:
    feats: dict = ref_full.get("features", {})
    if feats and all(isinstance(v, dict) and v.get("monitor_mode") in ("continuous", "skip") for v in feats.values()):
        return {n: v["monitor_mode"] for n, v in feats.items()}, "explicit"
    p = Path(stack_dir) / SIDECAR_NAME
    if p.exists():
        try:
            side = json.loads(p.read_text(encoding="utf-8"))
            if side.get("source_feature_reference_hash") == canonical_hash(ref_full):
                modes = {n: (side.get("features", {}).get(n) or {}).get("monitor_mode") for n in feats}
                if all(m in ("continuous", "skip") for m in modes.values()):
                    return modes, "explicit"
        except (ValueError, OSError):
            pass
        log.warning("%s ignored: hash mismatch or incomplete; using legacy day_level fallback", SIDECAR_NAME)
    else:
        log.warning("%s missing; using legacy day_level fallback", SIDECAR_NAME)
    return {n: ("skip" if v.get("day_level") else "continuous") for n, v in feats.items()}, "legacy_day_level_fallback"
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_monitor_modes.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/mlentry/monitoring/monitor_modes.py backend/tests/test_mlentry_monitor_modes.py
git commit -m "feat(mlentry): monitor_modes——19 名 skip 明確名單、sidecar 建置、artifact→sidecar→day_level fallback 載入順序

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Sidecar CLI＋新 stack 原生 `monitor_mode`

**Files:**
- Create: `backend/scripts/mlentry_feature_monitoring_sidecar.py`
- Modify: `backend/app/mlentry/serving/train_stack.py`（`build_feature_reference` 的 `entry = {...}` 之後）
- Test: `backend/tests/test_mlentry_monitor_modes.py`（新增）

**Interfaces:**
- Consumes: `monitor_modes.build_sidecar`、`monitor_mode_for`、`registry.versions.load_champion`
- Produces: CLI `python -m scripts.mlentry_feature_monitoring_sidecar [--model-version X] [--root DIR]`，只寫 `<stack_dir>/feature_reference.monitoring.json`；`write_sidecar(stack_dir: Path) -> tuple[Path, dict]`

- [ ] **Step 1: Write the failing tests**

在 `tests/test_mlentry_monitor_modes.py` 檔尾加：

```python
def test_write_sidecar_touches_only_sidecar_and_is_idempotent(tmp_path):
    import hashlib
    from scripts.mlentry_feature_monitoring_sidecar import write_sidecar
    ref = _ref(["ret_5d", "is_attention_stock"])
    (tmp_path / "feature_reference.json").write_text(json.dumps(ref, indent=2), encoding="utf-8")
    (tmp_path / "stack.json").write_text("{}", encoding="utf-8")
    (tmp_path / "target_10d.lgbm.txt").write_bytes(b"model-bytes")
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}
    path, side = write_sidecar(tmp_path)
    path2, side2 = write_sidecar(tmp_path)
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir() if p.name != SIDECAR_NAME}
    assert path == path2 == tmp_path / SIDECAR_NAME and side == side2
    assert before == after                                            # artifact 既有檔案逐 byte 不變
    assert side["features"]["is_attention_stock"]["monitor_mode"] == "skip"


def test_train_stack_reference_carries_native_monitor_mode():
    import numpy as np
    import pandas as pd
    from app.mlentry.serving.train_stack import build_feature_reference
    rng = np.random.default_rng(0)
    n = 400
    df = pd.DataFrame({"signal_date": np.repeat(pd.date_range("2024-01-01", periods=8).astype(str), n // 8),
                       "ret_5d": rng.normal(size=n), "is_attention_stock": rng.integers(0, 2, n).astype(float)})
    ref = build_feature_reference(df, ["ret_5d", "is_attention_stock"], np.ones(n, dtype=bool), sample_every=1)
    assert ref["ret_5d"]["monitor_mode"] == "continuous"
    assert ref["is_attention_stock"]["monitor_mode"] == "skip"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_monitor_modes.py -v`
Expected: 新 2 個 FAIL（`ModuleNotFoundError: scripts.mlentry_feature_monitoring_sidecar`；`KeyError: 'monitor_mode'`）

- [ ] **Step 3: Write implementation**

`train_stack.build_feature_reference`：在 `entry = {"missing_rate": ..., "day_level": bool(nun <= day_level_max)}` 那行之後加一行：

```python
        entry["monitor_mode"] = monitor_mode_for(n)                 # Spec A §2.6：新 stack 原生帶分布監控模式
```

並在檔案 import 區加 `from ..monitoring.monitor_modes import monitor_mode_for`。

```python
# backend/scripts/mlentry_feature_monitoring_sidecar.py
"""為 frozen champion 產生 feature_reference.monitoring.json sidecar（Spec A §2.6）。

    python -m scripts.mlentry_feature_monitoring_sidecar [--model-version X] [--root DIR]

只寫 sidecar；artifact 目錄其他檔案不讀寫（feature_reference.json 只讀）。冪等：內容相同不重寫。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.mlentry.monitoring.monitor_modes import SIDECAR_NAME, build_sidecar  # noqa: E402
from app.mlentry.registry.versions import SERVING_ROOT, load_champion  # noqa: E402


def write_sidecar(stack_dir: Path) -> tuple[Path, dict]:
    ref_full = json.loads((stack_dir / "feature_reference.json").read_text(encoding="utf-8"))
    side = build_sidecar(ref_full)
    path = stack_dir / SIDECAR_NAME
    text = json.dumps(side, ensure_ascii=False, indent=2, sort_keys=True)
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    return path, side


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-version", default=None); ap.add_argument("--root", default=None)
    args = ap.parse_args(argv)
    root = Path(args.root) if args.root else SERVING_ROOT
    mv = args.model_version
    if mv is None:
        champ = load_champion(root)
        if champ is None:
            print("no champion; pass --model-version"); return 1
        mv = champ.model_version
    path, side = write_sidecar(root / mv)
    modes = [v["monitor_mode"] for v in side["features"].values()]
    print(f"wrote {path}")
    print(f"continuous={modes.count('continuous')} skip={modes.count('skip')}")
    for n, v in sorted(side["features"].items()):
        print(f"  {v['monitor_mode']:10s} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests, then run the CLI on the real champion**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_monitor_modes.py tests/test_mlentry_serving.py -q`
Expected: 全數 PASS（serving 測試會用新 `build_feature_reference`）

先記錄 champion 目錄現況：`sha256sum data/mlentry/serving/mlentry_lgbm_6613b41a_20250814/*`（或 PowerShell `Get-FileHash`）存到暫存檔。
Run: `.venv/Scripts/python.exe -m scripts.mlentry_feature_monitoring_sidecar`
Expected: 印出 `continuous=38 skip=19`，skip 名單恰為 Task 2 測試中的 19 名。再跑一次 sha256sum 比對：除新增的 `feature_reference.monitoring.json` 外**所有檔案 hash 不變**。sidecar 位於 gitignored `backend/data/`，不 commit。

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/mlentry_feature_monitoring_sidecar.py backend/app/mlentry/serving/train_stack.py backend/tests/test_mlentry_monitor_modes.py
git commit -m "feat(mlentry): monitor_mode sidecar CLI（只寫 sidecar、artifact 不動）；新 stack feature_reference 原生帶 monitor_mode

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `diagnostics.py`——envelope、`safe`、`freshness`、`sanity_summary`

**Files:**
- Create: `backend/app/mlentry/monitoring/diagnostics.py`
- Modify: `backend/configs/mlentry/monitoring.yaml`（檔尾新增 `diagnostics:` 區）
- Test: `backend/tests/test_mlentry_diagnostics.py`

**Interfaces:**
- Consumes: `health.QS`、`data.calendar.TradingCalendar`、`data.quality.HardFlag`、`data.universe.EligFlag`
- Produces:
  - `envelope_ok(attention, **values) -> dict`、`envelope_skip(reason, **values) -> dict`、`envelope_error(exc) -> dict`、`safe(fn, *a, **kw) -> dict`
  - `latest_pipeline_watermark(con, step: str) -> dict | None`（`{"pipeline_run_id", "business_date", "completed_at"}`）
  - `freshness(as_of: str, cal: TradingCalendar, business_dates: dict[str, str | None], con, cfg: dict | None) -> dict`
  - `sanity_summary(elig_row: pd.Series, hard_row: pd.Series, cfg: dict | None) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_mlentry_diagnostics.py
"""§23 只記錄診斷：固定 envelope、freshness（business_date／ingestion_watermark）、sanity、feature_shift、recommendation。"""

from __future__ import annotations

import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

from app.mlentry.data.calendar import TradingCalendar
from app.mlentry.data.quality import HardFlag
from app.mlentry.data.universe import EligFlag
from app.mlentry.monitoring import diagnostics as dg

CAL = TradingCalendar([f"2026-09-{d:02d}" for d in (21, 22, 23, 24, 25, 28, 29)])
AS_OF = "2026-09-29"


def _con(rows):
    """pipeline_runs 假表：rows = [(id, trading_date, status, finished_at, steps_json)]。"""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE pipeline_runs (id INTEGER PRIMARY KEY, trading_date TEXT, started_at TEXT, finished_at TEXT, status TEXT, steps TEXT, error TEXT)")
    con.executemany("INSERT INTO pipeline_runs (id, trading_date, status, finished_at, steps) VALUES (?,?,?,?,?)", rows)
    return con


def _steps(**status):
    return json.dumps([{"name": k, "status": v} for k, v in status.items()])


def test_envelopes_and_safe():
    assert dg.envelope_ok(True, x=1) == {"evaluated": True, "attention": True, "x": 1}
    assert dg.envelope_skip("NO_DATA") == {"evaluated": False, "attention": False, "reason": "NO_DATA"}

    def boom():
        raise ValueError("secret /path/to/db")
    out = dg.safe(boom)
    assert out == {"evaluated": False, "attention": False, "error_type": "ValueError"}
    assert "secret" not in json.dumps(out)


FRESH_CFG = {"daily_prices": {"mode": "business_date", "max_lag_days": 0},
             "market_index": {"mode": "business_date", "max_lag_days": 0},
             "attention_listings": {"mode": "ingestion_watermark", "max_lag_days": 1, "step": "attention"}}


def test_freshness_normal_day_no_attention():
    con = _con([(1, "2026-09-28", "success", "2026-09-28 21:49:00", _steps(fetch="ok", attention="ok"))])
    out = dg.freshness(AS_OF, CAL, {"daily_prices": AS_OF, "market_index": AS_OF}, con, FRESH_CFG)
    assert out["evaluated"] and not out["attention"]
    assert out["sources"]["daily_prices"] == {"mode": "business_date", "max_date": AS_OF, "lag_days": 0, "attention": False}
    a = out["sources"]["attention_listings"]
    assert a["mode"] == "ingestion_watermark" and a["lag_trading_days"] == 1 and not a["attention"]
    assert a["watermark_pipeline_run_id"] == 1 and a["watermark_business_date"] == "2026-09-28"
    assert a["watermark_completed_at"] == "2026-09-28 21:49:00"


def test_freshness_stale_price_and_stale_watermark():
    con = _con([(1, "2026-09-25", "success", "2026-09-25 21:49:00", _steps(attention="ok")),
                (2, "2026-09-28", "failed", "2026-09-28 21:49:00", _steps(attention="failed"))])
    out = dg.freshness(AS_OF, CAL, {"daily_prices": "2026-09-28", "market_index": AS_OF}, con, FRESH_CFG)
    assert out["attention"]
    assert out["sources"]["daily_prices"]["lag_days"] == 1 and out["sources"]["daily_prices"]["attention"]
    a = out["sources"]["attention_listings"]
    assert a["watermark_pipeline_run_id"] == 1 and a["lag_trading_days"] == 2 and a["attention"]


def test_freshness_missing_source_is_attention_and_missing_threshold_is_not():
    con = _con([])
    cfg = {"daily_prices": {"mode": "business_date", "max_lag_days": 0}, "market_index": {"mode": "business_date"},
           "attention_listings": {"mode": "ingestion_watermark", "step": "attention", "max_lag_days": 1}}
    out = dg.freshness(AS_OF, CAL, {"daily_prices": None, "market_index": AS_OF}, con, cfg)
    dp, mi, at = out["sources"]["daily_prices"], out["sources"]["market_index"], out["sources"]["attention_listings"]
    assert dp["max_date"] is None and dp["lag_days"] is None and dp["attention"] is True        # 來源缺資料 → 提醒
    assert mi["lag_days"] == 0 and mi["attention"] is False and mi["reason"] == "THRESHOLD_NOT_CONFIGURED"
    assert at["watermark_pipeline_run_id"] is None and at["attention"] is True
    assert out["attention"]


def test_freshness_survives_missing_pipeline_runs_table():
    con = sqlite3.connect(":memory:")                          # 無 pipeline_runs 表（合成測試 DB）
    assert dg.latest_pipeline_watermark(con, "attention") is None


def test_freshness_without_config_is_skip():
    assert dg.freshness(AS_OF, CAL, {}, _con([]), None)["reason"] == "THRESHOLD_NOT_CONFIGURED"


SANITY_CFG = {"min_hard_flag_count": 5, "max_hard_flag_ratio": 0.005}


def _rows(n_present, n_hard, n_no_price=0):
    ids = [f"S{i}" for i in range(n_present + n_no_price)]
    elig = np.zeros(len(ids), dtype=np.uint16); hard = np.zeros(len(ids), dtype=np.uint16)
    elig[n_present:] = int(EligFlag.NO_PRICE)
    hard[:n_hard] = int(HardFlag.HIGH_LT_LOW); elig[:n_hard] |= int(EligFlag.DATA_QUALITY)
    return pd.Series(elig, index=ids), pd.Series(hard, index=ids)


def test_sanity_counts_and_attention_requires_both_conditions():
    e, h = _rows(2000, 4, n_no_price=10)                      # 4 < min_count → 不亮
    out = dg.sanity_summary(e, h, SANITY_CFG)
    assert out["evaluated"] and not out["attention"]
    assert out["universe_present"] == 2000 and out["hard_flag_count"] == 4 and out["elig"]["NO_PRICE"] == 10
    assert out["hard"]["HIGH_LT_LOW"] == 4 and out["hard_flag_ratio"] == pytest.approx(0.002)
    e, h = _rows(2000, 12)                                     # 12 ≥ 5 且 0.6% > 0.5% → 亮
    assert dg.sanity_summary(e, h, SANITY_CFG)["attention"]
    e, h = _rows(20000, 12)                                    # 12 ≥ 5 但 0.06% ≤ 0.5% → 不亮
    assert not dg.sanity_summary(e, h, SANITY_CFG)["attention"]


def test_sanity_no_data_and_no_threshold():
    e, h = _rows(0, 0, n_no_price=3)
    assert dg.sanity_summary(e, h, SANITY_CFG)["reason"] == "NO_DATA"
    e, h = _rows(10, 0)
    out = dg.sanity_summary(e, h, {})
    assert out["reason"] == "THRESHOLD_NOT_CONFIGURED" and out["hard_flag_count"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_diagnostics.py -v`
Expected: FAIL（`ModuleNotFoundError: app.mlentry.monitoring.diagnostics`）

- [ ] **Step 3: Write implementation**

`backend/configs/mlentry/monitoring.yaml` 檔尾**新增**（其他區不動）：

```yaml
diagnostics:                      # Spec A §23 只記錄不阻擋；缺鍵 → evaluated:false；attention 只換 UI 顏色
  freshness:
    daily_prices:       {mode: business_date,       max_lag_days: 0}
    market_index:       {mode: business_date,       max_lag_days: 0}
    attention_listings: {mode: ingestion_watermark, max_lag_days: 1, step: attention}   # 看上一輪已完成 pipeline，落後 1 日為常態
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

```python
# backend/app/mlentry/monitoring/diagnostics.py
"""§23 Observation diagnostics（Spec A）：只記錄、不阻擋、不改 run status。

四個診斷共用固定 envelope：
  正常     {"evaluated": True,  "attention": bool, ...values}
  無法評估 {"evaluated": False, "attention": False, "reason": "THRESHOLD_NOT_CONFIGURED"|"NO_DATA"|"NOT_APPLICABLE"}
  例外     {"evaluated": False, "attention": False, "error_type": "<ClassName>"}   ← 訊息只進 log
門檻全部來自 monitoring.yaml 的 diagnostics 區；本檔不硬寫數值。
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3

import numpy as np
import pandas as pd

from ..data.calendar import TradingCalendar
from ..data.quality import HardFlag
from ..data.universe import EligFlag
from .health import QS

log = logging.getLogger(__name__)
_P = tuple(float(q) for q in QS)          # 參考分位對應的累積機率 (0.01 … 0.99)


# ── envelope ────────────────────────────────────────────────────────────────

def envelope_ok(attention: bool, **values) -> dict:
    return {"evaluated": True, "attention": bool(attention), **values}


def envelope_skip(reason: str, **values) -> dict:
    return {"evaluated": False, "attention": False, "reason": reason, **values}


def envelope_error(exc: BaseException) -> dict:
    return {"evaluated": False, "attention": False, "error_type": type(exc).__name__}


def safe(fn, *args, **kwargs) -> dict:
    """任何例外只進 log；回傳只含 error_type。"""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:                                 # noqa: BLE001 — 診斷永不讓 run 失敗
        log.exception("diagnostic %s failed", getattr(fn, "__name__", repr(fn)))
        return envelope_error(exc)


def _r(x, nd: int = 4):
    """numpy／NaN 安全的 JSON 數值。"""
    if x is None:
        return None
    x = float(x)
    return None if not math.isfinite(x) else round(x, nd)


# ── freshness ───────────────────────────────────────────────────────────────

def _trading_lag(cal: TradingCalendar, as_of: str, d: str | None) -> int | None:
    """as_of 與 d（皆 ISO 日期）之間的交易日差；d 非交易日取其前一交易日；d 在 as_of 之後視為 0。"""
    if d is None:
        return None
    i = int(np.searchsorted(cal.dates, str(d), side="right")) - 1
    if i < 0:
        return None
    return max(0, int(cal.pos(as_of) - i))


def latest_pipeline_watermark(con, step: str) -> dict | None:
    """最近一筆已完成（status != running）且該 step 為 ok 的 pipeline_runs；現行 pipeline 尚未落地故看不到自己。"""
    try:
        rows = con.execute(
            "SELECT id, trading_date, finished_at, steps FROM pipeline_runs "
            "WHERE status != 'running' AND steps IS NOT NULL ORDER BY trading_date DESC, id DESC LIMIT 30").fetchall()
    except sqlite3.OperationalError:                          # 合成 DB 無此表：視為無水位
        return None
    for rid, tdate, fin, steps in rows:
        try:
            parsed = json.loads(steps) if isinstance(steps, str) else steps
        except ValueError:
            continue
        if any(isinstance(s, dict) and s.get("name") == step and s.get("status") == "ok" for s in parsed or []):
            return {"pipeline_run_id": int(rid), "business_date": str(tdate), "completed_at": str(fin) if fin else None}
    return None


def freshness(as_of: str, cal: TradingCalendar, business_dates: dict[str, str | None], con, cfg: dict | None) -> dict:
    """來源逐一比對；business_date 用已載入資料的最後有值日，ingestion_watermark 用 pipeline_runs 水位。"""
    if not cfg:
        return envelope_skip("THRESHOLD_NOT_CONFIGURED")
    sources, attention = {}, False
    for name, c in cfg.items():
        c = c or {}
        mode, max_lag = c.get("mode", "business_date"), c.get("max_lag_days")
        if mode == "ingestion_watermark":
            wm = latest_pipeline_watermark(con, c.get("step", name))
            lag = _trading_lag(cal, as_of, wm["business_date"]) if wm else None
            item = {"mode": mode, "watermark_pipeline_run_id": wm["pipeline_run_id"] if wm else None,
                    "watermark_business_date": wm["business_date"] if wm else None,
                    "watermark_completed_at": wm["completed_at"] if wm else None, "lag_trading_days": lag}
        else:
            d = business_dates.get(name)
            lag = _trading_lag(cal, as_of, d)
            item = {"mode": mode, "max_date": d, "lag_days": lag}
        if lag is None:
            item["attention"] = True                          # 來源缺資料本身就值得提醒
        elif max_lag is None:
            item["attention"], item["reason"] = False, "THRESHOLD_NOT_CONFIGURED"
        else:
            item["attention"] = bool(lag > max_lag)
        attention = attention or item["attention"]
        sources[name] = item
    return envelope_ok(attention, sources=sources)


# ── sanity ──────────────────────────────────────────────────────────────────

def sanity_summary(elig_row: pd.Series, hard_row: pd.Series, cfg: dict | None) -> dict:
    """結構性髒資料彙總；只有真正的 hard flag 才進 attention，停牌／歷史不足不算。"""
    e = elig_row.to_numpy().astype(np.uint16)
    h = hard_row.reindex(elig_row.index).fillna(0).to_numpy().astype(np.uint16)
    present_mask = (e & int(EligFlag.NO_PRICE)) == 0
    present = int(present_mask.sum())
    elig = {f.name: int(((e & int(f)) != 0).sum()) for f in EligFlag}
    hard = {f.name: int(((h & int(f)) != 0).sum()) for f in HardFlag}
    hard_count = int(((h != 0) & present_mask).sum())
    ratio = (hard_count / present) if present else None
    values = {"universe_present": present, "elig": elig, "hard": hard, "hard_flag_count": hard_count, "hard_flag_ratio": _r(ratio, 6)}
    if not cfg or "min_hard_flag_count" not in cfg or "max_hard_flag_ratio" not in cfg:
        return envelope_skip("THRESHOLD_NOT_CONFIGURED", **values)
    if present == 0:
        return envelope_skip("NO_DATA", **values)
    return envelope_ok(hard_count >= int(cfg["min_hard_flag_count"]) and ratio > float(cfg["max_hard_flag_ratio"]), **values)
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_diagnostics.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/mlentry/monitoring/diagnostics.py backend/configs/mlentry/monitoring.yaml backend/tests/test_mlentry_diagnostics.py
git commit -m "feat(mlentry): diagnostics——固定 envelope／safe、freshness（business_date＋pipeline watermark）、sanity 彙總；monitoring.yaml 新增 diagnostics 區

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: `feature_shift` 與 `recommendation_distribution`

**Files:**
- Modify: `backend/app/mlentry/monitoring/diagnostics.py`（檔尾）
- Test: `backend/tests/test_mlentry_diagnostics.py`（新增）

**Interfaces:**
- Consumes: `monitor_modes.load_monitor_modes` 的輸出（`modes`, `mode_source`）
- Produces:
  - `quantile_cdf_gap_7pt(ref_q: dict, x: np.ndarray) -> float | None`
  - `feature_shift(snapshot: pd.DataFrame, ref_full: dict, modes: dict[str, str], mode_source: str, cfg: dict | None) -> dict`
  - `recommendation_distribution(df: pd.DataFrame, sector_map: pd.Series, mcap: pd.Series, liquidity: pd.Series, cfg: dict | None) -> dict`（`df` 需有 `stock_id, gate_pass, recommended, recommendation_score`；`mcap`／`liquidity` 以 stock_id 為 index）

- [ ] **Step 1: Write the failing tests**

在 `tests/test_mlentry_diagnostics.py` 檔尾加：

```python
FS_CFG = {"mean_z_threshold": 3.0, "gap_threshold": 0.2, "max_features_mean_shift": 8, "max_features_gap": 8}


def _ref_from(x: np.ndarray, day_level=False):
    return {"q": {q: float(np.quantile(x, float(q))) for q in ("0.01", "0.05", "0.25", "0.5", "0.75", "0.95", "0.99")},
            "mean": float(x.mean()), "std": float(x.std()), "day_level": day_level, "missing_rate": 0.0}


def test_gap_near_zero_for_same_distribution_and_large_for_shift():
    rng = np.random.default_rng(0)
    base = rng.normal(0, 1, 20000)
    ref = _ref_from(base)
    assert dg.quantile_cdf_gap_7pt(ref["q"], rng.normal(0, 1, 3000)) < 0.05
    assert dg.quantile_cdf_gap_7pt(ref["q"], rng.normal(1, 1, 3000)) > 0.2
    assert dg.quantile_cdf_gap_7pt(ref["q"], np.array([np.nan])) is None


def test_feature_shift_skips_and_counts_and_source_label():
    rng = np.random.default_rng(1)
    base = rng.normal(0, 1, 20000)
    ref_full = {"features": {"ret_5d": _ref_from(base), "shifted": _ref_from(base), "is_attention_stock": _ref_from(rng.integers(0, 2, 20000).astype(float), day_level=True)}}
    snap = pd.DataFrame({"ret_5d": rng.normal(0, 1, 2000), "shifted": rng.normal(4, 1, 2000), "is_attention_stock": rng.integers(0, 2, 2000).astype(float)})
    modes = {"ret_5d": "continuous", "shifted": "continuous", "is_attention_stock": "skip"}
    out = dg.feature_shift(snap, ref_full, modes, "explicit", FS_CFG)
    assert out["evaluated"] and not out["attention"]                 # 1 個位移 < 8
    assert out["n_evaluated"] == 2 and out["n_mean_z_gt"] == 1 and out["n_gap_gt"] == 1
    assert out["skipped"] == ["is_attention_stock"] and out["monitor_mode_source"] == "explicit"
    assert out["top"][0]["name"] == "shifted" and out["top"][0]["quantile_cdf_gap_7pt"] > 0.2
    assert all(r["name"] != "is_attention_stock" for r in out["top"])


def test_feature_shift_reference_std_zero_and_thresholds():
    ref_full = {"features": {"const": {"q": {q: 1.0 for q in ("0.01", "0.05", "0.25", "0.5", "0.75", "0.95", "0.99")}, "mean": 1.0, "std": 0.0, "day_level": False}}}
    snap = pd.DataFrame({"const": np.ones(50)})
    out = dg.feature_shift(snap, ref_full, {"const": "continuous"}, "explicit", FS_CFG)
    row = out["top"][0]
    assert row["mean_z"] is None and row["std_ratio"] is None and row["reason"] == "REFERENCE_STD_ZERO"
    assert dg.feature_shift(snap, ref_full, {"const": "continuous"}, "explicit", {})["reason"] == "THRESHOLD_NOT_CONFIGURED"


def test_feature_shift_attention_uses_ge_count():
    rng = np.random.default_rng(2)
    base = rng.normal(0, 1, 20000)
    names = [f"f{i}" for i in range(8)]
    ref_full = {"features": {n: _ref_from(base) for n in names}}
    snap = pd.DataFrame({n: rng.normal(4, 1, 500) for n in names})       # 恰 8 個位移 → >= 8 亮
    out = dg.feature_shift(snap, ref_full, {n: "continuous" for n in names}, "explicit", FS_CFG)
    assert out["attention"] and out["n_gap_gt"] == 8


REC_CFG = {"min_recommendations_for_concentration": 3, "max_sector_share": 0.60, "min_sector_overweight": 2.0}


def _policy_df(n=100, rec_ids=(), qual_ids=()):
    ids = [f"S{i:03d}" for i in range(n)]
    return pd.DataFrame({"stock_id": ids, "gate_pass": [i in qual_ids or i in rec_ids for i in ids],
                         "recommended": [i in rec_ids for i in ids],
                         "recommendation_score": np.linspace(0, 1, n)})


def test_recommendation_distribution_sector_overweight_needs_all_three_conditions():
    df = _policy_df(100, rec_ids=("S000", "S001", "S002", "S003"), qual_ids=("S010", "S011"))
    sector = pd.Series([1.0] * 5 + [2.0] * 95, index=df["stock_id"])          # 類股 1 佔 universe 5%
    mcap = pd.Series(np.arange(100, dtype=float), index=df["stock_id"]); liq = mcap.copy()
    out = dg.recommendation_distribution(df, sector, mcap, liq, REC_CFG)
    assert out["evaluated"] and out["attention"]                            # 4 檔全在類股 1：share 1.0、overweight 20
    assert out["sector"]["max_sector"]["sector_id"] == "1" and out["sector"]["max_sector"]["overweight"] == pytest.approx(20.0)
    assert out["qualified_count"] == 6 and out["recommendation_count"] == 4
    assert out["score_q"]["p50"] == pytest.approx(np.median(df.loc[df["gate_pass"], "recommendation_score"]))
    assert out["mcap_tercile"]["mcap_basis"] == "current_company_profile_at_run_time"
    assert out["mcap_tercile"] == {**out["mcap_tercile"], "low": 4, "mid": 0, "high": 0, "missing_count": 0}
    sector_big = pd.Series([1.0] * 60 + [2.0] * 40, index=df["stock_id"])   # 類股 1 佔 60%：share 1.0 但 overweight 1.67 → 不亮
    assert not dg.recommendation_distribution(df, sector_big, mcap, liq, REC_CFG)["attention"]
    df2 = _policy_df(100, rec_ids=("S000", "S001"))                          # 只有 2 檔 < 3 → 不亮
    assert not dg.recommendation_distribution(df2, sector, mcap, liq, REC_CFG)["attention"]


def test_recommendation_distribution_edge_cases():
    df = _policy_df(10)                                                      # 無 qualified、無推薦
    sector = pd.Series(1.0, index=df["stock_id"]); mcap = pd.Series(np.nan, index=df["stock_id"])
    out = dg.recommendation_distribution(df, sector, mcap, mcap, REC_CFG)
    assert out["evaluated"] and not out["attention"]
    assert out["score_q"] is None and out["sector"]["recommended"] == {} and out["sector"]["max_sector"] is None
    assert out["mcap_tercile"]["missing_count"] == 10 and out["mcap_tercile"]["issued_shares_missing_count"] == 10
    assert dg.recommendation_distribution(df, sector, mcap, mcap, {})["reason"] == "THRESHOLD_NOT_CONFIGURED"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_diagnostics.py -v`
Expected: 新 6 個 FAIL（`AttributeError: ... has no attribute 'quantile_cdf_gap_7pt'`）

- [ ] **Step 3: Write implementation**

在 `diagnostics.py` 檔尾加：

```python
# ── feature shift ───────────────────────────────────────────────────────────

def quantile_cdf_gap_7pt(ref_q: dict, x: np.ndarray) -> float | None:
    """7-point reference-quantile ECDF gap：max_i |ECDF_now(q_i) − p_i|。不是 two-sample KS，欄名固定。"""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return None
    edges = np.array([ref_q[q] for q in QS], dtype=float)
    ecdf = np.searchsorted(np.sort(x), edges, side="right") / len(x)
    return float(np.max(np.abs(ecdf - np.array(_P))))


def feature_shift(snapshot: pd.DataFrame, ref_full: dict, modes: dict[str, str], mode_source: str, cfg: dict | None) -> dict:
    """只評估 monitor_mode == continuous；mean_z／std_ratio 零除回 None＋REFERENCE_STD_ZERO。"""
    feature_ref: dict = ref_full.get("features", {})
    cfg = cfg or {}
    z_thr, gap_thr = cfg.get("mean_z_threshold"), cfg.get("gap_threshold")
    rows, skipped = [], []
    for name, ref in feature_ref.items():
        if modes.get(name) != "continuous":
            skipped.append(name); continue
        if name not in snapshot.columns:
            continue
        x = snapshot[name].to_numpy(dtype=float)
        xf = x[np.isfinite(x)]
        row = {"name": name, "mean_z": None, "std_ratio": None, "quantile_cdf_gap_7pt": None}
        if len(xf) == 0:
            row["reason"] = "NO_DATA"; rows.append(row); continue
        std = float(ref.get("std", 0.0) or 0.0)
        if std < 1e-12:
            row["reason"] = "REFERENCE_STD_ZERO"
        else:
            row["mean_z"] = _r((xf.mean() - float(ref["mean"])) / std)
            row["std_ratio"] = _r(xf.std() / std)
        row["quantile_cdf_gap_7pt"] = _r(quantile_cdf_gap_7pt(ref["q"], xf))
        rows.append(row)
    n_z = sum(1 for r in rows if r["mean_z"] is not None and z_thr is not None and abs(r["mean_z"]) > float(z_thr))
    n_gap = sum(1 for r in rows if r["quantile_cdf_gap_7pt"] is not None and gap_thr is not None and r["quantile_cdf_gap_7pt"] > float(gap_thr))
    top = sorted(rows, key=lambda r: -(r["quantile_cdf_gap_7pt"] if r["quantile_cdf_gap_7pt"] is not None else -1.0))[:10]
    values = {"n_evaluated": len(rows), "n_mean_z_gt": n_z, "n_gap_gt": n_gap, "top": top, "skipped": skipped,
              "monitor_mode_source": mode_source}
    if any(k not in cfg for k in ("mean_z_threshold", "gap_threshold", "max_features_mean_shift", "max_features_gap")):
        return envelope_skip("THRESHOLD_NOT_CONFIGURED", **values)
    if not rows:
        return envelope_skip("NO_DATA", **values)
    return envelope_ok(n_z >= int(cfg["max_features_mean_shift"]) or n_gap >= int(cfg["max_features_gap"]), **values)


# ── recommendation distribution ─────────────────────────────────────────────

def _sector_counts(sec: np.ndarray, mask: np.ndarray) -> dict[str, int]:
    v = sec[mask]
    v = v[np.isfinite(v)]
    u, c = np.unique(v.astype(int), return_counts=True)
    return {str(int(k)): int(n) for k, n in zip(u, c)}


def _terciles(series: pd.Series, sid: pd.Series, rec_mask: np.ndarray) -> dict:
    v = series.reindex(sid.to_numpy()).to_numpy(dtype=float)
    valid = np.isfinite(v)
    out = {"low": 0, "mid": 0, "high": 0, "missing_count": int((~valid).sum()), "cuts": None}
    if valid.sum() < 3:
        return out
    lo, hi = np.quantile(v[valid], [1 / 3, 2 / 3])
    rv = v[rec_mask & valid]
    out.update({"low": int((rv <= lo).sum()), "mid": int(((rv > lo) & (rv <= hi)).sum()), "high": int((rv > hi).sum()),
                "cuts": [_r(lo, 2), _r(hi, 2)]})
    return out


def recommendation_distribution(df: pd.DataFrame, sector_map: pd.Series, mcap: pd.Series, liquidity: pd.Series, cfg: dict | None) -> dict:
    """推薦相對全 U_t 的分布：score 分位、類股集中（相對 universe 佔比）、市值／流動性三分位。只記錄。"""
    sid = df["stock_id"].astype(str)
    q_mask = df["gate_pass"].to_numpy(dtype=bool); rec_mask = df["recommended"].to_numpy(dtype=bool)
    n_q, n_rec = int(q_mask.sum()), int(rec_mask.sum())
    score_q = None
    if n_q:
        s = pd.to_numeric(df.loc[q_mask, "recommendation_score"], errors="coerce").dropna()
        if len(s):
            score_q = {"p10": _r(s.quantile(0.10)), "p50": _r(s.quantile(0.50)), "p90": _r(s.quantile(0.90))}
    sec = sector_map.reindex(sid.to_numpy()).to_numpy(dtype=float)
    uni = _sector_counts(sec, np.ones(len(df), dtype=bool)); n_u = sum(uni.values())
    universe_share = {k: _r(v / n_u) for k, v in uni.items()} if n_u else {}
    rec_counts = _sector_counts(sec, rec_mask)
    max_sector = None
    if rec_counts:
        k = max(rec_counts, key=rec_counts.get)
        rs = rec_counts[k] / n_rec; us = (uni.get(k, 0) / n_u) if n_u else 0.0
        max_sector = {"sector_id": k, "recommendation_share": _r(rs), "universe_share": _r(us),
                      "overweight": _r(rs / us) if us > 0 else None}
    mcap_t = _terciles(mcap, sid, rec_mask)
    mcap_t.update({"mcap_basis": "current_company_profile_at_run_time",
                   "issued_shares_missing_count": int(mcap.reindex(sid.to_numpy()).isna().sum())})
    values = {"qualified_count": n_q, "recommendation_count": n_rec, "score_q": score_q,
              "sector": {"recommended": rec_counts, "qualified": _sector_counts(sec, q_mask), "universe_share": universe_share,
                         "max_sector": max_sector},
              "mcap_tercile": mcap_t, "liquidity_tercile": _terciles(liquidity, sid, rec_mask)}
    cfg = cfg or {}
    if any(k not in cfg for k in ("min_recommendations_for_concentration", "max_sector_share", "min_sector_overweight")):
        return envelope_skip("THRESHOLD_NOT_CONFIGURED", **values)
    att = bool(max_sector and n_rec >= int(cfg["min_recommendations_for_concentration"])
               and max_sector["recommendation_share"] > float(cfg["max_sector_share"])
               and max_sector["overweight"] is not None and max_sector["overweight"] > float(cfg["min_sector_overweight"]))
    return envelope_ok(att, **values)
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_diagnostics.py -v`
Expected: 14 passed。若 `test_feature_shift_attention_uses_ge_count` 因隨機樣本讓某特徵 gap 恰好 ≤ 0.2 而失敗，把平移改為 `rng.normal(5, 1, 500)`（不得改 `>=` 語意）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/mlentry/monitoring/diagnostics.py backend/tests/test_mlentry_diagnostics.py
git commit -m "feat(mlentry): diagnostics——feature_shift（quantile_cdf_gap_7pt、mean_z/std_ratio、monitor_mode_source）與推薦分布（類股 overweight、市值／流動性三分位）

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `serving/audit.py`

**Files:**
- Create: `backend/app/mlentry/serving/audit.py`
- Test: `backend/tests/test_mlentry_audit.py`

**Interfaces:**
- Consumes: `fingerprint.canonical_hash`、`config.load_yaml / CONFIG_DIR`、`ServingStack.dir / policy_name / code_commit`
- Produces:
  - `serving_stack_hash(stack_dir: Path) -> str | None`
  - `config_hashes(policy_name: str, config_dir: Path = CONFIG_DIR) -> dict[str, str | None]`（鍵 `monitoring, policy, features`）
  - `runtime_info() -> dict`（`python, lightgbm, pandas, numpy, hostname`）
  - `build_audit(requested_as_of: str, feature_snapshot_as_of: str | None, stack, sources: dict[str, dict]) -> dict`
  - `safe_build_audit(...) -> dict`（例外 → `{"error_type": ...}`）
  - `AUDIT_SUMMARY_KEYS`、`audit_summary(audit: dict | None) -> dict | None`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_mlentry_audit.py
"""§22 provenance audit：canonical hash、來源 view metadata、API 白名單。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.mlentry.serving import audit


def _stack(tmp_path):
    (tmp_path / "stack.json").write_text(json.dumps({"b": 1, "a": [1, 2]}, indent=2), encoding="utf-8")
    (tmp_path / "artifacts.json").write_text(json.dumps({"target_10d": {"kind": "binary"}}), encoding="utf-8")
    return SimpleNamespace(dir=tmp_path, policy_name="policy_baseline_v1", code_commit="abc1234")


def test_serving_stack_hash_is_semantic(tmp_path):
    s = _stack(tmp_path)
    h1 = audit.serving_stack_hash(tmp_path)
    (tmp_path / "stack.json").write_text(json.dumps({"a": [1, 2], "b": 1}), encoding="utf-8")   # 重排＋去縮排
    assert audit.serving_stack_hash(tmp_path) == h1 and len(h1) == 12
    (tmp_path / "stack.json").write_text(json.dumps({"a": [1, 3], "b": 1}), encoding="utf-8")
    assert audit.serving_stack_hash(tmp_path) != h1
    assert audit.serving_stack_hash(tmp_path / "nope") is None
    assert s.policy_name == "policy_baseline_v1"


def test_config_hashes_real_configs_are_stable_12hex():
    h = audit.config_hashes("policy_baseline_v1")
    assert set(h) == {"monitoring", "policy", "features"} and all(len(v) == 12 for v in h.values())
    assert audit.config_hashes("no_such_policy")["policy"] is None


def test_build_audit_shape_and_snapshot_id(tmp_path):
    s = _stack(tmp_path)
    sources = {"daily_prices": {"max_business_date": "2026-09-29", "max_available_at": None, "rows_visible_at_as_of": 1783},
               "attention_listings": {"max_business_date": "2026-09-24", "max_available_at": "2026-09-28 21:49:00", "rows_visible_at_as_of": 163}}
    a = audit.build_audit("2026-09-29", "2026-09-29", s, sources)
    assert set(a) == {"requested_as_of", "feature_snapshot_as_of", "data_snapshot_id", "sources", "serving_stack_hash",
                      "config_hashes", "runtime", "code_commit"}
    assert a["sources"]["daily_prices"]["max_available_at"] is None
    assert a["data_snapshot_id"] == audit.build_audit("2026-09-29", "2026-09-29", s, json.loads(json.dumps(sources)))["data_snapshot_id"]
    sources["daily_prices"]["rows_visible_at_as_of"] = 1784
    assert a["data_snapshot_id"] != audit.build_audit("2026-09-29", "2026-09-29", s, sources)["data_snapshot_id"]
    assert {"python", "lightgbm", "pandas", "numpy", "hostname"} <= set(a["runtime"])


def test_audit_summary_whitelist_and_mismatch():
    full = {"requested_as_of": "2026-09-29", "feature_snapshot_as_of": "2026-09-28", "data_snapshot_id": "x", "serving_stack_hash": "y",
            "code_commit": "z", "runtime": {"hostname": "SECRET-HOST"}, "sources": {}, "config_hashes": {}}
    s = audit.audit_summary(full)
    assert s == {"requested_as_of": "2026-09-29", "feature_snapshot_as_of": "2026-09-28", "data_snapshot_id": "x",
                 "serving_stack_hash": "y", "code_commit": "z", "as_of_mismatch": True}
    assert "SECRET-HOST" not in json.dumps(s)
    assert audit.audit_summary(None) is None
    assert audit.audit_summary({"error_type": "OSError"})["as_of_mismatch"] is False


def test_safe_build_audit_returns_error_type_only(caplog):
    with caplog.at_level("ERROR"):
        out = audit.safe_build_audit("2026-09-29", "2026-09-29", SimpleNamespace(dir=None, policy_name="p", code_commit="c"), {})
    assert out == {"error_type": "TypeError"}                    # Path(None) 拋 TypeError；訊息只在 log
    assert any("build_audit failed" in m for m in caplog.messages)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_audit.py -v`
Expected: FAIL（`ImportError: cannot import name 'audit'`）

- [ ] **Step 3: Write implementation**

```python
# backend/app/mlentry/serving/audit.py
"""§22 run provenance（Spec A §3）：「這次 run 用了什麼」。與 diagnostics（有沒有異常）分開存。

data_snapshot_id 是 run 實際可見 source view 的指紋，只供比對，不宣稱可重建資料。
完整內容存 MLEntryRun.audit_json；API 只透出 audit_summary() 白名單。
"""

from __future__ import annotations

import json
import logging
import platform
import socket
from pathlib import Path

from ..config import CONFIG_DIR, load_yaml
from ..fingerprint import canonical_hash

log = logging.getLogger(__name__)

AUDIT_SUMMARY_KEYS = ("requested_as_of", "feature_snapshot_as_of", "data_snapshot_id", "serving_stack_hash", "code_commit")
_SOURCE_KEYS = ("max_business_date", "max_available_at", "rows_visible_at_as_of")


def serving_stack_hash(stack_dir: Path) -> str | None:
    """stack.json＋artifacts.json 解析後的 canonical hash（縮排／key 順序不影響）。"""
    parts = []
    for name in ("stack.json", "artifacts.json"):
        p = Path(stack_dir) / name
        if not p.exists():
            return None
        parts.append(json.loads(p.read_text(encoding="utf-8")))
    return canonical_hash(parts)


def config_hashes(policy_name: str, config_dir: Path = CONFIG_DIR) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for key, name in (("monitoring", "monitoring"), ("policy", policy_name), ("features", "features")):
        try:
            out[key] = canonical_hash(load_yaml(name, config_dir))
        except (OSError, ValueError):
            out[key] = None
    return out


def runtime_info() -> dict:
    import lightgbm, numpy, pandas  # noqa: E401
    return {"python": platform.python_version(), "lightgbm": lightgbm.__version__, "pandas": pandas.__version__,
            "numpy": numpy.__version__, "hostname": socket.gethostname()}


def build_audit(requested_as_of: str, feature_snapshot_as_of: str | None, stack, sources: dict[str, dict]) -> dict:
    src = {name: {k: (s or {}).get(k) for k in _SOURCE_KEYS} for name, s in sources.items()}
    return {"requested_as_of": str(requested_as_of), "feature_snapshot_as_of": feature_snapshot_as_of,
            "data_snapshot_id": canonical_hash(src), "sources": src,
            "serving_stack_hash": serving_stack_hash(stack.dir), "config_hashes": config_hashes(stack.policy_name),
            "runtime": runtime_info(), "code_commit": stack.code_commit}


def safe_build_audit(requested_as_of, feature_snapshot_as_of, stack, sources) -> dict:
    try:
        return build_audit(requested_as_of, feature_snapshot_as_of, stack, sources)
    except Exception as exc:                                  # noqa: BLE001 — audit 永不讓 run 失敗
        log.exception("build_audit failed")
        return {"error_type": type(exc).__name__}


def audit_summary(audit: dict | None) -> dict | None:
    if not audit:
        return None
    out = {k: audit.get(k) for k in AUDIT_SUMMARY_KEYS}
    a, b = out["requested_as_of"], out["feature_snapshot_as_of"]
    out["as_of_mismatch"] = bool(a and b and a != b)
    return out
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_audit.py -v`
Expected: 5 passed（`test_safe_build_audit_returns_error_type_only` 第二段：`Path(None)` 拋 `TypeError`）

- [ ] **Step 5: Commit**

```bash
git add backend/app/mlentry/serving/audit.py backend/tests/test_mlentry_audit.py
git commit -m "feat(mlentry): audit——serving stack／config canonical hash、source view 指紋 data_snapshot_id、API 白名單 audit_summary

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: `daily_run` 整合＋`audit_json` 欄位＋gate 等價基準

**Files:**
- Modify: `backend/app/storage/models.py`（`MLEntryRun.log_tail` 之後）、`backend/app/storage/database.py`（`_COLUMN_ADDITIONS`）
- Modify: `backend/app/mlentry/serving/daily_run.py`
- Create: `backend/tests/fixtures/mlentry_gates_baseline.json`（Step 1 產生）
- Test: `backend/tests/test_mlentry_serving.py`（新增）

**Interfaces:**
- Consumes: Task 2 `load_monitor_modes`、Task 4/5 `diagnostics.*`、Task 6 `audit.safe_build_audit`
- Produces: `MLEntryRun.audit_json: str | None`；`health_json["diagnostics"]` 四鍵 `freshness / sanity / feature_shift / recommendation`；`RunResult.health` 亦含 `diagnostics`

- [ ] **Step 1: 先產生 gate 等價基準（改 daily_run 之前）**

在 `tests/test_mlentry_serving.py` 檔尾加：

```python
GATE_KEYS = ("data_quality", "feature_health", "prediction_health", "recommendation")
BASELINE = Path(__file__).parent / "fixtures" / "mlentry_gates_baseline.json"


def _gates_canonical(health: dict) -> str:
    from app.mlentry.fingerprint import canonical_json
    return canonical_json({k: health.get(k) for k in GATE_KEYS})


def test_four_health_gates_unchanged_by_observation_layer(env):
    """Spec A 規則 1：加 diagnostics／audit 前後，四個 gate 子樹語意與內容完全相同（canonical JSON 比對）。"""
    con, Session, cfg, dates = env["con"], env["Session"], env["cfg"], env["dates"]
    with Session() as session:
        r = daily_run.run_daily(con, session, dates[60], cfg=cfg, monitoring=_lenient_mon())
    got = _gates_canonical(r.health)
    if not BASELINE.exists():                                     # 只在基準尚不存在時寫入（改 daily_run 前跑一次）
        BASELINE.parent.mkdir(exist_ok=True)
        BASELINE.write_text(got, encoding="utf-8")
    assert got == BASELINE.read_text(encoding="utf-8")
```

並在檔頭 import 區加 `from pathlib import Path`。

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_serving.py::test_four_health_gates_unchanged_by_observation_layer -v`
Expected: PASS，且 `backend/tests/fixtures/mlentry_gates_baseline.json` 被建立。用 `git add` 納入。再跑一次確認仍 PASS（決定性：合成資料 `default_rng(1)`、LightGBM `n_jobs=1`）。

- [ ] **Step 2: Write the failing integration tests**

在 `tests/test_mlentry_serving.py` 檔尾再加：

```python
def test_daily_run_writes_diagnostics_and_audit(env):
    con, Session, cfg, dates = env["con"], env["Session"], env["cfg"], env["dates"]
    with Session() as session:
        r = daily_run.run_daily(con, session, dates[60], cfg=cfg, monitoring=_lenient_mon())
        from app.storage import models
        row = session.query(models.MLEntryRun).filter_by(run_id=r.run_id).one()
    health = json.loads(row.health_json)
    diag = health["diagnostics"]
    assert set(diag) == {"freshness", "sanity", "feature_shift", "recommendation"}
    for v in diag.values():
        assert {"evaluated", "attention"} <= set(v)
    assert diag["feature_shift"]["monitor_mode_source"] == "explicit"          # 新 stack 原生 monitor_mode
    assert diag["sanity"]["evaluated"] and diag["freshness"]["sources"]["daily_prices"]["lag_days"] == 0
    a = json.loads(row.audit_json)
    assert a["requested_as_of"] == dates[60] and a["feature_snapshot_as_of"] == dates[60]
    assert len(a["data_snapshot_id"]) == 12 and a["serving_stack_hash"] and "daily_prices" in a["sources"]
    assert a["sources"]["daily_prices"]["rows_visible_at_as_of"] >= r.universe_count       # 有價格檔數 ≥ eligible 檔數


def test_diagnostic_exception_never_changes_status_or_leaks_message(env, monkeypatch):
    from app.mlentry.monitoring import diagnostics
    con, Session, cfg, dates = env["con"], env["Session"], env["cfg"], env["dates"]

    def boom(*a, **k):
        raise ValueError("secret path C:/db")
    monkeypatch.setattr(diagnostics, "feature_shift", boom)
    with Session() as session:
        r = daily_run.run_daily(con, session, dates[61], cfg=cfg, monitoring=_lenient_mon())
        from app.storage import models
        row = session.query(models.MLEntryRun).filter_by(run_id=r.run_id).one()
    assert r.status in ("OK", "NO_TRADE")
    d = json.loads(row.health_json)["diagnostics"]["feature_shift"]
    assert d == {"evaluated": False, "attention": False, "error_type": "ValueError"}
    assert "secret path" not in row.health_json
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_serving.py -v -k "diagnostics_and_audit or never_changes_status"`
Expected: 2 FAIL（`KeyError: 'diagnostics'`）

- [ ] **Step 3: Write implementation**

`models.py`，`MLEntryRun` 內 `log_tail` 那行之後加：

```python
    audit_json: Mapped[str | None] = mapped_column(Text)          # Spec A §3 provenance（API 只透出白名單）
```

`database.py` `_COLUMN_ADDITIONS` 加一項：

```python
    "mlentry_runs": {"audit_json": "TEXT"},                       # Spec A：既有 SQLite 補欄
```

`daily_run.py`：

1. import 區加：
```python
from ..data import prices
from ..monitoring import diagnostics
from ..monitoring.monitor_modes import load_monitor_modes
from . import audit
```
2. 把 `eligible, elig_flags = build_universe(m, cfg.universe)` 保留（已有 `elig_flags`）。
3. 在 `gates["recommendation"] = health.recommendation_drift(...)` 之後、`# 5. Immutable run` 之前插入：

```python
    # 4b. Observation diagnostics（§23 只記錄）與 provenance audit（§22）——policy 之後；例外只寫 envelope，永不改 status
    diag_cfg = mon.get("diagnostics") or {}
    modes, mode_src = load_monitor_modes(stack.dir, ref)
    close_last = m["close"].loc[as_of]
    shares = _issued_shares(con)
    mcap = (close_last * shares.reindex(close_last.index)).rename("mcap")
    liquidity = m["turnover"].iloc[-20:].mean(axis=0)
    hard_row = quality.hard_flags({k: v.iloc[[-1]] for k, v in m.items()}).iloc[0]
    business_dates = {"daily_prices": _last_valid_date(m["close"]), "market_index": _last_valid_date(mkt)}
    gates["diagnostics"] = {
        "freshness": diagnostics.safe(diagnostics.freshness, as_of, cal, business_dates, con, diag_cfg.get("freshness")),
        "sanity": diagnostics.safe(diagnostics.sanity_summary, elig_flags.loc[as_of], hard_row, diag_cfg.get("sanity")),
        "feature_shift": diagnostics.safe(diagnostics.feature_shift, snap, ref, modes, mode_src, diag_cfg.get("feature_shift")),
        "recommendation": diagnostics.safe(diagnostics.recommendation_distribution, df, sector, mcap, liquidity,
                                           diag_cfg.get("recommendation")),
    }
    audit_doc = audit.safe_build_audit(as_of, str(ctx.calendar.dates[-1]), stack,
                                       _source_views(con, as_of, m, mkt, gates["diagnostics"]["freshness"]))
```

4. `run_row` 加 `"audit_json": json.dumps(audit_doc, ensure_ascii=False, default=str),`（放在 `"log_tail": None,` 之後）。
5. 檔尾（`_f` 之後）加 helpers：

```python
def _last_valid_date(x) -> str | None:
    has = x.notna().any(axis=1) if isinstance(x, pd.DataFrame) else x.notna()
    idx = has[has].index
    return str(idx[-1]) if len(idx) else None


def _issued_shares(con) -> pd.Series:
    """run 當下的 company_profile 股本（非 PIT，僅供診斷）；缺表回空 Series。"""
    try:
        df = pd.read_sql_query("SELECT stock_id, issued_shares FROM company_profile", con)
    except Exception:                                          # noqa: BLE001 — 合成 DB 無此表
        return pd.Series(dtype=float)
    return pd.Series(pd.to_numeric(df["issued_shares"], errors="coerce").to_numpy(), index=df["stock_id"].astype(str)).replace(0, np.nan)


def _source_views(con, as_of: str, m: dict, mkt: pd.Series, fresh: dict) -> dict[str, dict]:
    """run 實際可見的 source view metadata（PIT 語意）；無 available_at 語意者填 None。"""
    close = m["close"]
    wm = ((fresh.get("sources") or {}).get("attention_listings") or {}) if isinstance(fresh, dict) else {}
    try:
        win = prices.load_attention_windows(con)
        seen = win[win["date"].astype(str) <= as_of]
        att = {"max_business_date": str(seen["date"].max()) if len(seen) else None,
               "max_available_at": wm.get("watermark_completed_at"), "rows_visible_at_as_of": int(len(seen))}
    except Exception:                                          # noqa: BLE001 — 來源缺表：記 None，不拋
        att = {"max_business_date": None, "max_available_at": None, "rows_visible_at_as_of": None}
    return {"daily_prices": {"max_business_date": _last_valid_date(close), "max_available_at": None,
                             "rows_visible_at_as_of": int(close.loc[as_of].notna().sum())},
            "market_index": {"max_business_date": _last_valid_date(mkt), "max_available_at": None,
                             "rows_visible_at_as_of": int(mkt.notna().sum())},
            "attention_listings": att}
```

注意：測試 env 的合成 DB 可能沒有 `company_profile`／`attention_listings`／`pipeline_runs` 表；`_issued_shares`、`_source_views` 已各自防禦，`latest_pipeline_watermark`（Task 4）對缺表回 `None`。

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_serving.py tests/test_mlentry_diagnostics.py tests/test_mlentry_audit.py -v`
Expected: 全數 PASS，含 `test_four_health_gates_unchanged_by_observation_layer` 仍與基準相同。

再跑真實 DB 冒煙（不寫入）：`.venv/Scripts/python.exe -c "from app.storage.database import init_db; init_db(); import sqlite3; from app.config import get_settings; c=sqlite3.connect(str(get_settings().db_path)); print([r[1] for r in c.execute('pragma table_info(mlentry_runs)') if r[1]=='audit_json'])"`
Expected: `['audit_json']`（既有 SQLite 已補欄）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/storage/models.py backend/app/storage/database.py backend/app/mlentry/serving/daily_run.py backend/app/mlentry/monitoring/diagnostics.py backend/tests/test_mlentry_serving.py backend/tests/fixtures/mlentry_gates_baseline.json
git commit -m "feat(mlentry): daily_run 於 policy 後掛四項診斷與 audit（safe envelope、永不改 status）；MLEntryRun.audit_json；四 gate canonical 基準測試

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: API——`RunInfo.diagnostics / audit`、history `attention_count`

**Files:**
- Modify: `backend/app/api/routes_mlentry.py`
- Test: `backend/tests/test_mlentry_api.py`

**Interfaces:**
- Consumes: `audit.audit_summary`
- Produces: `RunInfo.diagnostics: dict | None`（原樣，但去除任何 `error`/`message` 鍵）、`RunInfo.audit: dict | None`（AuditSummary）；`/health.history[].attention_count: int | None`

- [ ] **Step 1: Write the failing tests**

在 `tests/test_mlentry_api.py` fixture 的 `health_json=json.dumps({...})` 內加一鍵 `"diagnostics": {"freshness": {"evaluated": True, "attention": True, "sources": {}}, "sanity": {"evaluated": False, "attention": False, "reason": "NO_DATA"}, "feature_shift": {"evaluated": False, "attention": False, "error_type": "ValueError", "error": "should be stripped"}, "recommendation": {"evaluated": True, "attention": False}}`，並在 `MLEntryRun(...)` 加 `audit_json=json.dumps({"requested_as_of": "2019-01-02", "feature_snapshot_as_of": "2019-01-02", "data_snapshot_id": "abcdef012345", "serving_stack_hash": "0123456789ab", "code_commit": "abc", "runtime": {"hostname": "SECRET-HOST"}, "sources": {}, "config_hashes": {}})`。

檔尾加：

```python
def test_run_info_diagnostics_and_audit_whitelist(client, monkeypatch):
    monkeypatch.setattr(routes_mlentry, "load_champion", lambda: None)
    j = client.get(f"/api/mlentry/board?signal_date={SIGNAL.isoformat()}").json()
    run = j["run"]
    assert set(run["diagnostics"]) == {"freshness", "sanity", "feature_shift", "recommendation"}
    assert run["diagnostics"]["feature_shift"] == {"evaluated": False, "attention": False, "error_type": "ValueError"}
    assert run["audit"] == {"requested_as_of": "2019-01-02", "feature_snapshot_as_of": "2019-01-02", "data_snapshot_id": "abcdef012345",
                            "serving_stack_hash": "0123456789ab", "code_commit": "abc", "as_of_mismatch": False}
    assert "SECRET-HOST" not in json.dumps(j) and "should be stripped" not in json.dumps(j)


def test_health_history_attention_count(client, monkeypatch):
    monkeypatch.setattr(routes_mlentry, "load_champion", lambda: None)
    j = client.get("/api/mlentry/health?limit=5").json()
    h = next(x for x in j["history"] if x["signal_date"] == SIGNAL.isoformat())
    assert h["attention_count"] == 1


def test_legacy_run_without_diagnostics_or_audit(client, monkeypatch):
    from app.storage.database import session_scope
    rid = "2019-01-03_legacy_000000000000"
    with session_scope() as s:
        s.merge(models.MLEntryRun(run_id=rid, signal_date=date(2019, 1, 3), as_of_timestamp=datetime(2019, 1, 3, 21, 30),
                                  dataset_version="ds_t", universe_version="u", feature_version="f", label_version="l",
                                  model_version="test_stack", calibration_version="c", policy_version="p", policy_name="policy_baseline_v1",
                                  model_status="RESEARCH_SHADOW", deployment_mode="SHADOW", promotion_eligible=False, code_commit="abc",
                                  status="NO_TRADE", no_trade=True, no_trade_reason="POLICY_NO_CANDIDATE", universe_count=3,
                                  qualified_count=0, recommendation_count=0, health_json=json.dumps({"data_quality": {"ok": True}})))
    try:
        monkeypatch.setattr(routes_mlentry, "load_champion", lambda: None)
        r = client.get(f"/api/mlentry/runs/{rid}"); assert r.status_code == 200
        assert r.json()["diagnostics"] is None and r.json()["audit"] is None
        j = client.get("/api/mlentry/health?limit=5").json()
        assert next(x for x in j["history"] if x["signal_date"] == "2019-01-03")["attention_count"] is None
    finally:
        with session_scope() as s:
            s.query(models.MLEntryRun).filter_by(run_id=rid).delete()
```

（檔頭已有 `date`、`datetime`、`json`、`models` import；若無則補。）

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_api.py -v`
Expected: 新 3 個 FAIL（`KeyError: 'diagnostics'` / `'attention_count'`）

- [ ] **Step 3: Write implementation**

`routes_mlentry.py`：

1. import 加 `from ..mlentry.serving.audit import audit_summary`。
2. `RunInfo` 加兩欄：
```python
    diagnostics: dict | None
    audit: dict | None
```
3. 在 `_n_drifted` 之後加：
```python
_STRIP = ("error", "message", "traceback")


def _strip_messages(obj):
    """防呆：診斷 JSON 只允許 error_type，任何 error/message 鍵一律移除後才出 API。"""
    if isinstance(obj, dict):
        return {k: _strip_messages(v) for k, v in obj.items() if k not in _STRIP}
    if isinstance(obj, list):
        return [_strip_messages(v) for v in obj]
    return obj


def _diagnostics(health: dict) -> dict | None:
    d = health.get("diagnostics")
    return _strip_messages(d) if isinstance(d, dict) else None


def _attention_count(r: models.MLEntryRun) -> int | None:
    try:
        d = (json.loads(r.health_json) if r.health_json else {}).get("diagnostics")
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    return sum(1 for v in d.values() if isinstance(v, dict) and v.get("attention") is True)


def _audit(r: models.MLEntryRun) -> dict | None:
    try:
        return audit_summary(json.loads(r.audit_json)) if getattr(r, "audit_json", None) else None
    except Exception:
        return None
```
4. `_run_info` 的 `RunInfo(...)` 呼叫加 `diagnostics=_diagnostics(health), audit=_audit(r),`；並把 `health=` 白名單推導式的條件改為 `for k, v in health.items() if isinstance(v, dict) and k != "diagnostics"`（診斷只走 `diagnostics` 欄，不混進 gate `health`）。
5. `/health` 的 `hist.append({...})` 加 `"attention_count": _attention_count(r)`。

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mlentry_api.py tests/test_mlentry_serving.py -q`
Expected: 全數 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes_mlentry.py backend/tests/test_mlentry_api.py
git commit -m "feat(mlentry): API——RunInfo.diagnostics（去 message）與 audit 白名單摘要；history attention_count；舊 run 回 null

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: 前端——型別＋系統狀態頁「觀測診斷」區、audit 摘要、提醒數欄

**Files:**
- Modify: `frontend/src/api/client.ts`（`MLEntryRun` 與 `MLEntryHealth.history`）
- Modify: `frontend/src/components/MLEntrySystem.tsx`

**Interfaces:**
- Consumes: Task 8 的 JSON 形狀
- Produces: `MLEntryDiagnostic`、`MLEntryDiagnostics`、`MLEntryAuditSummary` 型別；`MLEntryRun.diagnostics/audit`；`history[].attention_count`

- [ ] **Step 1: 型別**

`client.ts`，在 `export type MLEntryRun = {` 之前加：

```ts
export type MLEntryDiagnostic = {
  evaluated: boolean; attention: boolean; reason?: string; error_type?: string;
  [k: string]: unknown;
};
export type MLEntryDiagnostics = {
  freshness: MLEntryDiagnostic; sanity: MLEntryDiagnostic; feature_shift: MLEntryDiagnostic; recommendation: MLEntryDiagnostic;
};
export type MLEntryAuditSummary = {
  requested_as_of: string | null; feature_snapshot_as_of: string | null; data_snapshot_id: string | null;
  serving_stack_hash: string | null; code_commit: string | null; as_of_mismatch: boolean;
};
```

`MLEntryRun` 加 `diagnostics: MLEntryDiagnostics | null; audit: MLEntryAuditSummary | null;`。
`MLEntryHealth.history` 元素型別加 `attention_count: number | null;`。

- [ ] **Step 2: 系統狀態頁**

`MLEntrySystem.tsx`：

1. import 改為 `import type { MLEntryDiagnostic, MLEntryDiagnostics, MLEntryHealth as HealthT, MLEntryStatus } from "../api/client";`
2. 在 `Row` 元件之後加：

```tsx
// 觀測診斷（Spec A）：只記錄、不影響出單；attention 只換顏色。數字與判定全來自後端 envelope。
const fmtPct = (v: unknown, d = 1) => (typeof v === "number" ? `${(v * 100).toFixed(d)}%` : "—");
const fmtNum = (v: unknown, d = 2) => (typeof v === "number" ? v.toFixed(d) : "—");

function diagTone(d: MLEntryDiagnostic | undefined): { dot: string; label: string } {
  if (!d || !d.evaluated) return { dot: "bg-gray-600", label: d?.error_type ? `未評估（${d.error_type}）` : d?.reason === "THRESHOLD_NOT_CONFIGURED" ? "未設門檻" : "未評估" };
  return d.attention ? { dot: "bg-amber-400", label: "提醒" } : { dot: "bg-emerald-700", label: "正常" };
}

const DIAG_VIEW: { key: keyof MLEntryDiagnostics; title: string; sub?: string; rows: (d: MLEntryDiagnostic) => [string, string][] }[] = [
  { key: "freshness", title: "資料新鮮度", rows: (d) => Object.entries((d.sources ?? {}) as Record<string, Record<string, unknown>>).map(([n, s]) =>
      [n, s.mode === "ingestion_watermark"
        ? `落後 ${fmtNum(s.lag_trading_days, 0)} 日（pipeline #${s.watermark_pipeline_run_id ?? "—"} ${s.watermark_business_date ?? "—"}）`
        : `落後 ${fmtNum(s.lag_days, 0)} 日（${s.max_date ?? "—"}）`]) },
  { key: "sanity", title: "結構檢查", rows: (d) => [
      ["有價格檔數", fmtNum(d.universe_present, 0)],
      ["結構性髒資料", `${fmtNum(d.hard_flag_count, 0)}（${fmtPct(d.hard_flag_ratio, 2)}）`],
      ["缺價格／歷史不足", `${fmtNum((d.elig as Record<string, unknown> | undefined)?.NO_PRICE, 0)}／${fmtNum((d.elig as Record<string, unknown> | undefined)?.HISTORY_TOO_SHORT, 0)}`]] },
  { key: "feature_shift", title: "特徵分布位移", sub: "7-point reference-quantile ECDF gap（非 KS）", rows: (d) => [
      ["評估特徵數", `${fmtNum(d.n_evaluated, 0)}（略過 ${Array.isArray(d.skipped) ? d.skipped.length : "—"}）`],
      ["|mean z| 超門檻", fmtNum(d.n_mean_z_gt, 0)],
      ["ECDF gap 超門檻", fmtNum(d.n_gap_gt, 0)],
      ["最大 gap", (() => { const t = (d.top as { name: string; quantile_cdf_gap_7pt: number | null }[] | undefined)?.[0];
        return t ? `${t.name} ${fmtNum(t.quantile_cdf_gap_7pt, 3)}` : "—"; })()],
      ["模式來源", String(d.monitor_mode_source ?? "—")]] },
  { key: "recommendation", title: "推薦分布", sub: "市值＝run 當下股本×收盤，非 PIT", rows: (d) => {
      const ms = d.sector && (d.sector as Record<string, unknown>).max_sector as Record<string, unknown> | null | undefined;
      const mc = d.mcap_tercile as Record<string, unknown> | undefined; const lq = d.liquidity_tercile as Record<string, unknown> | undefined;
      const sq = d.score_q as Record<string, unknown> | null | undefined;
      return [
        ["通過 Gate／推薦", `${fmtNum(d.qualified_count, 0)}／${fmtNum(d.recommendation_count, 0)}`],
        ["Score p10／50／90", sq ? `${fmtNum(sq.p10, 3)}／${fmtNum(sq.p50, 3)}／${fmtNum(sq.p90, 3)}` : "—"],
        ["最大類股", ms ? `#${ms.sector_id} 佔 ${fmtPct(ms.recommendation_share, 0)}（universe ${fmtPct(ms.universe_share, 1)}，${fmtNum(ms.overweight, 1)}×）` : "—"],
        ["市值 低／中／高", mc ? `${fmtNum(mc.low, 0)}／${fmtNum(mc.mid, 0)}／${fmtNum(mc.high, 0)}（缺 ${fmtNum(mc.missing_count, 0)}）` : "—"],
        ["流動性 低／中／高", lq ? `${fmtNum(lq.low, 0)}／${fmtNum(lq.mid, 0)}／${fmtNum(lq.high, 0)}` : "—"]];
    } },
];
```

3. 在 run 敘事 `<section>` 內、`run_id ...` 那行之後加 audit 摘要：

```tsx
        {r?.audit && (
          <p className="mt-1 font-mono text-[11px] text-gray-500">
            snapshot {r.audit.data_snapshot_id ?? "—"}・stack {r.audit.serving_stack_hash ?? "—"}・commit {r.audit.code_commit ?? "—"}
            {r.audit.as_of_mismatch && <span className="ml-2 text-rose-300">特徵快照日 {r.audit.feature_snapshot_as_of} ≠ 請求日 {r.audit.requested_as_of}</span>}
          </p>
        )}
```

4. 在四張 gate 卡 `</section>` 之後、「近 60 日 run 歷史」之前加：

```tsx
      <section>
        <h2 className="mb-2 text-base font-semibold">觀測診斷 <span className="text-xs font-normal text-gray-500">只記錄，不影響出單</span></h2>
        {!r?.diagnostics ? (
          <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-3 text-sm text-gray-500">此 run 無診斷紀錄（舊版 run）。</div>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {DIAG_VIEW.map((dv) => {
              const d = r.diagnostics?.[dv.key]; const tone = diagTone(d);
              return (
                <div key={dv.key} className={`rounded-lg border bg-gray-900/60 p-3 ${d?.attention ? "border-amber-700/70" : "border-gray-800"}`}>
                  <div className="flex items-center gap-2 text-sm">
                    <span className={`inline-block h-2.5 w-2.5 rounded-full ${tone.dot}`} />
                    <span className="font-semibold">{dv.title}</span>
                    <span className={`text-xs ${d?.attention ? "text-amber-300" : "text-gray-500"}`}>{tone.label}</span>
                  </div>
                  {dv.sub && <div className="mt-0.5 text-[11px] text-gray-500">{dv.sub}</div>}
                  {d && <div className="mt-1">{dv.rows(d).map(([k, val]) => <Row key={k} k={k} v={val} />)}</div>}
                </div>
              );
            })}
          </div>
        )}
      </section>
```

5. run 歷史表：表頭 `漂移特徵數` 之後加 `<th className="px-3 py-2 text-right">提醒數</th>`；每列在 `n_drifted` 那格之後加：
```tsx
                  <td className={`px-3 py-2 text-right ${h.attention_count ? "text-amber-300" : "text-gray-400"}`}>{h.attention_count ?? "—"}</td>
```

- [ ] **Step 3: Build**

Run（`frontend/`）：`npm run build`
Expected: 成功（tsc 無錯）。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/components/MLEntrySystem.tsx
git commit -m "feat(level1-ui): 系統狀態頁觀測診斷區（只記錄、attention 琥珀）、audit 摘要行、run 歷史提醒數欄

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: 實機驗證與收尾

**Files:**
- 無新檔（發現 bug 修在對應 Task 的檔案並另 commit）

- [ ] **Step 1: 全部後端測試＋freeze 檢查**

Run（`backend/`）：`.venv/Scripts/python.exe -m pytest tests/test_mlentry_fingerprint.py tests/test_mlentry_monitor_modes.py tests/test_mlentry_diagnostics.py tests/test_mlentry_audit.py tests/test_mlentry_serving.py tests/test_mlentry_api.py tests/test_mlentry_labels.py tests/test_mlentry_tracking.py tests/test_mlentry_performance_ui.py -q`
Expected: 全數 PASS。

Run（repo 根）：`git diff develop..HEAD --stat -- backend/configs backend/app/mlentry/labels backend/app/mlentry/models backend/app/mlentry/recommendation backend/app/mlentry/features`
Expected: 只有 `backend/configs/mlentry/monitoring.yaml`（且 `git diff develop..HEAD -- backend/configs/mlentry/monitoring.yaml` 只有新增行、無刪除／修改行）。

Run：`sha256sum backend/data/mlentry/serving/mlentry_lgbm_6613b41a_20250814/*` 與 Task 3 Step 4 存的清單比對
Expected: 除 `feature_reference.monitoring.json` 外全同。

- [ ] **Step 2: 產生一筆真實 run 並檢查 JSON**

用 `backend-verify`（:8001）或直接 Python 跑一次 `run_daily`（會新增一筆 immutable run，屬正常每日行為；signal_date 用最新交易日）：

```bash
cd backend && .venv/Scripts/python.exe -c "import sqlite3,json;from app.config import get_settings;from app.storage.database import init_db,session_scope;from app.mlentry.serving.daily_run import run_daily;init_db();con=sqlite3.connect(str(get_settings().db_path))
with session_scope() as s: r=run_daily(con,s)
print(r.run_id,r.status);print(json.dumps(r.health['diagnostics'],ensure_ascii=False)[:1500])"
```
Expected: status 與當日既有 run 相同；`diagnostics.feature_shift.monitor_mode_source == "explicit"`（sidecar 已產生）、`freshness.sources.attention_listings.lag_trading_days == 1`、`sanity.hard_flag_count` 小、`recommendation.mcap_tercile.mcap_basis` 正確。`audit_json` 的 `serving_stack_hash`、`data_snapshot_id` 為 12 hex。

- [ ] **Step 3: 前端建置＋瀏覽器**

`npm run build` → `preview_start` name `backend-verify` → 開 `http://localhost:8001/app/level1` → 系統狀態頁：
- 四張 gate 卡不變；下方「觀測診斷」四卡顯示數字，燈號灰／琥珀／暗綠；特徵卡副標「7-point reference-quantile ECDF gap（非 KS）」；推薦卡副標「市值＝run 當下股本×收盤，非 PIT」。
- run 敘事下有 `snapshot …・stack …・commit …`。
- run 歷史多欄「提醒數」，舊 run 顯示「—」。
- 健康條四燈號與判讀句與改前相同（attention 不影響）。
- `read_console_messages` 無錯誤；360px 無頁面橫向溢出；截圖留證。

- [ ] **Step 4: 回報**

附截圖、真實 run 的診斷 JSON 摘要、freeze 檢查結果；與 spec 不符處列出並修正（各自 commit）。
