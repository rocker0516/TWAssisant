# Level 1 v3 Research Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建好 v3 目標（隔日開盤進場淨報酬）、五族新特徵、q25 評估指標與 walk-forward，跑完「基準線 → 逐族 ablation」並產出可稽核的結果 JSON 與 findings 文件。

**Architecture:** 沿用 Level 1 的矩陣制研究管線（index=date str, columns=stock_id）：`targets_v3` 由 open/close 產 Entry／fill／R_net；`features_v3` 按角色把特徵分成 `ranked`（橫斷面 rank、缺值 0.5）與 `raw`（原始值、缺值 NaN）兩組裝進 `FeatureSet`；`evaluation_v3` 量 Top-K 淨報酬、q25 校準、「不進場」訊號；`walkforward.walk_forward_scores_v3` 沿用 embargo=horizon 硬規則。兩支 script：`level1_targets_v3`（產快取 pkl）、`level1_v3_ablation`（基準線＋逐族、三種子、只印 dev）。v2 程式碼與 ledger 一律不動。

**Tech Stack:** Python 3.12、pandas 2.3、numpy 2.4、lightgbm 4.7（`objective="quantile"`）、scikit-learn、pytest、ruff。sqlite `data/twa.db`。

**對應設計文件：** `docs/superpowers/specs/2026-09-28-level1-v3-target-features-design.md`（§0–2、§5 步驟 1–3）。本計畫**不含** §3 消息面資料層與 §4 ledger/pipeline/前端，那兩塊各自另開計畫。

## Global Constraints

- 成本常數 `COST_RT = 0.00585`（0.585%/RT），凍結。
- Horizons `(1, 5, 10)`；Entry = O(t+1)；Exit = C(t+N)。
- 損失函數 quantile `alpha = 0.25`；排名分數 = 預測的 q25。
- O(t+1) 為漲停價（以 C(t) 用 `app.research.level2.costs.up_limit` 貼 tick）→ 未成交，標籤 NaN。跌停開盤視為成交。
- U_t 沿用 `universe.build_tradable_universe`（ADV20 ≥ 5,000 萬 ∧ 非處置），不改。寬度／離散度**只用 U_t 內股票**算。
- 表示法二選一：方向訊號 rank（缺值補 0.5）；尺度／情境 raw（缺值留 NaN，**不補 0**）。不做雙表示。
- v2 的 4 個 `_bull` 交互項不進 v3。
- walk-forward：`first_test="2022-01-01"`、`step=126`、expanding、**embargo = horizon**、`MIN_TRAIN_DAYS=250`。
- dev = 2022-01-01~2024-12-31；holdout = 2025-01-01 起，**只讀不調**（script 只印 dev 指標）。
- 三種子 `(42, 7, 2024)`；LightGBM `n_jobs=1`（重現性），`subsample=0.8, subsample_freq=1, colsample_bytree=0.8`（種子才有離散可量）。
- 增量 < 三種子離散 → 噪音，不留；負結果照實寫進 findings，不調參救。
- Windows 坑：**任何 import lightgbm 的檔案必須先 `import sklearn.linear_model`**（OpenMP DLL 順序）。
- 所有 findings 數字一律由結果 JSON 帶出，禁手寫。
- 測試指令自 `backend/` 執行：`.venv/Scripts/python.exe -m pytest <path> -q`；lint：`.venv/Scripts/python.exe -m ruff check app/research/level1 scripts tests`。
- Commit 訊息結尾加 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。

## File Structure

| 檔案 | 責任 |
|---|---|
| `backend/app/research/level1/prices.py`（新） | 從 DB 載 open/high/low/volume/turnover 矩陣、類股對照、加權指數，對齊 close 形狀 |
| `backend/app/research/level1/targets_v3.py`（新） | 漲停價矩陣、Entry／fill、R_net、`build_targets_v3` |
| `backend/app/research/level1/features_v3.py`（新） | 五族特徵函式、`FeatureSet`、`FAMILIES`、`BASELINE`、`assemble_v3` |
| `backend/app/research/level1/evaluation_v3.py`（新） | Top-K 淨報酬、q25 校準、不進場訊號、`evaluate_v3(_by_period)` |
| `backend/app/research/level1/walkforward.py`（改） | 新增 `walk_forward_scores_v3`（吃 FeatureSet），既有函式不動 |
| `backend/app/research/level1/__init__.py`（改） | docstring 補 v3 模組 |
| `backend/scripts/level1_targets_v3.py`（新） | 產 `data/level1_v3_targets.pkl` |
| `backend/scripts/level1_v3_ablation.py`（新） | 基準線＋逐族 ablation，產 `data/level1_v3_results.json` |
| `backend/tests/test_level1_v3_targets.py`（新） | prices 載入、漲停、fill、R_net、截斷未來不改過去 |
| `backend/tests/test_level1_v3_features.py`（新） | 五族特徵無未來、U_t-only 寬度、FeatureSet 缺值政策 |
| `backend/tests/test_level1_v3_evaluation.py`（新） | 校準、Top-K 淨報酬、不進場、walk-forward v3 embargo |
| `docs/level1-v3-ablation-findings.md`（新） | 由 JSON 帶出的結果與 keep/drop 裁定 |

---

### Task 1: 價量矩陣載入器 `prices.py`

**Files:**
- Create: `backend/app/research/level1/prices.py`
- Test: `backend/tests/test_level1_v3_targets.py`

**Interfaces:**
- Produces: `load_price_matrices(con, index, columns, cols=PRICE_COLS) -> dict[str, pd.DataFrame]`（鍵 `"open","high","low","volume","turnover"`，float64，形狀 = (index, columns)）；`load_sector_map(con) -> pd.Series`（index=stock_id str, value=sector_id float, 無類股者 NaN）；`load_market_close(con) -> pd.Series`（index=date str）。

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_level1_v3_targets.py
"""Level 1 v3：價量載入、漲停判定、Entry/fill、R_net、洩漏防線。"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
import pytest

from app.research.level1 import prices as pr


def _db_with_prices():
    con = sqlite3.connect(":memory:")
    con.execute("""CREATE TABLE daily_prices(
        stock_id TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,
        volume INTEGER, turnover REAL)""")
    con.executemany(
        "INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?,?)",
        [("1111", "2025-01-01", 10.0, 10.5, 9.8, 10.2, 1000, 10200.0),
         ("1111", "2025-01-02", 10.3, 10.6, 10.1, 10.4, 1100, 11440.0),
         ("2222", "2025-01-02", 20.0, 20.5, 19.5, 20.1, 500, 10050.0),
         ("9999", "2025-01-01", 1.0, 1.0, 1.0, 1.0, 1, 1.0)])   # 不在 columns
    con.execute("CREATE TABLE stocks(id TEXT, sector_id INTEGER)")
    con.executemany("INSERT INTO stocks VALUES (?,?)",
                    [("1111", 3), ("2222", None), ("9999", 5)])
    con.execute("CREATE TABLE market_index(date TEXT, close REAL)")
    con.executemany("INSERT INTO market_index VALUES (?,?)",
                    [("2025-01-01", 20000.0), ("2025-01-02", 20100.0)])
    return con


def test_load_price_matrices_aligns_to_index_columns():
    con = _db_with_prices()
    index = pd.Index(["2025-01-01", "2025-01-02"], name="date")
    columns = pd.Index(["1111", "2222"], name="stock_id")
    mats = pr.load_price_matrices(con, index, columns)
    assert set(mats) == {"open", "high", "low", "volume", "turnover"}
    assert mats["open"].shape == (2, 2)
    assert mats["open"].loc["2025-01-02", "1111"] == 10.3
    assert np.isnan(mats["open"].loc["2025-01-01", "2222"])   # 缺列 → NaN
    assert "9999" not in mats["open"].columns
    assert mats["turnover"].dtypes.iloc[0] == "float64"


def test_load_sector_map_and_market_close():
    con = _db_with_prices()
    sec = pr.load_sector_map(con)
    assert sec.loc["1111"] == 3.0
    assert np.isnan(sec.loc["2222"])
    mkt = pr.load_market_close(con)
    assert list(mkt.index) == ["2025-01-01", "2025-01-02"]
    assert mkt.loc["2025-01-02"] == 20100.0
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_level1_v3_targets.py -q`
Expected: FAIL，`ModuleNotFoundError: app.research.level1.prices`

- [ ] **Step 3: 實作 `prices.py`**

```python
# backend/app/research/level1/prices.py
"""Level 1 v3 價量矩陣載入（設計 2026-09-28 §1、§2.3）。

v2 只用 close/volume；v3 的 Entry 是 O(t+1)、成交判定要漲停價、尺度族要 high/low，
故一次把 OHLCV+turnover 對齊 close 矩陣形狀載入。缺格 = NaN（該股該日無列）。
"""

from __future__ import annotations

import pandas as pd

PRICE_COLS = ("open", "high", "low", "volume", "turnover")


def load_price_matrices(con, index: pd.Index, columns: pd.Index,
                        cols: tuple[str, ...] = PRICE_COLS) -> dict[str, pd.DataFrame]:
    """daily_prices → {欄名: 矩陣}，形狀對齊 (index, columns)，float64。"""
    df = pd.read_sql_query(
        f"SELECT stock_id, date, {', '.join(cols)} FROM daily_prices", con)
    df = df[df["stock_id"].astype(str).isin(set(columns))]
    df["stock_id"] = df["stock_id"].astype(str)
    out: dict[str, pd.DataFrame] = {}
    for c in cols:
        mat = df.pivot_table(index="date", columns="stock_id", values=c, aggfunc="last")
        out[c] = mat.reindex(index=index, columns=columns).astype("float64")
    return out


def load_sector_map(con) -> pd.Series:
    """stock_id → sector_id（float，無類股 NaN）。類股中性化用。"""
    df = pd.read_sql_query("SELECT id, sector_id FROM stocks", con)
    return pd.Series(df["sector_id"].astype("float64").to_numpy(),
                     index=df["id"].astype(str), name="sector_id")


def load_market_close(con) -> pd.Series:
    """加權指數收盤（index=date str 升冪）。"""
    df = pd.read_sql_query("SELECT date, close FROM market_index ORDER BY date", con)
    return pd.Series(df["close"].astype("float64").to_numpy(),
                     index=df["date"].astype(str), name="mkt_close")
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_level1_v3_targets.py -q`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/research/level1/prices.py backend/tests/test_level1_v3_targets.py
git commit -m "feat(level1): v3 價量矩陣載入器（OHLCV/turnover、類股對照、加權指數）

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Target v3——漲停價、Entry／fill、R_net

**Files:**
- Create: `backend/app/research/level1/targets_v3.py`
- Modify: `backend/app/research/level1/__init__.py`（docstring 加兩行）
- Test: `backend/tests/test_level1_v3_targets.py`（追加）

**Interfaces:**
- Consumes: `app.research.level2.costs.up_limit(prev_close: float) -> float`
- Produces:
  - 常數 `COST_RT=0.00585`、`HORIZONS_V3=(1,5,10)`、`FILLED=0`、`LIMIT_UP_UNFILLED=1`、`NO_TRADE=2`
  - `limit_up_from_prev(prev_close: pd.DataFrame) -> pd.DataFrame`
  - `entry_and_fill(open_, close) -> tuple[pd.DataFrame, pd.DataFrame]`（Entry 對齊決策日 t；fill int8）
  - `net_returns(entry, close, horizon, cost=COST_RT) -> pd.DataFrame`
  - `build_targets_v3(open_, close, in_universe, horizons=HORIZONS_V3, cost=COST_RT) -> tuple[dict[int, dict[str, pd.DataFrame]], pd.DataFrame, pd.DataFrame]`，回 `(targets{N: {"net": float32 已套 U_t}}, entry, fill)`

- [ ] **Step 1: 追加失敗測試**

```python
# 追加到 backend/tests/test_level1_v3_targets.py
from app.research.level1 import targets_v3 as t3
from app.research.level2.costs import up_limit


def test_limit_up_matrix_matches_scalar_rule():
    prev = pd.DataFrame({"A": [9.99, 49.5, 95.0, 999.0, np.nan], "B": [10.0, 100.0, 1000.0, 2475.0, 33.3]})
    lim = t3.limit_up_from_prev(prev)
    for c in prev.columns:
        for i, v in enumerate(prev[c]):
            if np.isnan(v):
                assert np.isnan(lim[c].iloc[i])
            else:
                assert lim[c].iloc[i] == pytest.approx(up_limit(v))


def _oc():
    """4 個交易日、4 檔。決策日 d1 的 Entry = d2 開盤。"""
    dates = pd.Index([f"2025-01-0{i}" for i in range(1, 5)], name="date")
    close = pd.DataFrame(index=dates, data={
        "LIM": [100.0, 110.0, 112.0, 115.0],   # d2 開盤即漲停（110 = up_limit(100)）
        "OK":  [100.0, 104.0, 106.0, 108.0],   # 正常成交
        "GAP": [100.0, np.nan, 101.0, 102.0],  # d2 停牌 → NO_TRADE
        "DN":  [100.0, 91.0, 92.0, 93.0],      # d2 開盤跌停 → 視為成交
    })
    open_ = pd.DataFrame(index=dates, data={
        "LIM": [99.0, 110.0, 111.0, 114.0],
        "OK":  [99.0, 105.0, 105.5, 107.0],
        "GAP": [99.0, np.nan, 100.5, 101.5],
        "DN":  [99.0, 90.0, 91.5, 92.5],
    })
    close.columns.name = open_.columns.name = "stock_id"
    return open_, close


def test_entry_and_fill_codes():
    open_, close = _oc()
    entry, fill = t3.entry_and_fill(open_, close)
    d1 = "2025-01-01"
    assert fill.loc[d1, "LIM"] == t3.LIMIT_UP_UNFILLED and np.isnan(entry.loc[d1, "LIM"])
    assert fill.loc[d1, "OK"] == t3.FILLED and entry.loc[d1, "OK"] == 105.0
    assert fill.loc[d1, "GAP"] == t3.NO_TRADE and np.isnan(entry.loc[d1, "GAP"])
    assert fill.loc[d1, "DN"] == t3.FILLED and entry.loc[d1, "DN"] == 90.0
    assert (fill.loc["2025-01-04"] == t3.NO_TRADE).all()     # 最後一日無 t+1
    assert fill.dtypes.iloc[0] == "int8"


def test_net_return_formula_and_cost():
    open_, close = _oc()
    entry, _ = t3.entry_and_fill(open_, close)
    net1 = t3.net_returns(entry, close, 1)
    # OK：d2 開盤 105 進，d2 收盤 104 出
    assert net1.loc["2025-01-01", "OK"] == pytest.approx(104.0 / 105.0 - 1 - 0.00585)
    net2 = t3.net_returns(entry, close, 2)
    assert net2.loc["2025-01-01", "OK"] == pytest.approx(106.0 / 105.0 - 1 - 0.00585)
    assert np.isnan(net1.loc["2025-01-01", "LIM"])            # 未成交 → NaN


def test_build_targets_masks_universe_and_truncation_does_not_change_past():
    open_, close = _oc()
    mask = close.notna()
    mask.loc[:, "DN"] = False                                  # DN 不在 U_t
    targets, entry, fill = t3.build_targets_v3(open_, close, mask, horizons=(1, 2))
    assert set(targets) == {1, 2}
    assert np.isnan(targets[1]["net"].loc["2025-01-01", "DN"])
    assert targets[1]["net"].dtypes.iloc[0] == "float32"
    # 截斷未來（去掉最後一日）不得改變過去任何非 NaN 值
    t_cut, _, _ = t3.build_targets_v3(open_.iloc[:-1], close.iloc[:-1], mask.iloc[:-1], horizons=(1, 2))
    full = targets[2]["net"].iloc[:-1]
    cut = t_cut[2]["net"]
    both = full.notna() & cut.notna()
    assert np.allclose(full[both].fillna(0).to_numpy(), cut[both].fillna(0).to_numpy())
    assert cut.loc["2025-01-02"].isna().all()                 # 截斷後最後可算列變 NaN
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_level1_v3_targets.py -q`
Expected: 4 new FAIL，`cannot import name 'targets_v3'`

- [ ] **Step 3: 實作 `targets_v3.py`**

```python
# backend/app/research/level1/targets_v3.py
"""Level 1 v3 Target（設計 2026-09-28 §1）：隔日開盤進場的絕對淨報酬。

Entry(i,t)   = O(i,t+1)
fill(i,t)    = FILLED / LIMIT_UP_UNFILLED（O(t+1) ≥ up_limit(C(t))）/ NO_TRADE（O(t+1) 缺）
R_net(i,t,N) = C(i,t+N) / Entry(i,t) − 1 − COST_RT

- 未成交列標籤 NaN：不進訓練、不進評估分母；ledger 另記 fill_status。
- 跌停開盤視為成交（買得到，且是模型該學會避開的）。
- 已明講的副作用：「昨日鎖漲停、今日又跳空漲停」被排除 → 模型系統性看衰漲停股。
  這是設計出來的可執行性偏誤，不是模型發現。
- fill 與 horizon 無關（只看 t+1 開盤），所以獨立回傳一份。
- close/open 未還原權息（延續 v2 已知限制）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.research.level2.costs import up_limit

COST_RT = 0.00585            # 0.585%/RT，Level 2 富果化成本模型
HORIZONS_V3 = (1, 5, 10)
FILLED, LIMIT_UP_UNFILLED, NO_TRADE = 0, 1, 2

_up_limit_vec = np.vectorize(up_limit, otypes=[float])


def limit_up_from_prev(prev_close: pd.DataFrame) -> pd.DataFrame:
    """以前一日收盤算漲停價矩陣（tick 貼齊由 costs.up_limit 單一來源負責）。"""
    vals = prev_close.to_numpy(dtype=float)
    out = np.full_like(vals, np.nan)
    ok = np.isfinite(vals)
    out[ok] = _up_limit_vec(vals[ok])
    return pd.DataFrame(out, index=prev_close.index, columns=prev_close.columns)


def entry_and_fill(open_: pd.DataFrame, close: pd.DataFrame,
                   ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(Entry, fill)。兩者皆對齊決策日 t；Entry 值為 O(t+1)，未成交為 NaN。"""
    nxt_open = open_.shift(-1)
    lim_next = limit_up_from_prev(close)                 # C(t) → t+1 的漲停價
    at_limit = nxt_open >= (lim_next - 1e-9)
    fill = pd.DataFrame(FILLED, index=close.index, columns=close.columns, dtype="int8")
    fill = fill.mask(at_limit, LIMIT_UP_UNFILLED).mask(nxt_open.isna(), NO_TRADE)
    fill = fill.astype("int8")
    entry = nxt_open.where(fill == FILLED)
    return entry, fill


def net_returns(entry: pd.DataFrame, close: pd.DataFrame, horizon: int,
                cost: float = COST_RT) -> pd.DataFrame:
    """C(t+N) / Entry − 1 − cost。Entry NaN（未成交）自然傳播為 NaN。"""
    return close.shift(-horizon) / entry - 1 - cost


def build_targets_v3(open_: pd.DataFrame, close: pd.DataFrame, in_universe: pd.DataFrame,
                     horizons: tuple[int, ...] = HORIZONS_V3, cost: float = COST_RT,
                     ) -> tuple[dict[int, dict[str, pd.DataFrame]], pd.DataFrame, pd.DataFrame]:
    """→ ({N: {"net": R_net 矩陣（已套 U_t, float32）}}, entry, fill)。"""
    entry, fill = entry_and_fill(open_, close)
    out: dict[int, dict[str, pd.DataFrame]] = {}
    for n in horizons:
        out[n] = {"net": net_returns(entry, close, n, cost).where(in_universe).astype("float32")}
    return out, entry, fill
```

`__init__.py` docstring 在「模組：」清單末尾追加：

```
- prices／targets_v3／features_v3／evaluation_v3：v3（隔日開盤進場淨報酬、q25 排名，
  設計 docs/superpowers/specs/2026-09-28-level1-v3-target-features-design.md）
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_level1_v3_targets.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/research/level1/targets_v3.py backend/app/research/level1/__init__.py backend/tests/test_level1_v3_targets.py
git commit -m "feat(level1): v3 target——O(t+1) 進場淨報酬、漲停未成交、fill 代碼

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: 快取建置腳本 `level1_targets_v3.py`

**Files:**
- Create: `backend/scripts/level1_targets_v3.py`

**Interfaces:**
- Consumes: `universe.build_tradable_universe(con)`、`prices.load_price_matrices`、`targets_v3.build_targets_v3`
- Produces: `backend/data/level1_v3_targets.pkl`，結構：
  ```
  {"meta": {...}, "close": f32, "open": f32, "high": f32, "low": f32, "volume": f32,
   "turnover": f32, "universe": bool, "entry": f32, "fill": int8,
   "targets": {1: {"net": f32}, 5: {...}, 10: {...}}}
  ```
  `meta` 含 `db_max_date`（供 `_assert_snapshot_fresh` 同款檢查）、`fill_report`（逐年未成交率）。

- [ ] **Step 1: 寫腳本**

```python
# backend/scripts/level1_targets_v3.py
"""Level 1 v3 Target 快取建置（設計 2026-09-28 §1）。

產出 data/level1_v3_targets.pkl：OHLCV+turnover 矩陣、U_t、Entry、fill、{N: {"net"}}。
用法：cd backend && .venv/Scripts/python.exe -m scripts.level1_targets_v3
"""

from __future__ import annotations

import pickle
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.research.level1 import prices as pr  # noqa: E402
from app.research.level1 import targets_v3 as t3  # noqa: E402
from app.research.level1 import universe as uv  # noqa: E402

_DB = Path(__file__).resolve().parents[1] / "data" / "twa.db"
_OUT = Path(__file__).resolve().parents[1] / "data" / "level1_v3_targets.pkl"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    con = sqlite3.connect(_DB)
    db_max_date = con.execute("SELECT max(date) FROM daily_prices").fetchone()[0]
    close, mask = uv.build_tradable_universe(con)           # 唯一入口
    mats = pr.load_price_matrices(con, close.index, close.columns)
    con.close()
    close = close.astype("float64")
    _log(f"矩陣 {close.shape[0]} 日 × {close.shape[1]} 檔（{close.index[0]} ~ {close.index[-1]}）")

    targets, entry, fill = t3.build_targets_v3(mats["open"], close, mask)

    in_u = mask.to_numpy()
    fill_u = fill.where(mask)
    years = pd.Index(close.index).str[:4]
    fill_report = {}
    for y in sorted(set(years)):
        sel = fill_u.loc[years == y]
        n = int(sel.notna().to_numpy().sum())
        if n == 0:
            continue
        fill_report[y] = {
            "n": n,
            "limit_up_unfilled_pct": round(float((sel == t3.LIMIT_UP_UNFILLED).to_numpy().sum()) / n * 100, 2),
            "no_trade_pct": round(float((sel == t3.NO_TRADE).to_numpy().sum()) / n * 100, 2),
        }
    _log("逐年未成交率（U_t 內）：" + "; ".join(
        f"{y} 漲停 {r['limit_up_unfilled_pct']}% / 停牌 {r['no_trade_pct']}%" for y, r in fill_report.items()))

    report = {}
    for n, m in targets.items():
        net = m["net"]
        valid = int(net.notna().to_numpy().sum())
        report[n] = {"valid": valid, "mean_net_pct": round(float(net.stack().mean()) * 100, 3),
                     "median_net_pct": round(float(net.stack().median()) * 100, 3)}
        _log(f"  {n:>3}D  有效列 {valid:>10,}  均值 {report[n]['mean_net_pct']:+.3f}%  中位 {report[n]['median_net_pct']:+.3f}%")

    payload = {
        "meta": {
            "built_at": date.today().isoformat(),
            "spec": "v3 §1: R_net = C(t+N)/O(t+1) − 1 − 0.585%; O(t+1) 漲停 → 未成交",
            "db_max_date": db_max_date,
            "cost_rt": t3.COST_RT,
            "horizons": list(targets),
            "research_start": uv.RESEARCH_START,
            "n_universe_cells": int(in_u.sum()),
            "fill_report": fill_report,
            "report": report,
        },
        "close": close.astype("float32"),
        "open": mats["open"].astype("float32"),
        "high": mats["high"].astype("float32"),
        "low": mats["low"].astype("float32"),
        "volume": mats["volume"].astype("float32"),
        "turnover": mats["turnover"].astype("float32"),
        "universe": mask,
        "entry": entry.astype("float32"),
        "fill": fill,
        "targets": targets,
    }
    with open(_OUT, "wb") as f:
        pickle.dump(payload, f, protocol=4)
    _log(f"已存 {_OUT.name}（{_OUT.stat().st_size / 1e6:.0f} MB）")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 跑腳本**

Run: `cd backend && .venv/Scripts/python.exe -m scripts.level1_targets_v3`
Expected: 印出逐年未成交率與三個 horizon 的有效列數；`data/level1_v3_targets.pkl` 產生。把印出的逐年漲停未成交率記下來（findings 要用）。

- [ ] **Step 3: 健檢——漲停未成交率合理性**

Run:
```bash
cd backend && .venv/Scripts/python.exe -c "
import pickle; p=pickle.load(open('data/level1_v3_targets.pkl','rb'))
print(p['meta']['fill_report']); print(p['meta']['report'])"
```
Expected: 每年 `limit_up_unfilled_pct` 落在 0.3%~3% 區間（v2 記錄 1D Top-20 鎖死率 19% vs 市場 1.5%，全池應接近後者）。若 >5% 或 =0，先查 `entry_and_fill` 的 tick 規則再往下。

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/level1_targets_v3.py
git commit -m "feat(level1): v3 target 快取建置腳本（含逐年未成交率報告）

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

（`data/*.pkl` 依既有慣例不入版控。）

---

### Task 4: 特徵族 A（成交／隔夜）與 B（尺度）

**Files:**
- Create: `backend/app/research/level1/features_v3.py`
- Test: `backend/tests/test_level1_v3_features.py`

**Interfaces:**
- Consumes: `targets_v3.limit_up_from_prev`
- Produces:
  - `build_execution_features(open_, close) -> dict[str, pd.DataFrame]`：`dist_limit_up`, `lockup_days20`, `gap_std20`, `overnight_minus_intraday20`
  - `build_scale_features(high, low, close) -> dict[str, pd.DataFrame]`：`vol20`, `vol60`, `downside_vol20`, `atr14_pct`

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_level1_v3_features.py
"""Level 1 v3 特徵：無未來資訊、U_t-only 寬度、FeatureSet 缺值政策。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.research.level1 import features_v3 as f3
from app.research.level2.costs import up_limit


def _ohlc(n_days=70, n_stocks=6, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.Index(pd.date_range("2025-01-01", periods=n_days).astype(str), name="date")
    cols = pd.Index([f"{1000+i}" for i in range(n_stocks)], name="stock_id")
    close = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.02, (n_days, n_stocks)), axis=0)),
                         index=dates, columns=cols)
    open_ = close.shift(1).fillna(close) * (1 + rng.normal(0, 0.01, close.shape))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.01, close.shape))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.01, close.shape))
    return open_, high, low, close


def _assert_no_future(build, *mats, t_pos=40):
    """擾動 t+1 之後的所有輸入，t 及之前的特徵值不得改變。"""
    base = build(*mats)
    pert = [m.copy() for m in mats]
    for m in pert:
        m.iloc[t_pos + 1:] = m.iloc[t_pos + 1:] * 1.37 + 3.0
    after = build(*pert)
    for k in base:
        a, b = base[k].iloc[:t_pos + 1], after[k].iloc[:t_pos + 1]
        assert np.allclose(a.fillna(-999).to_numpy(), b.fillna(-999).to_numpy()), k


def test_execution_features_no_future():
    open_, _, _, close = _ohlc()
    _assert_no_future(f3.build_execution_features, open_, close)


def test_dist_limit_up_zero_when_locked_and_lockup_counts():
    open_, _, _, close = _ohlc(n_days=30, n_stocks=1)
    c = close.copy()
    # 第 20~24 天連續鎖漲停
    for i in range(20, 25):
        c.iloc[i, 0] = up_limit(float(c.iloc[i - 1, 0]))
    feats = f3.build_execution_features(open_, c)
    assert feats["dist_limit_up"].iloc[22, 0] == pytest.approx(0.0, abs=1e-9)
    assert feats["lockup_days20"].iloc[24, 0] == 5
    assert feats["lockup_days20"].iloc[19, 0] == 0


def test_overnight_minus_intraday_sign():
    """全部漲幅來自跳空（開盤=前收×1.01，收盤=開盤）→ 差值為正。"""
    dates = pd.Index(pd.date_range("2025-01-01", periods=30).astype(str), name="date")
    close = pd.DataFrame({"A": 100 * 1.01 ** np.arange(30)}, index=dates)
    open_ = close.copy()                       # 開盤 = 收盤 → 盤中報酬 0
    feats = f3.build_execution_features(open_, close)
    assert feats["overnight_minus_intraday20"].iloc[-1, 0] > 0.15
    assert feats["gap_std20"].iloc[-1, 0] == pytest.approx(0.0, abs=1e-9)


def test_scale_features_no_future_and_downside_only_negative():
    open_, high, low, close = _ohlc()
    _assert_no_future(f3.build_scale_features, high, low, close)
    up_only = pd.DataFrame({"A": 100 * 1.01 ** np.arange(40)},
                           index=pd.Index(pd.date_range("2025-01-01", periods=40).astype(str)))
    feats = f3.build_scale_features(up_only, up_only, up_only)
    assert feats["downside_vol20"].iloc[-1, 0] == pytest.approx(0.0, abs=1e-12)
    assert feats["vol20"].iloc[-1, 0] > 0 or feats["vol20"].iloc[-1, 0] == pytest.approx(0.0, abs=1e-6)
    assert feats["atr14_pct"].iloc[-1, 0] > 0
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_level1_v3_features.py -q`
Expected: FAIL，`cannot import name 'features_v3'`

- [ ] **Step 3: 實作 A、B 兩族**

```python
# backend/app/research/level1/features_v3.py
"""Level 1 v3 特徵（設計 2026-09-28 §2）——按「角色」決定表示法，不做雙表示。

角色 → 表示：
- 方向訊號（D 動能／E 流動性比率／F 基本面）→ 每日橫斷面 rank，缺值補 0.5
- 尺度（B）／情境（C 大盤寬度）／成交（A）→ 原始值，缺值留 NaN 給 GBM，不補 0

所有回看視窗含 T 日（決策時點 = T 收盤後）。任何用到 O(t+1)、C(t+1) 的量都是標籤，
不得出現在這裡（test_level1_v3_features 的 _assert_no_future 釘死）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .targets_v3 import limit_up_from_prev

_W20, _MP20 = 20, 10
_W60, _MP60 = 60, 30


# ── A. 成交／隔夜（execution；raw）──

def build_execution_features(open_: pd.DataFrame, close: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """dist_limit_up / lockup_days20 / gap_std20 / overnight_minus_intraday20。"""
    prev_close = close.shift(1)
    lim_today = limit_up_from_prev(prev_close)           # C(t−1) → t 日漲停價
    locked = (close >= lim_today - 1e-9).astype(float).where(close.notna() & lim_today.notna())
    gap = open_ / prev_close - 1                         # 隔夜報酬 O(t)/C(t−1)
    intraday = close / open_ - 1                         # 盤中報酬 C(t)/O(t)
    return {
        "dist_limit_up": close / lim_today - 1,
        "lockup_days20": locked.rolling(_W20, min_periods=_MP20).sum(),
        "gap_std20": gap.rolling(_W20, min_periods=_MP20).std(),
        "overnight_minus_intraday20": (gap.rolling(_W20, min_periods=_MP20).sum()
                                       - intraday.rolling(_W20, min_periods=_MP20).sum()),
    }


# ── B. 尺度（scale；raw）──

def build_scale_features(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame,
                         ) -> dict[str, pd.DataFrame]:
    """vol20 / vol60 / downside_vol20（下行半標準差）/ atr14_pct。"""
    ret1 = close.pct_change(fill_method=None)
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
                   axis=1, keys=["hl", "hc", "lc"]).T.groupby(level=1).max().T
    tr = tr.reindex(columns=close.columns)
    return {
        "vol20": ret1.rolling(_W20, min_periods=_MP20).std(),
        "vol60": ret1.rolling(_W60, min_periods=_MP60).std(),
        "downside_vol20": ret1.clip(upper=0).pow(2).rolling(_W20, min_periods=_MP20).mean().pow(0.5),
        "atr14_pct": tr.rolling(14, min_periods=7).mean() / close,
    }
```

注意 `tr` 的寫法：`pd.concat(..., axis=1, keys=...)` 產生兩層欄位 (key, stock_id)，轉置後以 `level=1`（stock_id）分組取 max 再轉回來。若嫌繞，等價寫法：`tr = np.maximum.reduce([(high-low).to_numpy(), (high-prev_close).abs().to_numpy(), (low-prev_close).abs().to_numpy()])` 再包回 DataFrame——擇一，測試會釘住結果。

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_level1_v3_features.py -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/research/level1/features_v3.py backend/tests/test_level1_v3_features.py
git commit -m "feat(level1): v3 特徵族 A 成交／隔夜、B 尺度（raw 表示）

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: 特徵族 C（大盤／寬度）與 D/E（方向／流動性）

**Files:**
- Modify: `backend/app/research/level1/features_v3.py`
- Test: `backend/tests/test_level1_v3_features.py`（追加）

**Interfaces:**
- Consumes: `prices.load_sector_map` 的輸出型別（`pd.Series` index=stock_id, value=sector_id float）
- Produces:
  - `build_market_features(close, in_universe, mkt_close: pd.Series) -> dict`（全部 raw、已 broadcast 成矩陣）：`mkt_ret5`, `mkt_ret20`, `mkt_vol20`, `breadth_ma20`, `dispersion`
  - `build_direction_features(close, volume, turnover, in_universe, sector_of: pd.Series) -> tuple[dict, dict]`：`(to_rank, raw)`；`to_rank` 含 `ret1, ret5, ret20, ret60, ret20_ex5, bias20, pos240, vr5_60, amihud20, sec_neutral_ret20`；`raw` 含 `dollar_vol20`

- [ ] **Step 1: 追加失敗測試**

```python
# 追加到 backend/tests/test_level1_v3_features.py

def test_market_features_broadcast_and_no_future():
    open_, high, low, close = _ohlc()
    mask = close.notna()
    mkt = pd.Series(100 * np.exp(np.cumsum(np.full(len(close), 0.001))), index=close.index)
    feats = f3.build_market_features(close, mask, mkt)
    assert set(feats) == {"mkt_ret5", "mkt_ret20", "mkt_vol20", "breadth_ma20", "dispersion"}
    for k, m in feats.items():
        assert m.shape == close.shape, k
        row = m.iloc[-1].dropna()
        assert row.nunique() == 1, k                      # 同日各股相同（情境特徵）
    _assert_no_future(lambda c, m: f3.build_market_features(c, mask, m), close, pd.DataFrame({"m": mkt}).iloc[:, 0].to_frame() if False else close * 0 + mkt.to_numpy()[:, None])


def test_breadth_and_dispersion_use_universe_only():
    open_, high, low, close = _ohlc(n_days=40, n_stocks=4)
    mkt = pd.Series(1.0, index=close.index)
    mask_all = close.notna()
    mask_drop = mask_all.copy()
    mask_drop.iloc[:, 0] = False                            # 第 0 檔踢出 U_t
    b_all = f3.build_market_features(close, mask_all, mkt)["breadth_ma20"].iloc[-1, 0]
    b_drop = f3.build_market_features(close, mask_drop, mkt)["breadth_ma20"].iloc[-1, 0]
    # 手算：只用 U_t 內三檔
    ma20 = close.rolling(20, min_periods=10).mean()
    manual = float((close > ma20).iloc[-1, 1:].mean())
    assert b_drop == pytest.approx(manual)
    assert b_all != b_drop or close.shape[1] == 1
    d_drop = f3.build_market_features(close, mask_drop, mkt)["dispersion"].iloc[-1, 0]
    assert d_drop == pytest.approx(float(close.pct_change().iloc[-1, 1:].std()))


def test_direction_features_sector_neutral_and_amihud():
    open_, high, low, close = _ohlc(n_days=70, n_stocks=4)
    volume = pd.DataFrame(1000.0, index=close.index, columns=close.columns)
    turnover = close * volume
    mask = close.notna()
    sector = pd.Series([1.0, 1.0, 2.0, np.nan], index=close.columns)
    to_rank, raw = f3.build_direction_features(close, volume, turnover, mask, sector)
    assert set(raw) == {"dollar_vol20"}
    assert {"ret20", "sec_neutral_ret20", "amihud20", "vr5_60", "pos240"} <= set(to_rank)
    r20 = to_rank["ret20"].iloc[-1]
    sn = to_rank["sec_neutral_ret20"].iloc[-1]
    # 類股 1 兩檔的中性化值和為 0；無類股者 NaN
    assert sn.iloc[0] + sn.iloc[1] == pytest.approx(0.0, abs=1e-12)
    assert sn.iloc[2] == pytest.approx(0.0, abs=1e-12)       # 單檔類股 → 等於自己均值
    assert np.isnan(sn.iloc[3])
    assert (to_rank["amihud20"].iloc[-1] > 0).all()
    assert raw["dollar_vol20"].iloc[-1, 0] == pytest.approx(np.log1p(turnover.iloc[-20:, 0].mean()))
```

`test_market_features_broadcast_and_no_future` 最後一行的 `_assert_no_future` 呼叫寫法太繞，改成獨立明確版本：

```python
def test_market_features_no_future():
    _, _, _, close = _ohlc()
    mask = close.notna()
    mkt = pd.Series(100 * np.exp(np.cumsum(np.full(len(close), 0.001))), index=close.index)
    base = f3.build_market_features(close, mask, mkt)
    c2, m2 = close.copy(), mkt.copy()
    c2.iloc[41:] *= 1.5
    m2.iloc[41:] *= 1.5
    after = f3.build_market_features(c2, mask, m2)
    for k in base:
        assert np.allclose(base[k].iloc[:41].fillna(-9).to_numpy(), after[k].iloc[:41].fillna(-9).to_numpy()), k
```

（把 `test_market_features_broadcast_and_no_future` 裡的 `_assert_no_future` 那一行刪掉，只留 shape／同日相同的斷言。）

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_level1_v3_features.py -q`
Expected: 4 new FAIL，`AttributeError: build_market_features`

- [ ] **Step 3: 實作 C、D/E**

追加到 `features_v3.py`：

```python
# ── C. 大盤／寬度（context-gate；raw，同日各股相同）──

def _broadcast(s: pd.Series, like: pd.DataFrame) -> pd.DataFrame:
    arr = np.broadcast_to(s.reindex(like.index).to_numpy(dtype=float)[:, None], like.shape)
    return pd.DataFrame(arr.copy(), index=like.index, columns=like.columns)


def build_market_features(close: pd.DataFrame, in_universe: pd.DataFrame,
                          mkt_close: pd.Series) -> dict[str, pd.DataFrame]:
    """mkt_ret5 / mkt_ret20 / mkt_vol20（加權指數）；breadth_ma20 / dispersion（只用 U_t 內）。

    大盤特徵在橫斷面內是常數——v2 因 rank 表示而只能做交互；v3 目標是絕對報酬，
    GBM 直接吃原始值即可（「今天不進場」主要靠這族）。
    """
    mkt = mkt_close.reindex(close.index).ffill()
    mret1 = mkt.pct_change(fill_method=None)
    ma20 = close.rolling(_W20, min_periods=_MP20).mean()
    ret1 = close.pct_change(fill_method=None)
    series = {
        "mkt_ret5": mkt.pct_change(5, fill_method=None),
        "mkt_ret20": mkt.pct_change(20, fill_method=None),
        "mkt_vol20": mret1.rolling(_W20, min_periods=_MP20).std(),
        "breadth_ma20": (close > ma20).astype(float).where(in_universe & ma20.notna()).mean(axis=1),
        "dispersion": ret1.where(in_universe).std(axis=1),
    }
    return {k: _broadcast(v, close) for k, v in series.items()}


# ── D 動能 / E 流動性（direction；to_rank）＋ dollar_vol20（raw）──

def build_direction_features(close: pd.DataFrame, volume: pd.DataFrame, turnover: pd.DataFrame,
                             in_universe: pd.DataFrame, sector_of: pd.Series,
                             ) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    """→ (to_rank, raw)。sec_neutral_ret20 = ret20 − 同日同類股（U_t 內）ret20 均值。"""
    ret1 = close.pct_change(fill_method=None)
    ret20 = close.pct_change(20, fill_method=None)
    v5 = volume.rolling(5, min_periods=3).mean()
    v60 = volume.rolling(_W60, min_periods=20).mean()

    sec = sector_of.reindex(close.columns)
    r20_u = ret20.where(in_universe)
    # 以 columns 分組：轉置後 groupby 類股 → transform mean → 轉回。NaN 類股不分組 → NaN。
    sec_mean = r20_u.T.groupby(sec.to_numpy()).transform("mean").T.reindex(columns=close.columns)

    to_rank = {
        "ret1": ret1,
        "ret5": close.pct_change(5, fill_method=None),
        "ret20": ret20,
        "ret60": close.pct_change(60, fill_method=None),
        "ret20_ex5": close.shift(5) / close.shift(20) - 1,
        "bias20": close / close.rolling(_W20, min_periods=_MP20).mean() - 1,
        "pos240": close / close.rolling(240, min_periods=60).max() - 1,
        "vr5_60": v5 / v60,
        "amihud20": (ret1.abs() / turnover).rolling(_W20, min_periods=_MP20).mean(),
        "sec_neutral_ret20": ret20 - sec_mean,
    }
    raw = {"dollar_vol20": np.log1p(turnover.rolling(_W20, min_periods=_MP20).mean())}
    return to_rank, raw
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_level1_v3_features.py -q`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/research/level1/features_v3.py backend/tests/test_level1_v3_features.py
git commit -m "feat(level1): v3 特徵族 C 大盤寬度（U_t-only）、D/E 方向與流動性（含類股中性化、Amihud）

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `FeatureSet`、族群定義、`assemble_v3`

**Files:**
- Modify: `backend/app/research/level1/features_v3.py`
- Test: `backend/tests/test_level1_v3_features.py`（追加）

**Interfaces:**
- Consumes: `features.rank_transform(feats, in_universe)`（v2 現成）、`features.build_fundamental_features(session, index, columns)`（v2 現成）
- Produces:
  - `@dataclass FeatureSet(ranked: dict[str, DataFrame], raw: dict[str, DataFrame])`，`.names -> list[str]`（ranked 在前、raw 在後）、`.subset(names: list[str]) -> FeatureSet`
  - `FAMILIES: dict[str, list[str]]`，鍵順序 `["A", "B", "C", "DE", "F"]`
  - `BASELINE: tuple[str, ...] = ("ret20", "vol20", "dollar_vol20", "mkt_ret20", "dist_limit_up")`
  - `build_feature_set(open_, high, low, close, volume, turnover, in_universe, mkt_close, sector_of, fund_feats: dict | None) -> FeatureSet`
  - `assemble_v3(fs: FeatureSet, target: pd.DataFrame, dates: pd.Index) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]`（列 = target 非 NaN；ranked 缺值 0.5、raw 缺值 NaN；meta 兩欄 `date, stock_id`）

- [ ] **Step 1: 追加失敗測試**

```python
# 追加到 backend/tests/test_level1_v3_features.py

def test_feature_set_names_order_and_subset():
    idx = pd.Index(["2025-01-01"], name="date")
    cols = pd.Index(["1111"], name="stock_id")
    one = pd.DataFrame(1.0, index=idx, columns=cols)
    fs = f3.FeatureSet(ranked={"r1": one, "r2": one}, raw={"x1": one})
    assert fs.names == ["r1", "r2", "x1"]
    sub = fs.subset(["x1", "r2"])
    assert sub.names == ["r2", "x1"]                        # 保持 ranked 前 raw 後
    with pytest.raises(KeyError):
        fs.subset(["nope"])


def test_assemble_v3_missing_policy():
    idx = pd.Index(["2025-01-01", "2025-01-02"], name="date")
    cols = pd.Index(["1111", "2222"], name="stock_id")
    target = pd.DataFrame([[0.01, np.nan], [0.02, -0.03]], index=idx, columns=cols)
    ranked = {"r": pd.DataFrame([[0.2, np.nan], [np.nan, 0.9]], index=idx, columns=cols)}
    raw = {"x": pd.DataFrame([[1.5, 2.5], [np.nan, 4.5]], index=idx, columns=cols)}
    x, y, meta = f3.assemble_v3(f3.FeatureSet(ranked, raw), target, idx)
    assert x.shape == (3, 2) and len(y) == 3                # target NaN 列被丟
    assert list(meta.columns) == ["date", "stock_id"]
    # 列序 = (d1,1111), (d2,1111), (d2,2222)
    assert x[1, 0] == pytest.approx(0.5)                    # ranked 缺值 → 0.5
    assert np.isnan(x[1, 1])                                # raw 缺值 → NaN 保留
    assert x[2, 1] == pytest.approx(4.5)
    assert x.dtype == np.float32


def test_families_cover_all_and_baseline_is_subset():
    all_names = {n for ns in f3.FAMILIES.values() for n in ns}
    assert set(f3.BASELINE) <= all_names
    assert list(f3.FAMILIES) == ["A", "B", "C", "DE", "F"]
    assert "ret20_bull" not in all_names                     # v2 交互項不進 v3


def test_build_feature_set_representation_by_role():
    open_, high, low, close = _ohlc(n_days=80, n_stocks=5)
    volume = pd.DataFrame(1000.0, index=close.index, columns=close.columns)
    turnover = close * volume
    mask = close.notna()
    mkt = pd.Series(np.linspace(100, 110, len(close)), index=close.index)
    sector = pd.Series(1.0, index=close.columns)
    fs = f3.build_feature_set(open_, high, low, close, volume, turnover, mask, mkt, sector, fund_feats=None)
    assert set(fs.raw) == set(f3.FAMILIES["A"]) | set(f3.FAMILIES["B"]) | set(f3.FAMILIES["C"]) | {"dollar_vol20"}
    assert "ret20" in fs.ranked and "vol20" in fs.raw
    # ranked 值域 (0,1]
    r = fs.ranked["ret20"].iloc[-1].dropna()
    assert r.min() > 0 and r.max() <= 1
    assert set(f3.FAMILIES["F"]) == set()                   # 無基本面時 F 空
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_level1_v3_features.py -q`
Expected: 4 new FAIL，`AttributeError: FeatureSet`

- [ ] **Step 3: 實作**

追加到 `features_v3.py`（import 區加 `from .features import rank_transform`）：

```python
# ── FeatureSet 與族群 ──

@dataclass
class FeatureSet:
    """ranked：已 rank (0,1]，組裝時缺值補 0.5；raw：原始值，缺值留 NaN。欄序 = ranked 後接 raw。"""
    ranked: dict[str, pd.DataFrame] = field(default_factory=dict)
    raw: dict[str, pd.DataFrame] = field(default_factory=dict)

    @property
    def names(self) -> list[str]:
        return list(self.ranked) + list(self.raw)

    def subset(self, names: list[str]) -> "FeatureSet":
        want = set(names)
        missing = want - set(self.names)
        if missing:
            raise KeyError(f"未知特徵：{sorted(missing)}")
        return FeatureSet(ranked={k: v for k, v in self.ranked.items() if k in want},
                          raw={k: v for k, v in self.raw.items() if k in want})


FUND_NAMES = ("rev_yoy", "rev_yoy_chg", "rev_yoy3", "eps_yoy_d", "gm_chg")

FAMILIES: dict[str, list[str]] = {
    "A": ["dist_limit_up", "lockup_days20", "gap_std20", "overnight_minus_intraday20"],
    "B": ["vol20", "vol60", "downside_vol20", "atr14_pct"],
    "C": ["mkt_ret5", "mkt_ret20", "mkt_vol20", "breadth_ma20", "dispersion"],
    "DE": ["ret1", "ret5", "ret20", "ret60", "ret20_ex5", "bias20", "pos240",
           "vr5_60", "amihud20", "sec_neutral_ret20", "dollar_vol20"],
    "F": [],   # build_feature_set 有基本面時填 FUND_NAMES
}

BASELINE: tuple[str, ...] = ("ret20", "vol20", "dollar_vol20", "mkt_ret20", "dist_limit_up")


def build_feature_set(open_, high, low, close, volume, turnover, in_universe, mkt_close,
                      sector_of, fund_feats: dict[str, pd.DataFrame] | None) -> FeatureSet:
    """五族全建。fund_feats 為 features.build_fundamental_features 的輸出（或 None）。"""
    to_rank, raw_de = build_direction_features(close, volume, turnover, in_universe, sector_of)
    if fund_feats:
        to_rank.update({k: fund_feats[k] for k in FUND_NAMES if k in fund_feats})
        FAMILIES["F"] = [k for k in FUND_NAMES if k in fund_feats]
    else:
        FAMILIES["F"] = []
    raw = {**build_execution_features(open_, close),
           **build_scale_features(high, low, close),
           **build_market_features(close, in_universe, mkt_close),
           **raw_de}
    raw = {k: v.where(in_universe).astype("float32") for k, v in raw.items()}
    return FeatureSet(ranked=rank_transform(to_rank, in_universe), raw=raw)


def assemble_v3(fs: FeatureSet, target: pd.DataFrame, dates: pd.Index,
                ) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    """(X, y, meta)。列 = (date, stock) 且 target 非 NaN。ranked 缺值 0.5、raw 缺值 NaN。"""
    y_long = target.loc[dates].stack(future_stack=True).dropna()
    idx = y_long.index
    cols: list[np.ndarray] = []
    for m in fs.ranked.values():
        v = m.loc[dates].stack(future_stack=True).reindex(idx).to_numpy(dtype=np.float32)
        cols.append(np.nan_to_num(v, nan=0.5))
    for m in fs.raw.values():
        cols.append(m.loc[dates].stack(future_stack=True).reindex(idx).to_numpy(dtype=np.float32))
    x = np.column_stack(cols).astype(np.float32) if cols else np.empty((len(idx), 0), np.float32)
    meta = idx.to_frame(index=False)
    meta.columns = ["date", "stock_id"]
    return x, y_long.to_numpy(dtype=np.float32), meta
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_level1_v3_features.py -q`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/research/level1/features_v3.py backend/tests/test_level1_v3_features.py
git commit -m "feat(level1): v3 FeatureSet／族群定義／assemble_v3（rank 補 0.5、raw 留 NaN）

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: 評估 v3——Top-K 淨報酬、q25 校準、不進場訊號

**Files:**
- Create: `backend/app/research/level1/evaluation_v3.py`
- Test: `backend/tests/test_level1_v3_evaluation.py`

**Interfaces:**
- Consumes: `evaluation.daily_rank_ic`, `evaluation.ic_summary`, `evaluation.daily_evaluation_n`
- Produces:
  - `topk_net_summary(score, net, ks=(20, 50)) -> dict`：每 k 有 `mean_net_pct, median_net_pct, day_win_rate（Top-K 當日均值 > 0 的日子占比）, excess_pct, n_days`
  - `calibration_q25(score, net, alpha=0.25) -> dict`：`breach_rate, abs_error_pp, n_cells`
  - `no_entry_summary(score, net) -> dict`：`n_days_flagged, share_flagged, univ_net_pct_flagged, univ_net_pct_other, diff_pp, top20_net_pct_flagged`
  - `evaluate_v3(score, net) -> dict`、`evaluate_v3_by_period(score, net, periods) -> dict`

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_level1_v3_evaluation.py
"""Level 1 v3 評估：q25 校準、Top-K 淨報酬、不進場訊號、walk-forward v3 embargo。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from app.research.level1 import evaluation_v3 as e3


def _mats(n_days=300, n_stocks=200, seed=0, sigma=0.03):
    rng = np.random.default_rng(seed)
    dates = pd.Index(pd.date_range("2025-01-01", periods=n_days).astype(str), name="date")
    cols = pd.Index([f"{1000+i}" for i in range(n_stocks)], name="stock_id")
    net = pd.DataFrame(rng.normal(0, sigma, (n_days, n_stocks)), index=dates, columns=cols)
    return dates, cols, net


def test_calibration_true_quantile_breaches_about_alpha():
    _, _, net = _mats()
    q25 = pd.DataFrame(norm.ppf(0.25) * 0.03, index=net.index, columns=net.columns)
    c = e3.calibration_q25(q25, net)
    assert abs(c["breach_rate"] - 0.25) < 0.01
    assert c["n_cells"] == net.size
    assert c["abs_error_pp"] < 1.0


def test_topk_net_perfect_score_beats_universe_and_win_rate():
    _, _, net = _mats(n_days=100)
    out = e3.topk_net_summary(net.copy(), net, ks=(20,))["top20"]
    assert out["mean_net_pct"] > 0
    assert out["excess_pct"] > 0
    assert out["day_win_rate"] == pytest.approx(1.0)
    assert out["n_days"] == 100


def test_topk_net_ignores_missing_net():
    _, _, net = _mats(n_days=50, n_stocks=60)
    score = net.copy()
    holed = net.copy()
    holed.iloc[:, :30] = np.nan                            # 前 30 檔未成交
    out = e3.topk_net_summary(score, holed, ks=(20,))["top20"]
    manual = holed.iloc[:, 30:].apply(lambda r: r.nlargest(20).mean(), axis=1).mean() * 100
    assert out["mean_net_pct"] == pytest.approx(round(float(manual), 3), abs=1e-3)


def test_no_entry_summary_flags_days_with_nonpositive_max():
    dates, cols, net = _mats(n_days=40, n_stocks=50)
    score = pd.DataFrame(0.01, index=dates, columns=cols)
    score.iloc[:10] = -0.01                                 # 前 10 日全負 → 旗標
    net.iloc[:10] = -0.05                                   # 那些日子池子確實跌
    out = e3.no_entry_summary(score, net)
    assert out["n_days_flagged"] == 10
    assert out["share_flagged"] == pytest.approx(0.25)
    assert out["univ_net_pct_flagged"] == pytest.approx(-5.0)
    assert out["diff_pp"] < 0                                # 旗標日比其他日差 → 訊號有用
    assert out["top20_net_pct_flagged"] == pytest.approx(-5.0)


def test_evaluate_v3_bundle_keys():
    _, _, net = _mats(n_days=80)
    out = e3.evaluate_v3(net.copy(), net)
    assert {"mean_ic", "topk", "calibration", "no_entry", "evaluation_n_mean"} <= set(out)
    by = e3.evaluate_v3_by_period(net.copy(), net, {"a": ("2025-01-01", "2025-02-01")})
    assert "a" in by and "topk" in by["a"]
```

（`scipy` 已隨 scikit-learn 安裝。）

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_level1_v3_evaluation.py -q`
Expected: FAIL，`cannot import name 'evaluation_v3'`

- [ ] **Step 3: 實作**

```python
# backend/app/research/level1/evaluation_v3.py
"""Level 1 v3 評估（設計 2026-09-28 §2.5）。

v2 的 Rank IC／分位單調是「相對排序」指標；v3 目標是絕對淨報酬的 q25，驗收改看：
1. Top-K 實際淨報酬與「絕對」勝率（當日 Top-K 均值 > 0）
2. q25 校準：預測下緣被跌破的比例應 ≈ 25%
3. 「不進場」訊號：Score_max ≤ 0 的日子，U_t 等權淨報酬是否顯著低於其他日
4. Rank IC 只當診斷

score 與 net 皆為矩陣（index=date, columns=stock_id）；net 已套 U_t 且未成交為 NaN，
score 在 net 缺值處被自然排除（與 v2 evaluation 同一慣例）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import evaluation as ev


def topk_net_summary(score: pd.DataFrame, net: pd.DataFrame,
                     ks: tuple[int, ...] = (20, 50)) -> dict:
    univ = net.mean(axis=1)
    rk = score.where(net.notna()).rank(axis=1, ascending=False, method="first")
    out = {}
    for k in ks:
        sel = net.where(rk <= k)
        daily = sel.mean(axis=1).dropna()
        excess = (daily - univ.loc[daily.index]).dropna()
        out[f"top{k}"] = {
            "mean_net_pct": round(float(daily.mean()) * 100, 3),
            "median_net_pct": round(float(sel.stack().median()) * 100, 3),
            "day_win_rate": round(float((daily > 0).mean()), 3),
            "excess_pct": round(float(excess.mean()) * 100, 3),
            "n_days": int(len(daily)),
        }
    return out


def calibration_q25(score: pd.DataFrame, net: pd.DataFrame, alpha: float = 0.25) -> dict:
    both = score.notna() & net.notna()
    n = int(both.to_numpy().sum())
    if n == 0:
        return {"alpha": alpha, "breach_rate": None, "abs_error_pp": None, "n_cells": 0}
    breach = ((net < score) & both).to_numpy().sum()
    rate = float(breach) / n
    return {"alpha": alpha, "breach_rate": round(rate, 4),
            "abs_error_pp": round(abs(rate - alpha) * 100, 2), "n_cells": n}


def no_entry_summary(score: pd.DataFrame, net: pd.DataFrame) -> dict:
    smax = score.where(net.notna()).max(axis=1)
    univ = net.mean(axis=1)
    valid = smax.notna() & univ.notna()
    flagged = (smax <= 0) & valid
    other = (smax > 0) & valid
    rk = score.where(net.notna()).rank(axis=1, ascending=False, method="first")
    top20 = net.where(rk <= 20).mean(axis=1)
    n_f, n_v = int(flagged.sum()), int(valid.sum())
    f_mean = float(univ[flagged].mean()) * 100 if n_f else None
    o_mean = float(univ[other].mean()) * 100 if other.any() else None
    return {
        "n_days_flagged": n_f,
        "share_flagged": round(n_f / n_v, 4) if n_v else None,
        "univ_net_pct_flagged": round(f_mean, 3) if f_mean is not None else None,
        "univ_net_pct_other": round(o_mean, 3) if o_mean is not None else None,
        "diff_pp": round(f_mean - o_mean, 3) if (f_mean is not None and o_mean is not None) else None,
        "top20_net_pct_flagged": round(float(top20[flagged].mean()) * 100, 3) if n_f else None,
    }


def evaluate_v3(score: pd.DataFrame, net: pd.DataFrame) -> dict:
    out = ev.ic_summary(ev.daily_rank_ic(score, net))          # 診斷用
    n_eval = ev.daily_evaluation_n(score, net)
    out["evaluation_n_mean"] = round(float(n_eval.mean()), 1)
    out["evaluation_n_min"] = int(n_eval.min())
    out["topk"] = topk_net_summary(score, net)
    out["calibration"] = calibration_q25(score, net)
    out["no_entry"] = no_entry_summary(score, net)
    return out


def evaluate_v3_by_period(score: pd.DataFrame, net: pd.DataFrame,
                          periods: dict[str, tuple[str, str]]) -> dict:
    out = {}
    for name, (a, b) in periods.items():
        idx = (score.index >= a) & (score.index <= b)
        if idx.sum() == 0:
            continue
        out[name] = evaluate_v3(score.loc[idx], net.loc[idx])
    return out
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_level1_v3_evaluation.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/research/level1/evaluation_v3.py backend/tests/test_level1_v3_evaluation.py
git commit -m "feat(level1): v3 評估——Top-K 淨報酬、q25 校準、不進場訊號

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: `walk_forward_scores_v3`

**Files:**
- Modify: `backend/app/research/level1/walkforward.py`
- Test: `backend/tests/test_level1_v3_evaluation.py`（追加）

**Interfaces:**
- Consumes: `features_v3.FeatureSet`, `features_v3.assemble_v3`
- Produces: `walk_forward_scores_v3(make_model, fs: FeatureSet, target, *, horizon, first_test=DEFAULT_FIRST_TEST, step=DEFAULT_STEP, min_train_days=MIN_TRAIN_DAYS) -> pd.DataFrame`（OOS 區塊有值，其餘 NaN；embargo = horizon）

- [ ] **Step 1: 追加失敗測試**

```python
# 追加到 backend/tests/test_level1_v3_evaluation.py
from app.research.level1 import walkforward as wf
from app.research.level1 import features_v3 as f3


class _ConstModel:
    def fit(self, x, y):
        self.c = float(np.nanmean(y))
    def predict(self, x):
        return np.full(len(x), self.c, dtype=np.float32)


def test_walk_forward_v3_embargo_and_raw_nan_passthrough():
    n_days, horizon = 80, 5
    dates = pd.Index(pd.date_range("2025-01-01", periods=n_days).astype(str), name="date")
    cols = pd.Index([f"{1000+i}" for i in range(35)], name="stock_id")
    rng = np.random.default_rng(1)
    net = pd.DataFrame(rng.normal(0, 0.03, (n_days, len(cols))), index=dates, columns=cols)
    raw = pd.DataFrame(rng.random((n_days, len(cols))), index=dates, columns=cols)
    raw.iloc[:, 0] = np.nan
    fs = f3.FeatureSet(ranked={}, raw={"x": raw})

    seen: list[tuple[pd.Index, np.ndarray]] = []
    orig = wf.assemble_v3

    def spy(fset, tgt, ds):
        x, y, meta = orig(fset, tgt, ds)
        seen.append((ds, x))
        return x, y, meta

    wf.assemble_v3 = spy
    try:
        score = wf.walk_forward_scores_v3(
            _ConstModel, fs, net, horizon=horizon,
            first_test=str(dates[40]), step=20, min_train_days=10)
    finally:
        wf.assemble_v3 = orig

    trains = seen[0::2]
    for (tr, x), s in zip(trains, [40, 60]):
        assert list(dates).index(tr[-1]) + horizon < s     # embargo 硬規則
        assert np.isnan(x).any()                              # raw NaN 沒被補成數字
    assert score.loc[dates[39]].isna().all()
    assert score.loc[dates[41]].notna().any()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_level1_v3_evaluation.py::test_walk_forward_v3_embargo_and_raw_nan_passthrough -q`
Expected: FAIL，`AttributeError: module ... has no attribute 'assemble_v3'`

- [ ] **Step 3: 實作**

在 `walkforward.py` 的 import 區加：

```python
from .features_v3 import FeatureSet, assemble_v3
```

檔尾追加：

```python
def walk_forward_scores_v3(
    make_model: Callable[[], object],
    fs: FeatureSet,
    target: pd.DataFrame,
    *,
    horizon: int,
    first_test: str = DEFAULT_FIRST_TEST,
    step: int = DEFAULT_STEP,
    min_train_days: int = MIN_TRAIN_DAYS,
) -> pd.DataFrame:
    """v3 版：吃 FeatureSet（ranked 補 0.5／raw 留 NaN），標籤為 R_net。embargo 規則與 v2 同源。"""
    dates = target.index
    score = pd.DataFrame(np.nan, index=dates, columns=target.columns, dtype="float32")
    for s, e in test_blocks(dates, first_test, step):
        tr_dates = train_slice(dates, s, horizon)
        if len(tr_dates) < min_train_days:
            continue
        x_tr, y_tr, _ = assemble_v3(fs, target, tr_dates)
        if not len(y_tr):
            continue
        model = make_model()
        model.fit(x_tr, y_tr)
        te_dates = dates[s:e]
        x_te, _, meta = assemble_v3(fs, target, te_dates)
        if not len(meta):
            continue
        pred = pd.Series(model.predict(x_te), name="score",
                         index=pd.MultiIndex.from_frame(meta))
        block = pred.unstack("stock_id")
        score.loc[block.index, block.columns] = block.astype("float32")
    return score
```

- [ ] **Step 4: 跑全部 Level 1 測試確認通過（含 v2 既有）**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_level1_v3_evaluation.py tests/test_level1_eval_walkforward.py -q`
Expected: 全綠（v2 的 embargo 測試不受影響）

- [ ] **Step 5: Commit**

```bash
git add backend/app/research/level1/walkforward.py backend/tests/test_level1_v3_evaluation.py
git commit -m "feat(level1): walk_forward_scores_v3（FeatureSet、R_net 標籤、embargo 同源）

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Ablation 腳本——基準線先跑

**Files:**
- Create: `backend/scripts/level1_v3_ablation.py`

**Interfaces:**
- Consumes: `data/level1_v3_targets.pkl`、`features_v3.build_feature_set / FAMILIES / BASELINE`、`walkforward.walk_forward_scores_v3`、`evaluation_v3.evaluate_v3_by_period`、`features.build_fundamental_features`
- Produces: `data/level1_v3_results.json`，結構：
  ```
  {"generated_at", "spec", "periods", "seeds", "model_params",
   "steps": ["baseline", "+A", "+B", "+C", "+DE", "+F"],
   "features_by_step": {step: [names]},
   "horizons": {"1": {step: {"seeds": {seed: {"dev_oos": {...}, "holdout": {...}}},
                             "dev_summary": {"top20_mean_net_pct": {"mean","std"},
                                             "calibration_abs_error_pp": {...},
                                             "no_entry_diff_pp": {...}},
                             "delta_vs_prev": {"top20_mean_net_pct": float,
                                               "noise_floor": float,
                                               "passes": bool}}}}}
  ```

- [ ] **Step 1: 寫腳本**

```python
# backend/scripts/level1_v3_ablation.py
"""Level 1 v3 基準線＋逐族 ablation（設計 2026-09-28 §2.5）。

步驟累積：baseline(5) → +A → +B → +C → +DE → +F。每步 × horizon × 三種子跑 walk-forward，
只印 dev（2022~2024）；holdout 算了存進 JSON 但不印、不拿來選（只讀不調）。

增量判定：Top-20 dev 淨報酬相對前一步的差 > noise_floor（= 兩步三種子 std 的較大者）才算過。
種子要有離散，LightGBM 必須開 subsample/colsample（否則 n_jobs=1 下三種子完全相同）。

用法：cd backend && .venv/Scripts/python.exe -m scripts.level1_v3_ablation [--steps baseline,+A] [--horizons 1,5,10]
"""

from __future__ import annotations

import argparse
import json
import pickle
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn.linear_model  # noqa: F401  # 必須先於 lightgbm：Windows OpenMP DLL 順序坑
from lightgbm import LGBMRegressor

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.research.level1 import evaluation_v3 as e3  # noqa: E402
from app.research.level1 import features as ft  # noqa: E402
from app.research.level1 import features_v3 as f3  # noqa: E402
from app.research.level1 import prices as pr  # noqa: E402
from app.research.level1 import walkforward as wf  # noqa: E402

_DATA = Path(__file__).resolve().parents[1] / "data"
PERIODS = {"dev_oos": ("2022-01-01", "2024-12-31"), "holdout": ("2025-01-01", "2026-12-31")}
SEEDS = (42, 7, 2024)
STEPS = ["baseline", "+A", "+B", "+C", "+DE", "+F"]
MODEL_PARAMS = dict(objective="quantile", alpha=0.25, n_estimators=100,
                    subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                    n_jobs=1, verbose=-1)


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _make_model(seed: int):
    return lambda: LGBMRegressor(random_state=seed, **MODEL_PARAMS)


def _features_for_step(step: str, prev: list[str]) -> list[str]:
    if step == "baseline":
        return list(f3.BASELINE)
    fam = step.lstrip("+")
    return prev + [n for n in f3.FAMILIES[fam] if n not in prev]


def _assert_snapshot_fresh(meta: dict) -> None:
    con = sqlite3.connect(_DATA / "twa.db")
    db_max = con.execute("SELECT max(date) FROM daily_prices").fetchone()[0]
    con.close()
    if meta.get("db_max_date") != db_max:
        raise SystemExit(f"level1_v3_targets.pkl 建於 {meta.get('db_max_date')}，DB 為 {db_max}；"
                         "請先重跑 scripts.level1_v3_targets。")


def _mean_std(vals: list[float]) -> dict:
    a = np.array([v for v in vals if v is not None], dtype=float)
    return {"mean": round(float(a.mean()), 4), "std": round(float(a.std(ddof=0)), 4)} if len(a) else {"mean": None, "std": None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", default=",".join(STEPS))
    ap.add_argument("--horizons", default="1,5,10")
    ap.add_argument("--out", default=str(_DATA / "level1_v3_results.json"))
    args = ap.parse_args()
    steps = [s for s in STEPS if s in set(args.steps.split(","))]
    horizons = [int(h) for h in args.horizons.split(",")]

    p = pickle.load(open(_DATA / "level1_v3_targets.pkl", "rb"))
    _assert_snapshot_fresh(p["meta"])
    close, mask = p["close"].astype("float64"), p["universe"]
    f64 = {k: p[k].astype("float64") for k in ("open", "high", "low", "volume", "turnover")}
    con = sqlite3.connect(_DATA / "twa.db")
    mkt = pr.load_market_close(con)
    sector = pr.load_sector_map(con)
    con.close()
    from app.storage.database import SessionLocal
    with SessionLocal() as s:
        fund = ft.build_fundamental_features(s, close.index, close.columns)
    fs_all = f3.build_feature_set(f64["open"], f64["high"], f64["low"], close, f64["volume"],
                                  f64["turnover"], mask, mkt, sector, fund)
    _log(f"特徵全集 {len(fs_all.names)} 個；族群：" + ", ".join(f"{k}={len(v)}" for k, v in f3.FAMILIES.items()))

    out_path = Path(args.out)
    results = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    results.update({"generated_at": datetime.now().isoformat(timespec="seconds"),
                    "spec": "v3 §2.5 baseline→+A→+B→+C→+DE→+F，q25，三種子，dev 只印",
                    "periods": PERIODS, "seeds": list(SEEDS), "model_params": MODEL_PARAMS,
                    "steps": STEPS, "holdout_read_only": True})
    results.setdefault("features_by_step", {})
    results.setdefault("horizons", {})

    for n in horizons:
        net = p["targets"][n]["net"].astype("float64")
        hres = results["horizons"].setdefault(str(n), {})
        prev_names: list[str] = []
        prev_key = None
        _log(f"── horizon {n}D ──")
        for step in STEPS:
            names = _features_for_step(step, prev_names)
            results["features_by_step"][step] = names
            if step not in steps:
                prev_names, prev_key = names, step if step in hres else prev_key
                continue
            fs = fs_all.subset(names)
            t0 = time.time()
            seeds_out = {}
            for seed in SEEDS:
                score = wf.walk_forward_scores_v3(_make_model(seed), fs, net, horizon=n)
                seeds_out[str(seed)] = e3.evaluate_v3_by_period(score, net, PERIODS)
            dev = [v["dev_oos"] for v in seeds_out.values() if "dev_oos" in v]
            summ = {
                "top20_mean_net_pct": _mean_std([d["topk"]["top20"]["mean_net_pct"] for d in dev]),
                "top20_day_win_rate": _mean_std([d["topk"]["top20"]["day_win_rate"] for d in dev]),
                "calibration_abs_error_pp": _mean_std([d["calibration"]["abs_error_pp"] for d in dev]),
                "no_entry_diff_pp": _mean_std([d["no_entry"]["diff_pp"] for d in dev]),
                "mean_ic": _mean_std([d["mean_ic"] for d in dev]),
            }
            entry = {"features": names, "seeds": seeds_out, "dev_summary": summ}
            if prev_key and prev_key in hres:
                pm = hres[prev_key]["dev_summary"]["top20_mean_net_pct"]
                cur = summ["top20_mean_net_pct"]
                floor = max(pm["std"] or 0.0, cur["std"] or 0.0)
                delta = round(cur["mean"] - pm["mean"], 4)
                entry["delta_vs_prev"] = {"vs": prev_key, "top20_mean_net_pct": delta,
                                          "noise_floor": round(floor, 4), "passes": bool(delta > floor)}
            hres[step] = entry
            d = entry.get("delta_vs_prev", {})
            _log(f"  {step:<9} n_feat={len(names):>2} top20={summ['top20_mean_net_pct']['mean']:+.3f}%"
                 f"±{summ['top20_mean_net_pct']['std']:.3f} win={summ['top20_day_win_rate']['mean']:.3f} "
                 f"cal_err={summ['calibration_abs_error_pp']['mean']:.2f}pp "
                 f"no_entry_diff={summ['no_entry_diff_pp']['mean']} ic={summ['mean_ic']['mean']:+.4f} "
                 f"| Δ={d.get('top20_mean_net_pct')} floor={d.get('noise_floor')} pass={d.get('passes')} "
                 f"({time.time()-t0:.0f}s)")
            prev_names, prev_key = names, step
        out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    _log(f"已存 {out_path.name}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 只跑基準線、單一 horizon 驗證管線**

Run: `cd backend && .venv/Scripts/python.exe -m scripts.level1_v3_ablation --steps baseline --horizons 5`
Expected: 印出 `baseline n_feat= 5 top20=…` 一行，`data/level1_v3_results.json` 產生。若 `cal_err` > 10pp 且 `top20` ≤ 0，**先停下來查標籤**（設計 §5 步驟 2 停損），不要往下加特徵。

- [ ] **Step 3: 跑基準線全 horizon**

Run: `cd backend && .venv/Scripts/python.exe -m scripts.level1_v3_ablation --steps baseline`
Expected: 三個 horizon 各一行。

- [ ] **Step 4: Lint 與 commit（腳本入版控，JSON 不入）**

Run: `cd backend && .venv/Scripts/python.exe -m ruff check app/research/level1 scripts/level1_v3_ablation.py scripts/level1_targets_v3.py tests`
Expected: All checks passed

```bash
git add backend/scripts/level1_v3_ablation.py
git commit -m "feat(level1): v3 ablation 腳本（基準線→逐族、q25、三種子、dev 只印）

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: 逐族 ablation 全跑＋findings 文件

**Files:**
- Create: `docs/level1-v3-ablation-findings.md`
- Reads: `backend/data/level1_v3_results.json`、`backend/data/level1_v3_targets.pkl` 的 `meta.fill_report`

- [ ] **Step 1: 全步驟全 horizon 跑完**

Run: `cd backend && .venv/Scripts/python.exe -m scripts.level1_v3_ablation`
Expected: 6 步 × 3 horizon 各一行（約 30~60 分鐘）。腳本每個 horizon 結束都會寫 JSON，中斷可用 `--steps` 補跑缺的步。

- [ ] **Step 2: 用腳本把 JSON 攤成表格（禁手寫數字）**

Run:
```bash
cd backend && .venv/Scripts/python.exe -c "
import json,pickle
r=json.load(open('data/level1_v3_results.json',encoding='utf-8'))
m=pickle.load(open('data/level1_v3_targets.pkl','rb'))['meta']
print('## fill_report'); [print(y,v) for y,v in m['fill_report'].items()]
for h,steps in r['horizons'].items():
    print(f'\n## {h}D')
    print('| step | n_feat | top20 net% (dev, mean±std) | win | cal_err pp | no_entry diff pp | IC | Δ | floor | pass |')
    print('|---|---|---|---|---|---|---|---|---|---|')
    for s in r['steps']:
        if s not in steps: continue
        e=steps[s]; d=e['dev_summary']; dv=e.get('delta_vs_prev',{})
        print(f\"| {s} | {len(e['features'])} | {d['top20_mean_net_pct']['mean']:+.3f}±{d['top20_mean_net_pct']['std']:.3f} | {d['top20_day_win_rate']['mean']:.3f} | {d['calibration_abs_error_pp']['mean']:.2f} | {d['no_entry_diff_pp']['mean']} | {d['mean_ic']['mean']:+.4f} | {dv.get('top20_mean_net_pct','—')} | {dv.get('noise_floor','—')} | {dv.get('passes','—')} |\")
"
```
Expected: 一份 fill_report 與三張 markdown 表。

- [ ] **Step 3: 寫 findings 文件**

`docs/level1-v3-ablation-findings.md` 內容骨架（表格直接貼 Step 2 輸出；裁定欄位依規則填，不得憑感覺）：

```markdown
# Level 1 v3 Ablation Findings

日期：<執行日>
來源：backend/data/level1_v3_results.json（generated_at <JSON 的 generated_at>）、level1_v3_targets.pkl meta
設計：docs/superpowers/specs/2026-09-28-level1-v3-target-features-design.md §2.5
紀律：dev 2022–2024 三種子；holdout 未看；所有數字由 JSON 帶出

## 0. 標籤現況
<貼 fill_report：逐年漲停未成交率／停牌率>
解讀：未成交率 X% 落在 v2 觀察的市場基率（~1.5%）量級 → 標籤合理／不合理（擇一，附理由）。

## 1. 基準線（5 特徵，q25）
<三 horizon 的 baseline 行>
- q25 校準 abs_error：<值>pp（門檻 ±5pp）→ 校準／未校準
- Top-20 dev 淨報酬是否 > 0：<是/否>
- 「不進場」訊號 diff_pp：<值>（負值＝旗標日確實較差）

## 2. 逐族增量
<三張表>

### 裁定（規則：Δ > noise_floor 且三 horizon 至少兩個一致 → keep；Δ ≤ floor → drop；方向不一致 → insufficient evidence）
| 族 | 1D | 5D | 10D | 裁定 | 一句話理由 |
|---|---|---|---|---|---|
| A 成交／隔夜 | | | | | |
| B 尺度 | | | | | |
| C 大盤寬度 | | | | | |
| DE 方向流動性 | | | | | |
| F 基本面 | | | | | |

## 3. 與 v2 的對照（只能定性）
v2 是百分位目標、Rank IC 驗收；v3 是絕對淨報酬、Top-20 淨報酬驗收，數字不可直接比。
只記：v3 baseline 的 IC 量級 <值> vs v2 holdout IC 0.06~0.08（同為診斷）。

## 4. 負結果與風險
- <哪些族沒過、可能原因（機制不成立／被 baseline 已涵蓋／樣本期 regime）>
- 副作用確認：Top-20 內 dist_limit_up 分布是否明顯偏離全池（漲停股被看衰是否如設計預期）——用 JSON 無法直接看，列為下一步。

## 5. 下一步（優先序）
1. 進 W2（ctx_matrix 池）機制過濾 ≤ 15 個 → 同協定加一步 "+W2"
2. 消息面資料層完成後 → "+W4" 獨立閘門（另一計畫）
3. 通過族 → v3 凍結與產品化（另一計畫）
```

- [ ] **Step 4: Commit**

```bash
git add docs/level1-v3-ablation-findings.md
git commit -m "docs(level1): v3 基準線與逐族 ablation findings（數字由 results.json 帶出）

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 5: 全套測試最後一次**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: 全綠（既有 248 條 + 本計畫新增 ~22 條）。

---

## Self-Review 結果

**Spec coverage（§0–2、§5 步驟 1–3）**
- §1.1 成交規則四種情況 → Task 2（漲停未成交、停牌 NO_TRADE、跌停視為成交、C(t+N) 缺 → NaN）✓
- §1.2 成本常數 → `COST_RT` ✓；滑價由 `amihud20` 代理 → Task 5 ✓
- §1.3 U_t 不變、E 多一個已成交條件 → `build_targets_v3` 套 mask；未成交 NaN 自然排除 ✓
- §2.1 表示法二選一、raw 不補 0、移除 `_bull` → Task 6（`assemble_v3`、`FAMILIES` 測試釘住）✓
- §2.3 A/B/C/D/E 全部特徵名與公式 → Task 4、5 ✓（`overnight_minus_intraday20` 用差值避免分母趨零 ✓；寬度 U_t-only 有測試 ✓）
- §2.5 基準線 5 特徵、逐族、三種子、四個數字、噪音門檻、holdout 只讀 → Task 7、9、10 ✓
- §2.5 交互檢查（A×N、C×個股）→ **未納入本計畫**：A×N 由三 horizon 分開跑天然涵蓋；C×個股的四格比較留到 W2 前、依 findings 決定是否值得，寫進 findings §5。
- §3 消息面、§4 ledger/pipeline/前端 → 明確排除，另開計畫。

**Placeholder scan**：Task 10 findings 骨架的 `<…>` 是「由 JSON 帶出」的填空位，不是實作 placeholder；其他任務無 TBD。

**Type consistency**：
- `FeatureSet.subset` 回傳順序 ranked→raw，與 `assemble_v3` 欄序一致 ✓
- `build_direction_features` 回 `(to_rank, raw)`，`build_feature_set` 依此解包 ✓
- `walk_forward_scores_v3` 的 `make_model` 是「回傳模型的可呼叫」，Task 9 `_make_model(seed)` 回 lambda ✓；Task 8 測試傳 `_ConstModel` 類別（呼叫即建構）✓
- `evaluate_v3_by_period` 鍵 `dev_oos/holdout` 與 Task 9 `PERIODS` 一致 ✓
- `FAMILIES["F"]` 在 `build_feature_set` 內被改寫（模組層可變狀態）——刻意，因基本面覆蓋要跑過 DB 才知道；測試 `test_build_feature_set_representation_by_role` 釘住無基本面時為空。
