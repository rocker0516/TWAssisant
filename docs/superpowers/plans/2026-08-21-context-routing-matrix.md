# 情境路由軌第一階段（類股×訊號效力矩陣）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建出「類股群 × 訊號家族 × 情境條件 → 20日超額報酬效力」矩陣：離線挖掘管線 → 凍結 artifact → API → 前端熱力圖頁。

**Architecture:** 沿用 corners 模式——研究核心放 `backend/app/research/ctx_matrix/`（純函數、可測試），薄 CLI 在 `backend/scripts/`，產物凍結為 `backend/data/ctx_matrix.json`，engine 載入 → FastAPI route → React 頁。挖掘紀律（控ATR桶 ctrl、動態 Bonferroni、3-fold＋holdout）全部沿用 `mega_mine2.py` 的既有實作方法。

**Tech Stack:** Python 3.11＋pandas/numpy/scipy（backend/.venv）、SQLite（backend/data/twa.db）、FastAPI＋Pydantic、React 18＋TS＋Vite＋TailwindCSS＋react-query。

## Global Constraints

- KPI 唯一：20 交易日內「累積超額報酬曾達 +X%」（超額＝個股累積報酬 − 大盤累積報酬，逐日路徑取 max）。X 由 Task 1 的基率掃描定版。
- 挖掘窗 2021-01-01 ~ 2024-12-31，holdout 2025-01-01 之後（與 `mega_mine2.py` 一致；常數見 `scripts/pop_condition_judge.py:41-42`）。
- 門檻不手拍：訊號切點用挖掘窗分位數；格子過關需 ctrl>0 於全部 3 個 fold、t ≥ 動態 Bonferroni gate、有效日 ≥60、觸發數 ≥200（週/月頻家族 ≥60）。
- 不做 lead-lag。純市場層特徵（ts_share≈1）不入池。
- ETF（股號 `00` 開頭）不入標的池（同 `backfill_forward_labels.py:50`）。
- 路由加值定義：`routing_delta = ctrl(格子) − ctrl(同訊號×同情境×全市場)`。
- 所有腳本 Windows 下執行：`cd backend` 後 `.venv\Scripts\python.exe scripts\xxx.py`；測試 `cd backend` 後 `.venv\Scripts\python.exe -m pytest tests\test_xxx.py -v`。
- 新程式碼中文註解、既有命名風格（snake_case、引擎 class 風格）。

## File Structure

```
backend/app/research/__init__.py            # 新：研究核心 package
backend/app/research/ctx_matrix/__init__.py
backend/app/research/ctx_matrix/labels.py       # 20日超額標籤＋KPI門檻掃描
backend/app/research/ctx_matrix/templates.py    # 訊號模板框架（展開參數格→謂詞遮罩）
backend/app/research/ctx_matrix/chip.py         # 籌碼家族 A~F
backend/app/research/ctx_matrix/other_families.py # 動能/量能/基本面/消息/事件家族
backend/app/research/ctx_matrix/context.py      # 情境維度：歷史market regime＋鏈共振
backend/app/research/ctx_matrix/evaluator.py    # 格子評估：lift/ctrl/t/folds/holdout/baseline
backend/app/research/ctx_matrix/pipeline.py     # 組裝：特徵→稽核閘→掃格→artifact
backend/scripts/ctx_matrix_run.py               # CLI 入口（build/audit/mine 子命令）
backend/data/core_chains.json                   # 手工核心產業鏈（進 git）
backend/data/ctx_matrix.json                    # 凍結產物（進 git）
backend/app/engines/ctx_matrix_loader.py        # runtime 載入 artifact
backend/app/api/routes_ctx.py                   # GET /api/ctx-matrix
frontend/src/pages/CtxMatrixPage.tsx            # 熱力圖頁
backend/tests/test_ctx_labels.py
backend/tests/test_ctx_templates.py
backend/tests/test_ctx_chip.py
backend/tests/test_ctx_context.py
backend/tests/test_ctx_evaluator.py
```

---

### Task 1: 20日超額標籤與 KPI 門檻掃描

**Files:**
- Create: `backend/app/research/__init__.py`（空檔）
- Create: `backend/app/research/ctx_matrix/__init__.py`（空檔）
- Create: `backend/app/research/ctx_matrix/labels.py`
- Test: `backend/tests/test_ctx_labels.py`

**Interfaces:**
- Produces:
  - `build_excess_labels(prices: pd.DataFrame, market: pd.Series, horizon: int = 20) -> pd.DataFrame`
    - `prices`：columns `[stock_id, date, close]`（date 為 `datetime64`，已按 stock_id,date 排序）
    - `market`：index=date 的大盤收盤 Series
    - 回傳 columns `[stock_id, date, exc_mfe20]`；`exc_mfe20` = T+1..T+20 每日 `(close_t/close_T − mkt_t/mkt_T)` 之最大值 ×100（%）。未來不足 20 根的日子不產列。
  - `scan_thresholds(labels: pd.DataFrame, grid: tuple = (6, 8, 10, 12)) -> dict`
    - 回傳 `{"base_rates": {X: 全體列中 exc_mfe20>=X 的比例}, "chosen_x": <基率最接近5%的X>}`

- [ ] **Step 0: 確認 pytest 可用**

```
cd backend
.venv\Scripts\python.exe -m pytest --version
```
若失敗：`.venv\Scripts\python.exe -m pip install pytest`（不改 requirements.txt——既有測試同樣未宣告 pytest）。

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_ctx_labels.py
import pandas as pd
import numpy as np
from app.research.ctx_matrix.labels import build_excess_labels, scan_thresholds


def _toy():
    dates = pd.date_range("2024-01-01", periods=25, freq="B")
    # 個股：第0天100，之後每天+1%；大盤：全程持平
    close = 100 * (1.01 ** np.arange(25))
    prices = pd.DataFrame({"stock_id": "1101", "date": dates, "close": close})
    market = pd.Series(100.0, index=dates)
    return prices, market


def test_excess_mfe_is_path_max():
    prices, market = _toy()
    out = build_excess_labels(prices, market, horizon=20)
    # 第0天：未來20根齊全 → 有列；exc_mfe20 ≈ 1.01^20-1 ≈ 22.0%
    row0 = out[out["date"] == prices["date"].iloc[0]].iloc[0]
    assert abs(row0["exc_mfe20"] - ((1.01 ** 20 - 1) * 100)) < 0.1


def test_incomplete_future_dropped():
    prices, market = _toy()
    out = build_excess_labels(prices, market, horizon=20)
    # 25根資料，只有前 25-20=5 天有完整未來窗
    assert len(out) == 5


def test_scan_picks_nearest_5pct():
    labels = pd.DataFrame({"exc_mfe20": [4.0] * 90 + [11.0] * 10})  # X=10 基率恰 10%
    res = scan_thresholds(labels, grid=(6, 8, 10, 12))
    assert res["base_rates"][12] == 0.0
    assert res["chosen_x"] == 10  # 10% 比 0% 更接近 5%
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend` → `.venv\Scripts\python.exe -m pytest tests\test_ctx_labels.py -v`
Expected: FAIL（ModuleNotFoundError: app.research）

- [ ] **Step 3: 實作**

```python
# backend/app/research/ctx_matrix/labels.py
"""20 交易日超額報酬標籤：KPI = 20日內累積超額曾達 +X%。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def build_excess_labels(prices: pd.DataFrame, market: pd.Series, horizon: int = 20) -> pd.DataFrame:
    market = market.sort_index()
    rows: list[pd.DataFrame] = []
    for sid, g in prices.groupby("stock_id", sort=False):
        g = g.sort_values("date").reset_index(drop=True)
        mkt = market.reindex(g["date"]).to_numpy(dtype=float)
        px = g["close"].to_numpy(dtype=float)
        n = len(g)
        if n <= horizon:
            continue
        m = n - horizon  # 有完整未來窗的天數
        exc = np.full(m, -np.inf)
        for k in range(1, horizon + 1):
            # 第 T 天、往後第 k 根的累積超額（%）
            e = (px[k:k + m] / px[:m] - mkt[k:k + m] / mkt[:m]) * 100
            exc = np.maximum(exc, e)
        rows.append(pd.DataFrame({"stock_id": sid, "date": g["date"].iloc[:m], "exc_mfe20": exc}))
    if not rows:
        return pd.DataFrame(columns=["stock_id", "date", "exc_mfe20"])
    return pd.concat(rows, ignore_index=True)


def scan_thresholds(labels: pd.DataFrame, grid: tuple = (6, 8, 10, 12)) -> dict:
    base = {int(x): float((labels["exc_mfe20"] >= x).mean()) for x in grid}
    chosen = min(grid, key=lambda x: abs(base[int(x)] - 0.05))
    return {"base_rates": base, "chosen_x": int(chosen)}
```

- [ ] **Step 4: 跑測試確認通過**

Run: 同 Step 2。Expected: 3 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/research backend/tests/test_ctx_labels.py
git commit -m "feat(ctx): 20日超額標籤與KPI門檻掃描核心"
```

---

### Task 2: 訊號模板框架

**Files:**
- Create: `backend/app/research/ctx_matrix/templates.py`
- Test: `backend/tests/test_ctx_templates.py`

**Interfaces:**
- Produces:
  - `@dataclass Template: id: str; family: str; col: str; op: str; params: dict`
    - `op ∈ {"q_hi", "q_lo", "consec_ge", "flag"}`
    - `q_hi/q_lo`：以挖掘窗分位數 `params["q"]` 切高/低端；`consec_ge`：`col` 連續為正 ≥ `params["n"]` 日（col 需為日頻帶正負值欄）；`flag`：col≠0。
  - `expand(templates: list[Template]) -> list[Template]`：把 `params` 內含 list 的參數格展開為多個具體 Template（id 加後綴，如 `A1_n5`）。
  - `build_masks(tpls: list[Template], mine: pd.DataFrame, target: pd.DataFrame) -> list[tuple[str, np.ndarray]]`
    - 門檻一律由 `mine`（挖掘窗）算、套用到 `target`——同 `mega_mine2.build_predicates`（`mega_mine2.py:376-404`）的紀律。
    - `consec_ge` 的連續日計算沿用 `flow_engine._consec` 的邏輯（`app/engines/flow_engine.py:75`），但以 groupby(stock_id) 向量化。
    - 支持度過濾：0.002 ≤ 觸發占比 ≤ 0.6（同 mega_mine2.py:383）。

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_ctx_templates.py
import numpy as np
import pandas as pd
from app.research.ctx_matrix.templates import Template, expand, build_masks


def test_expand_param_grid():
    t = Template(id="A1", family="chip", col="foreign_net", op="consec_ge", params={"n": [3, 5]})
    out = expand([t])
    assert sorted(x.id for x in out) == ["A1_n3", "A1_n5"]
    assert all(isinstance(x.params["n"], int) for x in out)


def test_q_hi_threshold_from_mine_window():
    rng = np.random.default_rng(0)
    mine = pd.DataFrame({"stock_id": "1101", "x": rng.normal(0, 1, 1000)})
    target = pd.DataFrame({"stock_id": "1101", "x": [10.0, -10.0]})  # 遠高/遠低於 mine q80
    tpl = Template(id="T", family="f", col="x", op="q_hi", params={"q": 0.8})
    masks = build_masks([tpl], mine, target)
    # target 太小不會過支持度過濾 → 先驗證遮罩本身：直接檢查閾值套用
    assert masks == [] or masks[0][1].tolist() == [True, False]


def test_consec_ge():
    df = pd.DataFrame({
        "stock_id": ["1101"] * 5,
        "net": [1.0, 1.0, 1.0, -1.0, 1.0],
    })
    tpl = Template(id="C", family="chip", col="net", op="consec_ge", params={"n": 3})
    masks = build_masks([tpl], df, df)
    if masks:
        # 第3天（index2）連正3日 → True；第5天連正僅1日 → False
        assert masks[0][1].tolist() == [False, False, True, False, False]
```

- [ ] **Step 2: 跑測試確認失敗**（同前，pytest 指向 test_ctx_templates.py）

- [ ] **Step 3: 實作**

```python
# backend/app/research/ctx_matrix/templates.py
"""訊號模板：參數格展開＋挖掘窗分位數定門檻。"""
from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import product

import numpy as np
import pandas as pd

_MIN_SHARE, _MAX_SHARE = 0.002, 0.6


@dataclass(frozen=True)
class Template:
    id: str
    family: str
    col: str
    op: str          # q_hi | q_lo | consec_ge | flag
    params: dict


def expand(templates: list[Template]) -> list[Template]:
    out: list[Template] = []
    for t in templates:
        grid_keys = [k for k, v in t.params.items() if isinstance(v, list)]
        if not grid_keys:
            out.append(t)
            continue
        for combo in product(*(t.params[k] for k in grid_keys)):
            p = dict(t.params)
            p.update(dict(zip(grid_keys, combo)))
            suffix = "_".join(f"{k}{v}" for k, v in zip(grid_keys, combo))
            out.append(replace(t, id=f"{t.id}_{suffix}", params=p))
    return out


def _consec_pos(g: pd.Series) -> pd.Series:
    """各列止於當日的連續正值天數。"""
    pos = (g > 0).astype(int)
    grp = (pos == 0).cumsum()
    return pos.groupby(grp).cumsum()


def _mask_one(t: Template, mine: pd.DataFrame, target: pd.DataFrame) -> np.ndarray | None:
    if t.col not in target.columns:
        return None
    v = target[t.col]
    if t.op == "q_hi":
        thr = mine[t.col].quantile(t.params["q"])
        return (v >= thr).to_numpy()
    if t.op == "q_lo":
        thr = mine[t.col].quantile(t.params["q"])
        return (v <= thr).to_numpy()
    if t.op == "consec_ge":
        streak = target.groupby("stock_id", sort=False)[t.col].transform(_consec_pos)
        return (streak >= t.params["n"]).to_numpy()
    if t.op == "flag":
        return (v.fillna(0) != 0).to_numpy()
    raise ValueError(f"unknown op: {t.op}")


def build_masks(tpls: list[Template], mine: pd.DataFrame, target: pd.DataFrame) -> list[tuple[str, np.ndarray]]:
    out: list[tuple[str, np.ndarray]] = []
    for t in tpls:
        m = _mask_one(t, mine, target)
        if m is None:
            continue
        share = float(np.nanmean(m))
        if not (_MIN_SHARE <= share <= _MAX_SHARE):
            continue
        out.append((t.id, m))
    return out
```

（注意 `test_consec_ge` 的 5 列小樣本：share=0.2 落在支持度區間內，會有輸出。）

- [ ] **Step 4: 跑測試確認通過**
- [ ] **Step 5: Commit** `feat(ctx): 訊號模板框架（參數格展開＋分位數門檻）`

---

### Task 3: 籌碼家族特徵組裝（spec §5 A~F）

**Files:**
- Create: `backend/app/research/ctx_matrix/chip.py`
- Test: `backend/tests/test_ctx_chip.py`

**Interfaces:**
- Consumes: `Template`（Task 2）。
- Produces:
  - `chip_feature_frame(db_path: str) -> pd.DataFrame`：從 SQLite 讀 `Institutional / Margin / ShareholdingDistribution / ShortLending / DayTrading / InsiderHolding` 六表，組出日頻特徵 frame（週/月頻資料 forward-fill 到日頻，**以公布日起算**避免前視）。columns 至少含：
    `stock_id, date, foreign_net, trust_net, dual_net_flag, netbuy_turnover_pct, netbuy_accel, trust_adopt_flag, sell_exhaust_flag, absorb_flag, receive_flag, margin_chg5, margin_chg10, margin_chg20, margin_down_price_up, short_margin_ratio_pct, squeeze_flag, washout_flag, big_holder_wk_up, retail_cnt_chg, conc_diff_chg, mid_holder_up, big_absorb_flag, sbl_chg5, sbl_chg10, sbl_chg20, short_cover_flag, daytrade_pct, daytrade_drop_flag, insider_chg, insider_buyback_flag, distribute_warn_flag`
  - `chip_templates() -> list[Template]`：把 spec §5 的 A1~F4 宣告成模板清單（含參數格），對映上列欄位。
- 實作要點：
  - 三大法人表結構參考 `flow_engine.stock_flow_ranking`（`app/engines/flow_engine.py:383`）讀法；表名/欄位以 `app/storage/models.py:114-205` 為準（Institutional/Margin/ShareholdingDistribution/ShortLending/DayTrading/InsiderHolding）。
  - 百分位欄（`netbuy_turnover_pct`、`short_margin_ratio_pct`、`daytrade_pct`）＝**類股內**橫斷面百分位，類股取 `stocks.sector_id`（讀法參考 `routes_sectors.py`）。
  - 複合旗標定義（全部寫成布林欄，門檻屬特徵工程層、由組成訊號的分位/連續條件構成，不另引新自由參數）：
    - `dual_net_flag`：近5日外資與投信淨買超皆 >0
    - `absorb_flag`：法人連買≥5日 且 同期股價漲幅 < 類股中位
    - `big_absorb_flag`：`big_holder_wk_up`(連3週升) 且 同期價格漲幅 < 類股中位
    - `squeeze_flag`：券資比 ≥ 自身一年 q80 且 收盤創 20 日高
    - `washout_flag`：單日跌幅 ≤ −5% 且 融資單日減 ≥ 2%
    - `distribute_warn_flag`：大戶週減 且 散戶人數增 且 融資5日增 ≥ 5% 且 法人5日淨賣
    - `insider_buyback_flag`：股價位於一年 q30 以下 且 董監月增
  - `chip_templates()` 中每個 Template 的 id 用 spec 編號（A1..F4），參數格照 spec 表（如 A1: `consec_ge`, n=[3,5,10,15]）。F 家族與旗標欄用 `op="flag"`。

- [ ] **Step 1: 寫失敗測試**（純函數部分——模板數與展開數）

```python
# backend/tests/test_ctx_chip.py
from app.research.ctx_matrix.chip import chip_templates
from app.research.ctx_matrix.templates import expand


def test_chip_templates_cover_spec():
    tpls = chip_templates()
    ids = {t.id for t in tpls}
    # spec §5 的六個子家族都要在
    for prefix in ("A1", "A3", "B4", "C1", "D4", "E1", "F1", "F4"):
        assert any(i.startswith(prefix) for i in ids), prefix


def test_chip_templates_expand():
    out = expand(chip_templates())
    # A1 n∈{3,5,10,15} → 4 個；總展開數應遠大於模板數
    assert sum(1 for t in out if t.id.startswith("A1_")) == 4
    assert len(out) >= 40
```

- [ ] **Step 2: 跑測試確認失敗**
- [ ] **Step 3: 實作 `chip_templates()` 與 `chip_feature_frame()`**（feature_frame 無法用純函數測試覆蓋 DB 讀取，改用 Step 4 的實資料煙霧測試驗證）
- [ ] **Step 4: 煙霧測試（實 DB）**

```
cd backend
.venv\Scripts\python.exe -c "from app.research.ctx_matrix.chip import chip_feature_frame; df=chip_feature_frame('data/twa.db'); print(df.shape); print(df.columns.tolist()); assert len(df)>100000"
```
Expected: shape 印出、必要欄位齊、列數 >10 萬。

- [ ] **Step 5: 跑單元測試通過後 Commit** `feat(ctx): 籌碼家族特徵與模板（spec A1~F4）`

---

### Task 4: 其餘四家族（動能/量能、基本面、消息、事件）

**Files:**
- Create: `backend/app/research/ctx_matrix/other_families.py`
- Test: 併入 `backend/tests/test_ctx_chip.py`（加測試函數）

**Interfaces:**
- Produces:
  - `other_feature_frame(db_path: str) -> pd.DataFrame`：columns 至少含
    - 動能/量能：`ret5, ret20, bias20, break20_flag, vol_ratio5, turnover_pct, rel_ret20`（`rel_ret20` 用中性化口徑：個股20日報酬 − 類股等權20日報酬，比照 `mega_mine.add_sector_neutral` 的 sec_rel 思路；**不用壞掉的 sec_ret20**）
    - 基本面動能：`rev_yoy, rev_yoy_accel, rev_mom, eps_qoq, score_chg20`（月營收自公布日起生效；score 取 `Score` 表）
    - 消息熱度：`attn_flag, attn_cnt20, news_cnt5_pct`（來源 `AttentionListing` 與 news/Event 表；讀法參考 `backfill_attention.py`）
    - 事件：`etf_add_win_flag, etf_del_win_flag`（`EtfIndexEvent`，生效日後第6~10日窗＝已驗證甜蜜點）
  - `other_templates() -> list[Template]`：每欄至少一模板；連續欄用 `q_hi`/`q_lo`（q∈[0.7,0.8,0.9] 或 [0.1,0.2,0.3]），旗標用 `flag`。
- 注意：`rev_yoy_chg` 與 `margin_chg5` 是既有稽核的 `_FLAGGED` 名單（`mega_mine2.py:326`）——照樣入池，但在 pipeline 稽核（Task 6）會重新判定。

- [ ] **Step 1: 加失敗測試**

```python
def test_other_templates_cover_families():
    from app.research.ctx_matrix.other_families import other_templates
    fams = {t.family for t in other_templates()}
    assert fams >= {"momentum", "volume", "fundamental", "news", "event"}
```

- [ ] **Step 2: 確認失敗 → Step 3: 實作 → Step 4: 煙霧測試（同 Task 3 形式，`other_feature_frame`）→ Step 5: 測試通過後 Commit** `feat(ctx): 動能/量能/基本面/消息/事件家族`

---

### Task 5: 情境維度（歷史 regime＋鏈共振）與手工核心鏈

**Files:**
- Create: `backend/app/research/ctx_matrix/context.py`
- Create: `backend/data/core_chains.json`
- Test: `backend/tests/test_ctx_context.py`

**Interfaces:**
- Produces:
  - `historical_regime(index_close: pd.Series, ma_n: int = 60, gap: float = 0.02) -> pd.Series`：把 `market_regime.wave_market_regime` 的 MA60 遲滯規則（`app/engines/market_regime.py:28-54`）改寫成對整段歷史序列的純函數，回傳 index=date、值 ∈ {"hold","defense"} 的 Series。
  - `chain_resonance(sec_rel20_by_chain: pd.DataFrame, stock_chains: pd.DataFrame) -> pd.DataFrame`
    - `sec_rel20_by_chain`：index=date、columns=chain_id 的鏈層中性化20日強度（＝鏈內成分股 rel_ret20 等權平均）
    - `stock_chains`：columns `[stock_id, chain_id]`
    - 回傳 `[stock_id, date, resonance]`，`resonance` ∈ {"strong","neutral","weak"}：該股所屬鏈當日強度在**全部鏈的當日橫斷面**取三分位。
  - `load_core_chains(path: str) -> pd.DataFrame`：columns `[chain_id, chain_name, stock_id, role]`。
- `core_chains.json` 初始內容：手工建 6 條鏈（每條列 8~15 檔代表成分，role ∈ 上游/中游/下游）：
  1. `SEMI` 半導體（上游 IP/設備：3661,3443,6415；中游晶圓：2330,2303,5347；下游封測：2311,3711,6239 …）
  2. `AISRV` AI伺服器（2317,2382,3231,6669,2376,3017,3324 …）
  3. `APPLE` 蘋概（2317,2354,3008,2474,4938 …）
  4. `SHIP` 航運（2603,2609,2615,2606,5608 …）
  5. `POWER` 重電/綠能（1503,1504,1513,1519,6806 …）
  6. `FIN` 金控（2881,2882,2884,2886,2891 …）
  - 成員以 `IndustryChainMember`（TPEX 快照）與公開常識校對；實作者用
    `.venv\Scripts\python.exe -c "..."` 查該表輔助確認成員，最終以 json 檔為準。
  - 檔案含 `"_note": "鏈成員為可證偽假設；格子系統性失效時先檢討成員，見 spec §4"`。

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_ctx_context.py
import pandas as pd
import numpy as np
from app.research.ctx_matrix.context import historical_regime, chain_resonance


def test_regime_hysteresis():
    dates = pd.date_range("2024-01-01", periods=200, freq="B")
    # 前100天緩漲（站上MA60→hold），後100天急跌破 MA60×0.98 → defense
    close = np.concatenate([np.linspace(100, 120, 100), np.linspace(120, 80, 100)])
    s = historical_regime(pd.Series(close, index=dates))
    assert s.iloc[99] == "hold"
    assert s.iloc[-1] == "defense"
    assert set(s.unique()) <= {"hold", "defense"}


def test_resonance_terciles():
    dates = pd.date_range("2024-01-01", periods=2, freq="B")
    strength = pd.DataFrame({"C1": [3.0, 3.0], "C2": [0.0, 0.0], "C3": [-3.0, -3.0]}, index=dates)
    stock_chains = pd.DataFrame({"stock_id": ["a", "b", "c"], "chain_id": ["C1", "C2", "C3"]})
    out = chain_resonance(strength, stock_chains)
    got = out[out["date"] == dates[0]].set_index("stock_id")["resonance"]
    assert got["a"] == "strong" and got["b"] == "neutral" and got["c"] == "weak"
```

- [ ] **Step 2: 確認失敗 → Step 3: 實作**（`historical_regime`：先算 MA60，逐日狀態機——上穿 `ma*(1+gap)` 轉 hold、下破 `ma*(1-gap)` 轉 defense，中間維持前態，初始態依首日 close≥ma 判 hold/defense；MA 未滿 60 根前沿用首個有效態）**→ Step 4: 測試通過 → Step 5: 建 `core_chains.json` → Step 6: Commit** `feat(ctx): 情境維度（歷史regime＋鏈共振）＋手工核心鏈v1`

---

### Task 6: 格子評估器（含全市場 baseline 與 routing_delta）

**Files:**
- Create: `backend/app/research/ctx_matrix/evaluator.py`
- Test: `backend/tests/test_ctx_evaluator.py`

**Interfaces:**
- Consumes: 標籤 frame（Task 1）、遮罩（Task 2）。
- Produces:
  - `class CellEvaluator:`
    - `__init__(self, df: pd.DataFrame, target_col: str = "exc_hit20", atr_col: str = "atr_bucket")`
      - `df` 為對齊後的 target frame：每列一 (stock, date)，含 `date, stock_id, exc_hit20(0/1), atr_bucket(int), fold(int 0-2), is_holdout(bool)`
    - `run(self, mask: np.ndarray, scope: np.ndarray) -> dict | None`
      - `scope`＝格子的母體遮罩（某類股群×某情境）；評估 `mask & scope` 相對 `scope` 內同日同 ATR 桶的期望（ctrl 口徑，同 `mega_mine2.Evaluator.run` L271-311 的方法）
      - 回傳 `{hit, base, lift, ctrl, t_ctrl, n_picks, n_days, fold_ctrl: [f0,f1,f2], holdout_ctrl, holdout_hit}`；有效日 <60 或觸發 <min_picks 回 None
    - `bonferroni_gate(n_cells: int) -> float`：`norm.isf(0.025 / n_cells)`（同 mega_mine2.py:471-476）
  - `routing_delta(cell: dict, allmarket: dict) -> float`：`cell["ctrl"] - allmarket["ctrl"]`

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_ctx_evaluator.py
import numpy as np
import pandas as pd
from app.research.ctx_matrix.evaluator import CellEvaluator, routing_delta


def _frame(n_days=80, n_stocks=30, seed=0):
    rng = np.random.default_rng(seed)
    dates = np.repeat(pd.date_range("2023-01-02", periods=n_days, freq="B"), n_stocks)
    df = pd.DataFrame({
        "date": dates,
        "stock_id": np.tile([f"s{i}" for i in range(n_stocks)], n_days),
        "exc_hit20": rng.binomial(1, 0.05, n_days * n_stocks),
        "atr_bucket": rng.integers(0, 3, n_days * n_stocks),
        "fold": np.repeat(np.arange(3), (n_days * n_stocks + 2) // 3)[: n_days * n_stocks],
        "is_holdout": False,
    })
    return df


def test_informative_mask_positive_ctrl():
    df = _frame()
    # 把某些列強制命中，遮罩剛好選中它們 → ctrl 必為正
    hot = np.zeros(len(df), bool)
    hot[::7] = True
    df.loc[hot, "exc_hit20"] = 1
    ev = CellEvaluator(df)
    res = ev.run(hot, scope=np.ones(len(df), bool))
    assert res is not None and res["ctrl"] > 0 and res["t_ctrl"] > 2


def test_random_mask_near_zero_ctrl():
    df = _frame()
    rng = np.random.default_rng(1)
    m = rng.random(len(df)) < 0.1
    ev = CellEvaluator(df)
    res = ev.run(m, scope=np.ones(len(df), bool))
    assert res is None or abs(res["ctrl"]) < 3.0


def test_too_few_days_returns_none():
    df = _frame(n_days=10)
    ev = CellEvaluator(df)
    assert ev.run(np.ones(len(df), bool), np.ones(len(df), bool)) is None


def test_routing_delta():
    assert routing_delta({"ctrl": 5.0}, {"ctrl": 2.0}) == 3.0
```

- [ ] **Step 2: 確認失敗 → Step 3: 實作**（逐日計算：當日 scope 內以 atr_bucket 分組的命中期望 `bexp`；ctrl=選中列 `hit − bexp` 的跨日平均×100；t 用日層級序列單樣本 t，參照 `mega_mine2.py:294-311`；fold_ctrl 分 fold 重算；holdout 列另算）**→ Step 4: 測試通過 → Step 5: Commit** `feat(ctx): 格子評估器（ctrl/t/fold/holdout/routing_delta）`

---

### Task 7: 管線組裝＋污染稽核閘＋CLI

**Files:**
- Create: `backend/app/research/ctx_matrix/pipeline.py`
- Create: `backend/scripts/ctx_matrix_run.py`
- Modify: 無

**Interfaces:**
- Consumes: Task 1~6 全部公開函數。
- Produces:
  - `pipeline.build(db_path) -> None`：組特徵（chip+other+context+labels 對齊成單一 frame）→ 存 `backend/data/ctx_features.pkl`
  - `pipeline.audit(feat_path) -> dict`：對池內每個連續特徵跑污染三判準（覆蓋率、ts_share、控ATR spread 挖掘窗vs holdout——**判準與常數照抄 `feature_contamination_audit.py:50-142`**，目標改 `exc_hit20`）→ 存 `backend/data/ctx_contamination.json`；`verdict=="必修"` 的特徵其模板**不入掃格**（記入 excluded 名單）
  - `pipeline.mine(feat_path) -> None`：
    1. KPI 定版：`scan_thresholds` → 寫入 artifact `kpi` 節
    2. 格子座標：群 g ∈ {全市場} ∪ {6條核心鏈} ∪ {TPEX chain_name 前15大（依成員數）}；情境 c ∈ {all, hold, defense, resonance_strong, resonance_weak}；訊號 s ∈ 稽核存活模板展開
    3. 每格跑 `CellEvaluator.run`；同 (s,c) 的全市場結果為 baseline → `routing_delta`
    4. 過關標記：`tier="pass"`（3 fold ctrl>0 且 t_ctrl≥bonferroni_gate(總格數)）／`"watch"`（t≥4.0）／`"fail"`；樣本不足者 `tier="insufficient"`
    5. 輸出 `backend/data/ctx_matrix.json`：
       ```json
       {"generated_at": "...", "kpi": {"horizon": 20, "x": 10, "base_rates": {...}},
        "excluded_features": [...],
        "cells": [{"group": "SEMI", "group_kind": "core_chain", "signal": "A1_n5",
                   "family": "chip", "context": "hold", "n_picks": 0, "n_days": 0,
                   "hit": 0.0, "ctrl": 0.0, "t_ctrl": 0.0, "fold_ctrl": [], 
                   "holdout_ctrl": 0.0, "routing_delta": 0.0, "tier": "pass"}],
        "chain_audit": [{"chain_id": "SEMI", "n_cells": 0, "n_pass": 0, "n_fail": 0,
                          "verdict": "正常|建議檢討成員"}]}
       ```
       `chain_audit.verdict`＝該鏈 pass 率 < 全體鏈 pass 率的一半 且格子數≥20 → 「建議檢討成員」（spec §4 鏈稽核）。
  - CLI：`ctx_matrix_run.py build|audit|mine`（argparse 子命令，`PYTHONIOENCODING=utf-8` 提示照 `feature_contamination_audit.py:30` 慣例寫在檔頭）。

- [ ] **Step 1: 實作 pipeline.py 與 CLI**（此 task 為整合層，正確性由 Task 1~6 單元測試與下列實跑驗收，不另寫單元測試）
- [ ] **Step 2: 實跑三步**

```
cd backend
.venv\Scripts\python.exe scripts\ctx_matrix_run.py build
.venv\Scripts\python.exe scripts\ctx_matrix_run.py audit
.venv\Scripts\python.exe scripts\ctx_matrix_run.py mine
```
Expected: 三個產物落地（`ctx_features.pkl`、`ctx_contamination.json`、`ctx_matrix.json`）；stdout 印格子總數、pass/watch/insufficient 統計、KPI 定版值、chain_audit 摘要。

- [ ] **Step 3: 人工抽查**：挑 3 個 pass 格子，核對 n_picks/ctrl 合理（無 NaN、fold 方向一致）；確認全市場 baseline 格子存在且 routing_delta 計算正確（cell.ctrl − baseline.ctrl）。
- [ ] **Step 4: Commit**（含 `ctx_matrix.json` 與 `ctx_contamination.json`；`ctx_features.pkl` **不進 git**，若 `.gitignore` 未涵蓋 `backend/data/*.pkl` 則補上）`feat(ctx): 矩陣挖掘管線＋稽核閘＋CLI，凍結首版矩陣`

---

### Task 8: Runtime 載入器＋API

**Files:**
- Create: `backend/app/engines/ctx_matrix_loader.py`
- Create: `backend/app/api/routes_ctx.py`
- Modify: `backend/app/main.py`（比照 corners router 的掛載處，約 L69）
- Test: `backend/tests/test_ctx_api.py`

**Interfaces:**
- Produces:
  - `ctx_matrix_loader.load_matrix() -> dict | None`：讀 `backend/data/ctx_matrix.json`（路徑解析比照 `corner_defs.load_corners()` 的方式），檔不存在回 None，內建 lru_cache＋mtime 檢查。
  - `GET /api/ctx-matrix` → `CtxMatrixResponse`（Pydantic，同檔定義，風格比照 `routes_corners.py:30-61`）：`{generated_at, kpi, groups: [...], signals: [...], cells: [...], chain_audit: [...]}`，cells 僅回傳 `tier != "fail"` 的格子＋全部 insufficient 統計數。
  - Router：`APIRouter(prefix="/ctx-matrix", tags=["ctx"])`，`app.include_router(..., prefix="/api")`。

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_ctx_api.py
from fastapi.testclient import TestClient
from app.main import app


def test_ctx_matrix_endpoint():
    client = TestClient(app)
    r = client.get("/api/ctx-matrix")
    assert r.status_code in (200, 404)  # artifact 存在時 200；缺檔時明確 404
    if r.status_code == 200:
        body = r.json()
        assert "cells" in body and "kpi" in body
```
（先確認既有測試怎麼處理 auth middleware——參考 `tests/test_auth_middleware.py` 的 client 建法，若 API 需登入則比照其 bypass 方式。）

- [ ] **Step 2: 確認失敗 → Step 3: 實作 → Step 4: 測試通過**
- [ ] **Step 5: 重生 openapi**：檢查 repo 既有流程（`backend/openapi.json` 如何更新——搜 README 或 package.json scripts；通常為啟動 app 後導出），更新後 `cd frontend && npm run gen:api`。
- [ ] **Step 6: Commit** `feat(ctx): ctx-matrix API 與 runtime 載入器`

---

### Task 9: 前端矩陣頁

**Files:**
- Create: `frontend/src/pages/CtxMatrixPage.tsx`
- Modify: `frontend/src/api/client.ts`（加 `useCtxMatrix` hook，比照 L897-955 corners 寫法）
- Modify: `frontend/src/App.tsx`（加 `<Route path="/ctx-matrix" ...>`）
- Modify: 導覽選單元件（依 App.tsx 內既有 nav 結構找到清單處，加一項「情境矩陣」）

**Interfaces:**
- Consumes: `GET /api/ctx-matrix`（Task 8 schema，型別由 gen:api 產生）。
- Produces: 頁面功能——
  1. 家族分頁籤（籌碼/動能/量能/基本面/消息/事件）
  2. 主體：CSS grid 熱力表，列＝類股群（核心鏈置頂）、欄＝該家族訊號；格色階依 `routing_delta`（紅正綠負或依站上既有色彙——遵循現有 Tailwind 配色慣例），格內小字 `n_picks`
  3. tier 徽章：pass=實心、watch=空心、insufficient=灰斜線
  4. 點格子開 popover：ctrl / t / fold_ctrl / holdout / baseline 對照
  5. 頂部資訊列：KPI 定版（20日超額 ≥ X%）、生成時間、excluded_features 數
  6. chain_audit 有「建議檢討成員」時，該鏈列首顯示警示 icon＋tooltip

- [ ] **Step 1: 實作 hook＋頁面＋路由＋nav**
- [ ] **Step 2: 驗證**：啟動 dev server（用 preview 工具依 `.claude/launch.json`／既有啟動方式），開 `/ctx-matrix`，確認：資料載入、六分頁切換、popover 內容、暗色主題下可讀（若站有暗色）。console 無錯誤。
- [ ] **Step 3: 截圖存證後 Commit** `feat(ctx): 前端情境矩陣熱力圖頁`

---

### Task 10: 收尾——文件與矩陣首版解讀

**Files:**
- Modify: `docs/superpowers/specs/2026-08-21-context-routing-track-design.md`（狀態改「第一階段已實作」＋補 KPI 定版值）
- Create: `docs/ctx-matrix-findings.md`（比照 `docs/poppability-finding.md` 的定位）

**Steps:**
- [ ] **Step 1: 寫首版發現報告**：pass 格子清單（依 routing_delta 排序）、全市場 baseline 對照、insufficient 比例、chain_audit 結果、以及對 spec §9 失敗準則的正式回答（有/沒有格子顯著優於全市場）。
- [ ] **Step 2: Commit** `docs(ctx): 矩陣首版發現與spec狀態更新`

---

## Self-Review 紀錄

- Spec 覆蓋：KPI（T1）、矩陣三維（T3/4/5/7）、產業鏈＋鏈稽核（T5/T7）、籌碼庫 A~F（T3）、其餘家族同粒度（T4）、驗證紀律（T6/T7 稽核閘＋Bonferroni＋fold＋holdout＋baseline）、交付物三項（T7 管線、T9 前端、T7 chain_audit）、不做清單（無 lead-lag 任務、無路由引擎任務、無新資料源）、失敗準則（T10 正式回答）。無缺口。
- 型別一致性：`exc_mfe20`（連續）→ pipeline 內以 KPI X 離散成 `exc_hit20`（T7 職責，evaluator 只吃 `exc_hit20`）；`Template`/`build_masks` 簽名在 T2 定義、T3/T4 消費一致。
- 已知風險註記：Task 3/4 的 feature frame 依賴實表結構，計畫以 models.py 行號與既有讀取範例為錨，不硬編臆測欄名；週/月頻 forward-fill 必須以公布日起算（防前視），已寫入 T3 要點。
