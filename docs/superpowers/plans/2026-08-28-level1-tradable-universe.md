# Level 1 Tradable Universe Redefinition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Level 1 的研究 Universe 與生產 Universe 統一為單一「可交易推薦 Universe」，並修掉會產生假戰績的訓練窗 leakage。

**Architecture:** `U_t = Structural ∩ TradingEligibility`（存在性 ∧ ADV20 ≥ 5,000 萬 ∧ 非處置），由 `universe.build_tradable_universe(con)` 單一入口產出，研究（`level1_run`）與生產（`level1_predict`）共用；Target 在同一 `U_t` 內重排；訓練窗由 `walkforward.train_slice_for_date()` 單一函式決定，兩端共用。

**Tech Stack:** Python 3.11、pandas、LightGBM、SQLAlchemy、FastAPI、pytest。

設計來源：`docs/superpowers/specs/2026-08-28-level1-tradable-universe-design.md`（commit d7c942c、7d8a663、7ed6062）。

## Global Constraints

- **ADV20 門檻 = `5e7`（5,000 萬新台幣）。FRS §3 凍結常數。** 不得參數化、不得因 OOS 績效變差而回調。
- ADV20 視窗 = 20 日、`min_periods=10`，只回看。
- **只排除處置（`kind='punish'`），不排除注意（`kind='notice'`）。**
- 研究期起點 = `2020-02-01`（ADV20 暖身期後）。
- `MODEL_VERSION = "l1_lgbm_v2"`、`FEATURE_VERSION = "v2_feat20_u2"`。
- **所有 `LGBMRegressor` 統一 `n_jobs=1`。** 這是 reproducibility control，不是 predictive-performance control。
- **OOS 與 Production 必須呼叫同一個 `train_slice` / `build_tradable_universe`，不得各自複製邏輯。**
- Windows 環境坑：任何 import `lightgbm` 的模組，必須先 `import sklearn.linear_model`（OpenMP DLL 載入順序），否則 access violation。
- 測試一律 `cwd=backend`，指令 `.venv/Scripts/python.exe -m pytest`。
- 既有測試全綠是每個 Task 的收工條件。

---

## File Structure

| 檔案 | 責任 | 動作 |
|---|---|---|
| `backend/app/research/level1/walkforward.py` | 訓練窗與 embargo 規則的唯一定義 | 修改（+1 函式） |
| `backend/app/research/level1/universe.py` | `U_t` 的唯一定義與唯一組裝入口 | 修改（+6 函式、+3 常數） |
| `backend/app/research/level1/evaluation.py` | 評估指標 | 修改（+1 函式） |
| `backend/scripts/level1_targets.py` | 研究快取產生器 | 修改 |
| `backend/scripts/level1_run.py` | OOS 評估 | 修改 |
| `backend/scripts/level1_predict.py` | 每日生產 pipeline | 修改 |
| `backend/app/api/routes_level1.py` | 展示層端點 | 修改 |
| `backend/tests/test_level1_eval_walkforward.py` | 訓練窗測試 | 修改 |
| `backend/tests/test_level1_universe_targets.py` | Universe 測試 | 修改 |
| `backend/tests/test_level1_pipeline_parity.py` | A/B 一致性護欄 | 建立 |
| `backend/tests/test_level1_board_api.py` | 端點版本過濾測試 | 建立 |

---

## Task 1: 訓練窗修正（P0 / Blocking）

修掉確定性的 temporal leakage：`level1_predict` 目前以 `close.index`（DB 全歷史）建訓練集，訓練終點是「DB 最新日 − N」而非「pred_date − N」。跑歷史日期會用到 pred_date 之後的資料。

**Files:**
- Modify: `backend/app/research/level1/walkforward.py`（在 `train_slice` 之後新增）
- Modify: `backend/scripts/level1_predict.py`（`predict()` 內的訓練集組裝）
- Test: `backend/tests/test_level1_eval_walkforward.py`（附加於檔尾）

**Interfaces:**
- Consumes: 現有 `walkforward.train_slice(dates, test_start, embargo)`
- Produces: `walkforward.train_slice_for_date(dates: pd.Index, pred_date: str, embargo: int) -> pd.Index`

- [ ] **Step 1: 寫失敗測試**

附加到 `backend/tests/test_level1_eval_walkforward.py` 檔尾：

```python
# ── Production 訓練窗（設計 §6：P0 leakage 防線）──

def test_train_slice_for_date_excludes_pred_date_and_future():
    dates = pd.Index([f"2025-01-{d:02d}" for d in range(1, 21)])
    tr = wf.train_slice_for_date(dates, "2025-01-15", embargo=5)
    assert tr[-1] == "2025-01-09"          # index 14 − embargo 5 → dates[:9]
    assert "2025-01-15" not in tr
    assert not any(d > "2025-01-09" for d in tr)


def test_train_slice_for_date_matches_walk_forward_rule():
    """Production 與 OOS 必須是同一規則的同一函式，不是兩份等價邏輯。"""
    dates = pd.Index([f"2025-02-{d:02d}" for d in range(1, 29)])
    i = 20
    assert list(wf.train_slice_for_date(dates, dates[i], embargo=10)) == \
           list(wf.train_slice(dates, i, embargo=10))


def test_train_slice_for_date_rejects_unknown_date():
    dates = pd.Index(["2025-01-01", "2025-01-02"])
    with pytest.raises(KeyError):
        wf.train_slice_for_date(dates, "2025-01-03", embargo=1)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/Scripts/python.exe -m pytest tests/test_level1_eval_walkforward.py -k train_slice_for_date -v`
Expected: FAIL，`AttributeError: module 'app.research.level1.walkforward' has no attribute 'train_slice_for_date'`

- [ ] **Step 3: 實作**

在 `backend/app/research/level1/walkforward.py` 的 `train_slice` 函式之後插入：

```python
def train_slice_for_date(dates: pd.Index, pred_date: str, embargo: int) -> pd.Index:
    """預測日的訓練窗——Production 端入口，與 OOS 的 test_blocks 同一 embargo 規則。

    Production 不得以「最近 N 日 label 為 NaN 會被 dropna 掉」當作 embargo：那只在
    pred_date == 最新交易日時成立，跑任何歷史日期都會用到 pred_date 之後的資料
    （確定性 temporal leakage，見設計 §2.1）。
    """
    return train_slice(dates, int(dates.get_loc(pred_date)), embargo)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/Scripts/python.exe -m pytest tests/test_level1_eval_walkforward.py -v`
Expected: PASS（含既有 embargo 測試）

- [ ] **Step 5: 讓 Production 呼叫它**

在 `backend/scripts/level1_predict.py` 的 import 區塊（`from app.research.level1 import targets as tg` 之後）加入：

```python
from app.research.level1 import walkforward as wf  # noqa: E402
```

把 `predict()` 中的這三行：

```python
        pct = tg.cross_sectional_pct(tg.forward_returns(close, n), mask)
        x_tr, y_tr, _ = ft.assemble_dataset(ranked, pct, close.index)
        model = LGBMRegressor(n_estimators=100, random_state=42, verbose=-1)
```

改為：

```python
        pct = tg.cross_sectional_pct(tg.forward_returns(close, n), mask)
        # 訓練窗綁 pred_date 而非 DB 最新日——與 OOS 共用同一個 train_slice（設計 §6）
        train_dates = wf.train_slice_for_date(close.index, pred_date, embargo=n)
        x_tr, y_tr, _ = ft.assemble_dataset(ranked, pct, train_dates)
        # n_jobs=1 是 reproducibility control，不是 predictive-performance control
        model = LGBMRegressor(n_estimators=100, random_state=42, n_jobs=1, verbose=-1)
```

同時把 `predict()` 的 log 行改為印出訓練窗終點，讓每次執行都留下可稽核痕跡。將：

```python
        _log(f"{n}D：訓練 {len(y_tr):,} 列、寫入 {len(rows)} 檔 "
             f"Top5={top5}（{time.time()-t0:.0f}s）")
```

改為：

```python
        _log(f"{n}D：訓練 {len(y_tr):,} 列（迄 {train_dates[-1]}）、寫入 {len(rows)} 檔 "
             f"Top5={top5}（{time.time()-t0:.0f}s）")
```

- [ ] **Step 6: 跑全套測試**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 全綠

- [ ] **Step 7: Commit**

```bash
git add backend/app/research/level1/walkforward.py backend/scripts/level1_predict.py backend/tests/test_level1_eval_walkforward.py
git commit -m "fix(level1): 訓練窗綁 pred_date 而非 DB 最新日——修掉歷史回填的確定性 leakage"
```

---

## Task 2: Universe 擴充為 Trading Eligibility

把流動性與處置條件推進 `U_t` 定義，並建立單一組裝入口 `build_tradable_universe`，杜絕研究／生產各自組裝。

**Files:**
- Modify: `backend/app/research/level1/universe.py`
- Test: `backend/tests/test_level1_universe_targets.py`（附加於檔尾）

**Interfaces:**
- Consumes: 現有 `eligible_ids`、`load_stocks`、`load_close_prices`、`close_matrix`、`universe_mask`
- Produces:
  - `universe.ADV20_FLOOR: float = 5e7`
  - `universe.ADV_WINDOW: int = 20`、`universe.ADV_MIN_PERIODS: int = 10`
  - `universe.adv20(turnover: pd.DataFrame) -> pd.DataFrame`
  - `universe.punish_mask(windows: pd.DataFrame, index: pd.Index, columns: pd.Index) -> pd.DataFrame`
  - `universe.tradable_mask(close: pd.DataFrame, turnover: pd.DataFrame, punish: pd.DataFrame, floor: float = ADV20_FLOOR) -> pd.DataFrame`
  - `universe.load_turnover(con, eligible: set[str], index: pd.Index, columns: pd.Index) -> pd.DataFrame`
  - `universe.load_punish_windows(con) -> pd.DataFrame`
  - `universe.build_tradable_universe(con) -> tuple[pd.DataFrame, pd.DataFrame]`（回傳 `(close, mask)`）

- [ ] **Step 1: 寫失敗測試（純函式部分）**

在 `backend/tests/test_level1_universe_targets.py` 頂端的 import 區塊加入 `import sqlite3`（Step 5 的測試會用到），並附加到檔尾：

```python
# ── Trading Eligibility（FRS §3 v1.1）──

def _turnover_fixture():
    """AAAA 全程高量、BBBB 全程低量、CCCC 前 10 日低量後 5 日暴量。"""
    dates = [f"2025-01-{d:02d}" for d in range(1, 16)]
    return pd.DataFrame(index=dates, data={
        "AAAA": [2e8] * 15,
        "BBBB": [1e6] * 15,
        "CCCC": [1e6] * 10 + [1e9] * 5,
    })


def _flat_close(t):
    return pd.DataFrame(10.0, index=t.index, columns=t.columns)


def _no_punish(t):
    return pd.DataFrame(False, index=t.index, columns=t.columns)


def test_adv20_warmup_is_nan():
    a = uv.adv20(_turnover_fixture())
    assert a.iloc[:9].isna().all().all()        # 不足 10 日 → NaN
    assert not a.iloc[9:].isna().any().any()


def test_tradable_mask_applies_liquidity_floor():
    t = _turnover_fixture()
    m = uv.tradable_mask(_flat_close(t), t, _no_punish(t))
    assert m.loc["2025-01-15", "AAAA"]          # 2 億 ≥ 5,000 萬
    assert not m.loc["2025-01-15", "BBBB"]      # 100 萬 < 5,000 萬
    assert not m.iloc[:9].any().any()           # 暖身期 NaN → 一律不在 U_t


def test_tradable_mask_liquidity_is_backward_looking_only():
    """CCCC 於 d11 才放量；d10 的 ADV20 不得被之後的成交值抬高（截斷未來不改變過去）。"""
    t = _turnover_fixture()
    m = uv.tradable_mask(_flat_close(t), t, _no_punish(t))
    assert not m.loc["2025-01-10", "CCCC"]
    truncated = uv.tradable_mask(_flat_close(t).iloc[:10], t.iloc[:10],
                                 _no_punish(t).iloc[:10])
    assert m.iloc[:10].equals(truncated)


def test_punish_mask_window_is_inclusive():
    dates = pd.Index([f"2025-01-{d:02d}" for d in range(1, 8)])
    w = pd.DataFrame([{"stock_id": "AAAA", "begin_date": "2025-01-03",
                       "end_date": "2025-01-05"}])
    pm = uv.punish_mask(w, dates, pd.Index(["AAAA", "BBBB"]))
    assert not pm.loc["2025-01-02", "AAAA"]
    assert pm.loc["2025-01-03", "AAAA"]         # 起日含
    assert pm.loc["2025-01-05", "AAAA"]         # 迄日含
    assert not pm.loc["2025-01-06", "AAAA"]
    assert not pm["BBBB"].any()


def test_punish_mask_ignores_unknown_stock():
    w = pd.DataFrame([{"stock_id": "ZZZZ", "begin_date": "2025-01-01",
                       "end_date": "2025-01-01"}])
    pm = uv.punish_mask(w, pd.Index(["2025-01-01"]), pd.Index(["AAAA"]))
    assert not pm.any().any()


def test_tradable_mask_excludes_punished_days():
    t = _turnover_fixture()
    w = pd.DataFrame([{"stock_id": "AAAA", "begin_date": "2025-01-12",
                       "end_date": "2025-01-13"}])
    m = uv.tradable_mask(_flat_close(t), t, uv.punish_mask(w, t.index, t.columns))
    assert m.loc["2025-01-11", "AAAA"]
    assert not m.loc["2025-01-12", "AAAA"]
    assert not m.loc["2025-01-13", "AAAA"]
    assert m.loc["2025-01-14", "AAAA"]          # 處置結束後回歸


def test_adv20_floor_is_frozen_constant():
    """5,000 萬是 FRS §3 凍結常數（設計 §4.1）。改動此值等同以 Universe 做 Target Mining。"""
    assert uv.ADV20_FLOOR == 5e7
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/Scripts/python.exe -m pytest tests/test_level1_universe_targets.py -k "tradable or adv20 or punish" -v`
Expected: FAIL，`AttributeError: module 'app.research.level1.universe' has no attribute 'adv20'`

- [ ] **Step 3: 實作純函式**

在 `backend/app/research/level1/universe.py` 的 `universe_mask` 之後附加：

```python
# ── Trading Eligibility（FRS §3 v1.1）──
#
# 判準是「排除的理由」而非排除的後果：流動性與處置回答的是「這檔股票能不能買」，
# 屬 Universe 天職；動能／估值那類回答「會不會漲」的條件才是被禁止的 Alpha 條件。

ADV20_FLOOR = 5e7        # 20 日均成交值下限（新台幣）。FRS 凍結常數，不得因績效回調。
ADV_WINDOW = 20
ADV_MIN_PERIODS = 10


def adv20(turnover: pd.DataFrame) -> pd.DataFrame:
    """20 日均成交值矩陣。只回看；不足 ADV_MIN_PERIODS 日的暖身期為 NaN。"""
    return turnover.rolling(ADV_WINDOW, min_periods=ADV_MIN_PERIODS).mean()


def punish_mask(windows: pd.DataFrame, index: pd.Index,
                columns: pd.Index) -> pd.DataFrame:
    """處置期間布林矩陣：T ∈ [begin_date, end_date]（含兩端）為 True。

    windows 欄位需含 stock_id / begin_date / end_date（YYYY-MM-DD 字串）。
    只處理處置（punish）——注意股（notice）仍為正常競價撮合，不排除。
    """
    out = pd.DataFrame(False, index=index, columns=columns)
    for row in windows.itertuples():
        if row.stock_id not in out.columns:
            continue
        sel = (index >= str(row.begin_date)) & (index <= str(row.end_date))
        if sel.any():
            out.loc[index[sel], row.stock_id] = True
    return out


def tradable_mask(close: pd.DataFrame, turnover: pd.DataFrame,
                  punish: pd.DataFrame, floor: float = ADV20_FLOOR) -> pd.DataFrame:
    """U_t 布林矩陣＝存在性 ∧ 流動性 ∧ 非處置。三個矩陣需同形狀。"""
    return universe_mask(close) & (adv20(turnover) >= floor) & ~punish
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/Scripts/python.exe -m pytest tests/test_level1_universe_targets.py -v`
Expected: PASS

- [ ] **Step 5: 寫組裝入口的失敗測試**

附加到 `backend/tests/test_level1_universe_targets.py` 檔尾：

```python
# ── 單一組裝入口（設計 §6：杜絕研究／生產各自組裝）──

def _memory_db():
    """最小 DB：台積電（高量、1/12–1/13 處置）、殼股（低量）、ETF（應被靜態排除）。"""
    con = sqlite3.connect(":memory:")
    con.executescript(
        "CREATE TABLE stocks (id TEXT, name TEXT, is_etf INT, market TEXT,"
        " industry_category TEXT, listed_date TEXT);"
        "CREATE TABLE daily_prices (stock_id TEXT, date TEXT, close REAL, turnover REAL);"
        "CREATE TABLE attention_listings (stock_id TEXT, date TEXT, kind TEXT,"
        " begin_date TEXT, end_date TEXT);"
    )
    con.executemany("INSERT INTO stocks VALUES (?,?,?,?,?,?)", [
        ("2330", "台積電", 0, "上市", "半導體業", "1994-09-05"),
        ("8444", "殼股", 0, "上市", "其他業", "2010-01-01"),
        ("0050", "ETF", 1, "上市", "所有證券", "2003-06-30"),
    ])
    rows = []
    for d in range(1, 16):
        day = f"2025-01-{d:02d}"
        rows += [("2330", day, 1000.0, 4e9), ("8444", day, 5.0, 1e5),
                 ("0050", day, 150.0, 5e9)]
    con.executemany("INSERT INTO daily_prices VALUES (?,?,?,?)", rows)
    con.executemany("INSERT INTO attention_listings VALUES (?,?,?,?,?)", [
        ("2330", "2025-01-12", "punish", "2025-01-12", "2025-01-13"),
        ("8444", "2025-01-05", "notice", None, None),   # 注意股不得被排除
    ])
    return con


def test_build_tradable_universe_end_to_end():
    con = _memory_db()
    close, mask = uv.build_tradable_universe(con)
    con.close()
    assert "0050" not in close.columns              # ETF 靜態排除
    assert mask.loc["2025-01-11", "2330"]           # 高量普通股在 U_t
    assert not mask.loc["2025-01-11", "8444"]       # 10 萬元/日 < 5,000 萬
    assert not mask.loc["2025-01-12", "2330"]       # 處置期間排除
    assert not mask.loc["2025-01-13", "2330"]
    assert mask.loc["2025-01-14", "2330"]           # 處置結束後回歸
    assert not mask.iloc[:9].any().any()            # ADV20 暖身期


def test_build_tradable_universe_keeps_notice_stocks():
    """注意股（notice）不是不可交易——只有處置（punish）才排除。"""
    con = sqlite3.connect(":memory:")
    con.executescript(
        "CREATE TABLE stocks (id TEXT, name TEXT, is_etf INT, market TEXT,"
        " industry_category TEXT, listed_date TEXT);"
        "CREATE TABLE daily_prices (stock_id TEXT, date TEXT, close REAL, turnover REAL);"
        "CREATE TABLE attention_listings (stock_id TEXT, date TEXT, kind TEXT,"
        " begin_date TEXT, end_date TEXT);"
    )
    con.execute("INSERT INTO stocks VALUES "
                "('2330','台積電',0,'上市','半導體業','1994-09-05')")
    con.executemany("INSERT INTO daily_prices VALUES (?,?,?,?)", [
        ("2330", f"2025-01-{d:02d}", 1000.0, 4e9) for d in range(1, 16)])
    con.execute("INSERT INTO attention_listings VALUES "
                "('2330','2025-01-12','notice',NULL,NULL)")
    _, mask = uv.build_tradable_universe(con)
    con.close()
    assert mask.loc["2025-01-12", "2330"]
```

- [ ] **Step 6: 跑測試確認失敗**

Run: `.venv/Scripts/python.exe -m pytest tests/test_level1_universe_targets.py -k build_tradable -v`
Expected: FAIL，`AttributeError: module 'app.research.level1.universe' has no attribute 'build_tradable_universe'`

- [ ] **Step 7: 實作 loaders 與組裝入口**

在 `backend/app/research/level1/universe.py` 檔尾附加：

```python
def load_turnover(con, eligible: set[str], index: pd.Index,
                  columns: pd.Index) -> pd.DataFrame:
    """合格股票的成交值矩陣，對齊 close 矩陣形狀（缺格為 NaN）。"""
    df = pd.read_sql_query(
        "SELECT stock_id, date, turnover FROM daily_prices "
        "WHERE turnover IS NOT NULL", con)
    return (df[df["stock_id"].isin(eligible)]
            .pivot_table(index="date", columns="stock_id", values="turnover",
                         aggfunc="last")
            .reindex(index=index, columns=columns))


def load_punish_windows(con) -> pd.DataFrame:
    """處置股區間長表（stock_id, begin_date, end_date）。notice 不在此列。"""
    return pd.read_sql_query(
        "SELECT stock_id, begin_date, end_date FROM attention_listings "
        "WHERE kind = 'punish' AND begin_date IS NOT NULL "
        "AND end_date IS NOT NULL", con)


def build_tradable_universe(con) -> tuple[pd.DataFrame, pd.DataFrame]:
    """DB → (close 矩陣, U_t 布林矩陣)。**研究與 Production 的唯一入口。**

    設計 §6：本次改版的三個裂縫皆源於兩條路徑各自組裝。任何新的呼叫端都必須走這裡，
    不得自行拼裝 eligible_ids / close_matrix / tradable_mask。
    """
    stocks = load_stocks(con)
    elig = eligible_ids(stocks)
    close = close_matrix(load_close_prices(con, elig))
    turnover = load_turnover(con, elig, close.index, close.columns)
    punish = punish_mask(load_punish_windows(con), close.index, close.columns)
    return close, tradable_mask(close, turnover, punish)
```

- [ ] **Step 8: 跑全套測試**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 全綠

- [ ] **Step 9: Commit**

```bash
git add backend/app/research/level1/universe.py backend/tests/test_level1_universe_targets.py
git commit -m "feat(level1): U_t 擴充為 Trading Eligibility——ADV20 5,000 萬 + 排除處置"
```

---

## Task 3: `level1_targets.py` 改用新 Universe

**Files:**
- Modify: `backend/scripts/level1_targets.py`

**Interfaces:**
- Consumes: `universe.build_tradable_universe(con)`、`universe.ADV20_FLOOR`
- Produces: `data/level1_targets.pkl`，其 `meta` 新增 `db_max_date`、`adv20_floor`、`research_start` 三個鍵（Task 4 的漂移檢查會讀 `db_max_date`）

- [ ] **Step 1: 加常數**

在 `backend/scripts/level1_targets.py` 的 `_OUT` 定義之後加入：

```python
RESEARCH_START = "2020-02-01"   # ADV20 暖身期之後（設計 §4.2）
```

- [ ] **Step 2: 改寫 `main()` 的 Universe 組裝段**

把 `main()` 開頭這一段：

```python
    con = sqlite3.connect(_DB)
    stocks = uv.load_stocks(con)
    elig = uv.eligible_ids(stocks)
    _log(f"靜態合格普通股 {len(elig)} 檔（主檔 {len(stocks)}）")

    prices = uv.load_close_prices(con, elig)
    con.close()
    close = uv.close_matrix(prices)
    mask = uv.universe_mask(close)
```

改為：

```python
    con = sqlite3.connect(_DB)
    db_max_date = con.execute("SELECT max(date) FROM daily_prices").fetchone()[0]
    close, mask = uv.build_tradable_universe(con)   # 唯一入口（設計 §6）
    con.close()

    # ADV20 暖身期（rolling 20 / min_periods 10）不足，起點後推（設計 §4.2）
    keep = close.index >= RESEARCH_START
    close, mask = close.loc[keep], mask.loc[keep]
    _log(f"U_t 已含流動性底線 ADV20 ≥ {uv.ADV20_FLOOR:,.0f} 與處置排除")
```

- [ ] **Step 3: 補 meta 三個鍵與限制條目**

把 `payload["meta"]` 中的 `"spec"` 一行改寫，並在其後補三個鍵：

```python
            "spec": "FRS v1.1 §5: Y = Percentile(R(t,N) | U_t^Tradable)",
            "db_max_date": db_max_date,
            "adv20_floor": uv.ADV20_FLOOR,
            "research_start": RESEARCH_START,
```

在 `"limitations"` 清單加入一條：

```python
                "U_t 已含流動性底線（ADV20 ≥ 5,000 萬）與處置排除，非全市場普通股",
```

- [ ] **Step 4: 重跑產生新快取**

Run: `.venv/Scripts/python.exe -m scripts.level1_targets`
Expected: log 顯示 `U_t 規模：min / median / max` 落在 368 / 564 / 810 附近（設計 §4.4 的實測值）；`pct 均值` 仍約 0.5

- [ ] **Step 5: 驗證產物**

Run:
```bash
.venv/Scripts/python.exe -c "import pickle; m=pickle.load(open('data/level1_targets.pkl','rb'))['meta']; print(m['db_max_date'], m['adv20_floor'], m['research_start'], m['universe_size'])"
```
Expected: 印出 DB 最新日、`50000000.0`、`2020-02-01`，且 `universe_size` 的 median 在 500–650 之間

- [ ] **Step 6: Commit**

```bash
git add backend/scripts/level1_targets.py
git commit -m "feat(level1): targets 改用可交易 U_t，meta 記錄 db_max_date 與門檻"
```

---

## Task 4: `level1_run.py` 修正三處

**Files:**
- Modify: `backend/scripts/level1_run.py`

**Interfaces:**
- Consumes: `data/level1_targets.pkl` 的 `meta["db_max_date"]`（Task 3 產生）
- Produces: `scripts.level1_run._assert_snapshot_fresh(meta: dict) -> None`；`data/level1_results.json`

- [ ] **Step 1: 先備份舊結果**

Run: `cp data/level1_results.json data/level1_results_u1.json`
Expected: 檔案存在。舊結果留檔對照（設計 §8），不得覆蓋後才想起來。

- [ ] **Step 2: 加入快照漂移 fail-fast**

在 `backend/scripts/level1_run.py` 的 `_eval_all` 函式之後、`main()` 之前插入：

```python
def _assert_snapshot_fresh(meta: dict) -> None:
    """研究快取與 DB 不同步時 fail-fast——不得靜默沿用舊快照（設計 §9）。"""
    con = sqlite3.connect(_DATA / "twa.db")
    db_max = con.execute("SELECT max(date) FROM daily_prices").fetchone()[0]
    con.close()
    if meta.get("db_max_date") != db_max:
        raise SystemExit(
            f"level1_targets.pkl 建於 db_max_date={meta.get('db_max_date')}，"
            f"DB 現況為 {db_max}。請先重跑 scripts.level1_targets。")
```

在 `main()` 中，把：

```python
    payload = pickle.load(open(_DATA / "level1_targets.pkl", "rb"))  # 自產快取
    close, mask, targets = payload["close"], payload["universe"], payload["targets"]
```

改為：

```python
    payload = pickle.load(open(_DATA / "level1_targets.pkl", "rb"))  # 自產快取
    _assert_snapshot_fresh(payload["meta"])
    close, mask, targets = payload["close"], payload["universe"], payload["targets"]
```

- [ ] **Step 3: 修覆蓋率 log 的分母**

`v.where(mask).notna().stack().mean()` 的分母是整個矩陣而非 U_t 格數，長期低報覆蓋率。把：

```python
    _log(f"基本面特徵覆蓋率：" + ", ".join(
        f"{k} {float(v.where(mask).notna().stack().mean()):.0%}"
        for k, v in fund_feats.items()))
```

改為：

```python
    _n_u = int(mask.to_numpy().sum())
    _log("基本面特徵覆蓋率：" + ", ".join(
        f"{k} {float((v.notna() & mask).to_numpy().sum()) / _n_u:.0%}"
        for k, v in fund_feats.items()))
```

- [ ] **Step 4: 釘 `n_jobs=1`**

把 `models` 字典中的 lgbm 一行：

```python
        "lgbm": (ranked_v2, lambda: LGBMRegressor(
            n_estimators=100, random_state=42, n_jobs=-1, verbose=-1)),
```

改為：

```python
        # n_jobs=1 是 reproducibility control，不是 predictive-performance control：
        # 多執行緒下直方圖累加順序不保證固定，score 無法逐位元重現（設計 §6）
        "lgbm": (ranked_v2, lambda: LGBMRegressor(
            n_estimators=100, random_state=42, n_jobs=1, verbose=-1)),
```

- [ ] **Step 5: 驗證 fail-fast 真的會擋**

Run:
```bash
.venv/Scripts/python.exe -c "
import pickle, sys
sys.path.insert(0, '.')
from scripts import level1_run
m = dict(pickle.load(open('data/level1_targets.pkl','rb'))['meta'])
m['db_max_date'] = '1999-01-01'
try:
    level1_run._assert_snapshot_fresh(m)
    print('FAIL: 未擋下')
except SystemExit as e:
    print('OK:', e)
"
```
Expected: 印出 `OK: level1_targets.pkl 建於 db_max_date=1999-01-01，DB 現況為 ...`

- [ ] **Step 6: 驗證覆蓋率分母修正的必要性**

Run:
```bash
.venv/Scripts/python.exe -c "
import pickle
mask = pickle.load(open('data/level1_targets.pkl','rb'))['universe']
n_u, n_all = int(mask.to_numpy().sum()), mask.size
print('U_t 格數 =', n_u, '整個矩陣格數 =', n_all, '比值 =', round(n_u / n_all, 3))
"
```
Expected: 比值約 0.30。舊分母用矩陣總格數，故覆蓋率被壓低約 3 倍——這說明修正是必要的。

- [ ] **Step 7: Commit**

```bash
git add backend/scripts/level1_run.py backend/data/level1_results_u1.json
git commit -m "fix(level1): run 加快照漂移 fail-fast、修覆蓋率分母、釘 n_jobs=1"
```

---

## Task 4b: 評估輸出 `evaluation_n`

設計 §7 要求 `universe_size`（預測母體）與 `evaluation_n`（評估母體）兩個數字都必須輸出、不得混用。ledger 的 `universe_size` 已由 `ledger.rank_scores` 寫入，但評估端目前只吐 `n_days`（天數），沒有每日的評估母體檔數——「為什麼 564 檔 Universe，n 只有 550？」這個問題現在無法從 `results.json` 回答。

**Files:**
- Modify: `backend/app/research/level1/evaluation.py`
- Test: `backend/tests/test_level1_eval_walkforward.py`（附加於檔尾）

**Interfaces:**
- Consumes: 現有 `evaluation.evaluate(score, fwd, n_q)`、`evaluation.ic_summary`
- Produces: `evaluation.daily_evaluation_n(score: pd.DataFrame, fwd: pd.DataFrame) -> pd.Series`；`evaluate()` 回傳值新增 `evaluation_n_mean: float` 與 `evaluation_n_min: int` 兩鍵（Task 5 產生的 `results.json` 會帶上它們）

- [ ] **Step 1: 寫失敗測試**

附加到 `backend/tests/test_level1_eval_walkforward.py` 檔尾：

```python
# ── 評估母體規模（設計 §7：universe_size 與 evaluation_n 不得混用）──

def test_daily_evaluation_n_counts_only_scored_and_realised():
    dates = pd.Index(["2025-01-01", "2025-01-02"])
    cols = pd.Index(["A", "B", "C"])
    score = pd.DataFrame([[0.1, 0.2, 0.3], [0.1, 0.2, np.nan]],
                         index=dates, columns=cols)
    fwd = pd.DataFrame([[0.01, np.nan, 0.03], [0.01, 0.02, 0.03]],
                       index=dates, columns=cols)
    assert ev.daily_evaluation_n(score, fwd).tolist() == [2, 2]
    # d1：B 無 fwd（T+N 已下市）；d2：C 無 score


def test_evaluate_reports_evaluation_n():
    _, _, fwd = _mats(n_days=40, n_stocks=50)
    score = fwd.copy()
    fwd.iloc[:, 0] = np.nan          # 一檔全期無實現報酬 → 評估母體 49
    out = ev.evaluate(score, fwd)
    assert out["evaluation_n_mean"] == pytest.approx(49.0)
    assert out["evaluation_n_min"] == 49
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/Scripts/python.exe -m pytest tests/test_level1_eval_walkforward.py -k evaluation_n -v`
Expected: FAIL，`AttributeError: module 'app.research.level1.evaluation' has no attribute 'daily_evaluation_n'`

- [ ] **Step 3: 實作**

在 `backend/app/research/level1/evaluation.py` 的 `ic_summary` 之後插入：

```python
def daily_evaluation_n(score: pd.DataFrame, fwd: pd.DataFrame) -> pd.Series:
    """每日評估母體檔數 = |{i ∈ U_t : score 與 R(i,t,N) 皆存在}|（設計 §7）。

    與 ledger 的 universe_size（= |U_t|，預測母體）不是同一個數字：T 日在 U_t、
    但 T+N 已下市或長停者，能被預測卻無法被評估。兩者必須分別輸出，不得混用。
    """
    return (score.notna() & fwd.notna()).sum(axis=1)
```

把 `evaluate()` 改為：

```python
def evaluate(score: pd.DataFrame, fwd: pd.DataFrame, n_q: int = 10) -> dict:
    """單一 (score, horizon) 的完整評估包。"""
    ic = daily_rank_ic(score, fwd)
    out = ic_summary(ic)
    n_eval = daily_evaluation_n(score, fwd)
    out["evaluation_n_mean"] = round(float(n_eval.mean()), 1)
    out["evaluation_n_min"] = int(n_eval.min())
    out.update(quantile_summary(score, fwd, n_q))
    out["topk"] = topk_summary(score, fwd)
    return out
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/Scripts/python.exe -m pytest tests/test_level1_eval_walkforward.py -v`
Expected: PASS

- [ ] **Step 5: 跑全套測試**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 全綠

- [ ] **Step 6: Commit**

```bash
git add backend/app/research/level1/evaluation.py backend/tests/test_level1_eval_walkforward.py
git commit -m "feat(level1): 評估輸出 evaluation_n——預測母體與評估母體不再混用同一個數字"
```

---

## Task 5: 【執行閘門】重跑評估並檢視停損點

這是決策關卡，不是程式碼變更。**在本 Task 通過之前，不得動 `level1_predict.py`。**

**Files:** 無程式碼變更（產生 `data/level1_results.json`、更新設計文件）

**Interfaces:**
- Consumes: Task 3 的新 `level1_targets.pkl`、Task 4 的 `level1_run.py`
- Produces: `data/level1_results.json`（新 U_t 上的模型階梯）；設計文件新增 §12.3

- [ ] **Step 1: 重跑 walk-forward 評估**

Run: `.venv/Scripts/python.exe -m scripts.level1_run`
Expected: 完成並覆寫 `data/level1_results.json`。訓練列數應約為舊版的三分之一。

- [ ] **Step 2: 印出新舊對照**

Run:
```bash
.venv/Scripts/python.exe -c "
import json
new = json.load(open('data/level1_results.json'))['horizons']
old = json.load(open('data/level1_results_u1.json'))['horizons']
head = ('h', 'model', '切段', 'IC', 'ICIR', 'mono', 'top20xs', 'win')
print('%3s %-10s %-8s %8s %6s %6s %9s %6s' % head)
for hz in ['1', '5', '10']:
    for m in ['random', 'mom_ret20', 'ridge_v2', 'lgbm']:
        for per in ['dev_oos', 'holdout']:
            r = new[hz][m][per]
            print('%3s %-10s %-8s %8.4f %6.2f %6.2f %8.3fpp %6.3f' % (
                hz, m, per, r['mean_ic'], r['icir'], r['monotonicity'],
                r['topk']['top20']['excess_pct'],
                r['topk']['top20']['day_win_rate']))
    o = old[hz]['lgbm']['holdout']
    print('    [舊 U_t lgbm holdout] IC=%s top20xs=%spp win=%s  <- 母體不同，僅供參照' % (
        o['mean_ic'], o['topk']['top20']['excess_pct'],
        o['topk']['top20']['day_win_rate']))
"
```
Expected: 完整對照表。**新舊數字不可直接比較**——它們量的是不同母體上的不同 target。

- [ ] **Step 3: 把結果寫進設計文件**

在 `docs/superpowers/specs/2026-08-28-level1-tradable-universe-design.md` 的 §12.2 之後，新增小節：

```markdown
### 12.3 重跑實績（新 U_t，YYYY-MM-DD）

[貼上 Step 2 的完整輸出]

停損點判定：[通過／不通過，逐條說明]
```

- [ ] **Step 4: 停損點判定**

依設計 §11 第 6 步，逐條檢查：

| 檢查 | 通過條件 | 不通過時的行動 |
|---|---|---|
| lgbm 的 IC 未崩 | dev_oos 與 holdout 的 `mean_ic` 皆 > 0.03 | **停止**，回報使用者重新評估 |
| 勝過 baseline | lgbm 的 `mean_ic` 在 dev_oos 高於 `random` 與 `mom_ret20` | **停止**，§9 升級閘門未過 |
| 5D 主軌可用 | 5D holdout 的 `topk.top20.day_win_rate` > 0.53 | **停止**，回報使用者 |
| 10D 個別檢視 | 10D holdout 的 `day_win_rate` > 0.53 | **不停止**，但記錄「10D 建議自展示層降級」並回報 |

先驗（設計 §12.1）：5D 預期 top20 超額約 +0.34pp、日勝率約 57%；10D 日勝率可能僅約 53%。

- [ ] **Step 5: 回報並取得繼續授權**

把 Step 2 的對照表與 Step 4 的判定結果回報使用者。**任一「停止」條件觸發時，本計畫在此中止**，不得逕行進入 Task 6。

- [ ] **Step 6: Commit**

```bash
git add backend/data/level1_results.json docs/superpowers/specs/2026-08-28-level1-tradable-universe-design.md
git commit -m "chore(level1): 新 U_t 上的 walk-forward 評估結果與停損點判定"
```

---

## Task 6: `level1_predict.py` 改版

**Files:**
- Modify: `backend/scripts/level1_predict.py`

**Interfaces:**
- Consumes: `universe.build_tradable_universe(con)`；`walkforward.train_slice_for_date`（Task 1 已接上）
- Produces: `MODEL_VERSION = "l1_lgbm_v2"`、`FEATURE_VERSION = "v2_feat20_u2"`（Task 7 的 `routes_level1.CURRENT_MODEL_VERSION` 必須與此一致）

- [ ] **Step 1: 換版本號**

把 `backend/scripts/level1_predict.py` 中：

```python
MODEL_VERSION = "l1_lgbm_v1"
FEATURE_VERSION = "v2_feat20"
```

改為：

```python
MODEL_VERSION = "l1_lgbm_v2"
# 特徵公式未改，但 rank_transform 是橫斷面操作：母體由 1801 縮至 ~564，
# 同股同日的特徵值必然改變，故版號必須換（設計 §8）
FEATURE_VERSION = "v2_feat20_u2"
```

- [ ] **Step 2: 改用單一 Universe 入口**

把 `_build_all()` 中：

```python
    con = sqlite3.connect(_DB)
    stocks = uv.load_stocks(con)
    elig = uv.eligible_ids(stocks)
    prices = uv.load_close_prices(con, elig)
    volq = pd.read_sql_query(
        "SELECT stock_id, date, volume FROM daily_prices WHERE volume IS NOT NULL", con)
    mkt = pd.read_sql_query("SELECT date, close FROM market_index", con,
                            index_col="date")["close"]
    con.close()

    close = uv.close_matrix(prices)
    mask = uv.universe_mask(close)
    volume = (volq[volq.stock_id.isin(elig)]
              .pivot_table(index="date", columns="stock_id", values="volume",
                           aggfunc="last")
              .reindex(index=close.index, columns=close.columns))
```

改為：

```python
    con = sqlite3.connect(_DB)
    close, mask = uv.build_tradable_universe(con)   # 唯一入口（設計 §6）
    elig = set(close.columns)
    volq = pd.read_sql_query(
        "SELECT stock_id, date, volume FROM daily_prices WHERE volume IS NOT NULL", con)
    mkt = pd.read_sql_query("SELECT date, close FROM market_index", con,
                            index_col="date")["close"]
    con.close()

    volume = (volq[volq.stock_id.isin(elig)]
              .pivot_table(index="date", columns="stock_id", values="volume",
                           aggfunc="last")
              .reindex(index=close.index, columns=close.columns))
```

- [ ] **Step 3: 更新 docstring 的凍結定義**

把檔頭 docstring 中 `Model Version 凍結定義（l1_lgbm_v1）：` 起算的整個區塊：

```
Model Version 凍結定義（l1_lgbm_v1）：
- 模型：LightGBM(n_estimators=100, random_state=42)，超參不得調（凍結）
- 特徵：v2_feat20 = 11 價量 + 5 PIT 基本面 + 4 市場 regime 交互（每日橫斷面 rank）
- Target：未來 N 日 Close-to-Close 報酬之 U_t 橫斷面百分位（N ∈ 1/5/10，5D 主軌）
- 訓練協定：expanding，用全部「已成熟」標籤——label 需要 t+N 收盤才存在，
  訓練集天然結束在預測日前 N 個交易日，與研究框架的 embargo 同語意。
```

改為：

```
Model Version 凍結定義（l1_lgbm_v2）：
- Universe：U_t = Structural ∩ TradingEligibility（存在 ∧ ADV20 ≥ 5,000 萬 ∧ 非處置），
  由 universe.build_tradable_universe() 單一入口產出，與研究端同一份程式碼。
- 模型：LightGBM(n_estimators=100, random_state=42, n_jobs=1)，超參不得調（凍結）。
  n_jobs=1 是 reproducibility control，不是 predictive-performance control。
- 特徵：v2_feat20_u2 = 11 價量 + 5 PIT 基本面 + 4 市場 regime 交互（每日橫斷面 rank）。
  公式同 v2_feat20，但橫斷面母體為新 U_t，故特徵值不同、版號另計。
- Target：未來 N 日 Close-to-Close 報酬之 U_t 橫斷面百分位（N ∈ 1/5/10，5D 主軌）
- 訓練協定：expanding，訓練窗由 walkforward.train_slice_for_date(pred_date, embargo=N)
  決定——與 OOS 共用同一函式。不得依賴「最近 N 日 label 為 NaN」的巧合式 embargo。
```

- [ ] **Step 4: 跑全套測試**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 全綠

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/level1_predict.py
git commit -m "feat(level1): predict 改用可交易 U_t，版本升 l1_lgbm_v2 / v2_feat20_u2"
```

---

## Task 7: `routes_level1.py` 版本過濾

v1 與 v2 在同一 `prediction_date` 並存時，現行查詢未過濾 `model_version`，同一支股票會回傳兩列、`rank` 重複，Top-K 直接失真；`performance()` 的 `avg()` 亦會跨版本混算。此項必須與 Task 6 同批上線。

**Files:**
- Modify: `backend/app/api/routes_level1.py`
- Create: `backend/tests/test_level1_board_api.py`

**Interfaces:**
- Consumes: `scripts.level1_predict.MODEL_VERSION`（Task 6 定為 `"l1_lgbm_v2"`）
- Produces: `routes_level1.CURRENT_MODEL_VERSION: str`

- [ ] **Step 1: 寫失敗測試**

建立 `backend/tests/test_level1_board_api.py`：

```python
"""Level 1 端點的版本隔離：v1/v2 ledger 並存時，board 不得混回兩版。

沿用 test_ctx_api.py 的 TestClient＋登入慣例：monkeypatch auth_enabled 為 True，
用 session_scope 建帳號、issue_token 換 session cookie。
"""

from __future__ import annotations

import secrets
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app import auth, main
from app.api import routes_level1
from app.storage import models
from app.storage.database import init_db, session_scope

_SUFFIX = secrets.token_hex(4)
EMAIL = f"l1-api-{_SUFFIX}@test.local"
PRED_DATE = date(2019, 1, 2)          # 遠早於真實資料，不干擾既有 ledger


@pytest.fixture(scope="module", autouse=True)
def _db():
    init_db()
    yield
    with session_scope() as s:
        s.query(models.Level1Prediction).filter(
            models.Level1Prediction.prediction_date == PRED_DATE).delete()
        u = s.query(models.User).filter(models.User.email == EMAIL).first()
        if u:
            s.delete(u)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "auth_enabled", lambda: True)
    return TestClient(main.app)


def _mk_user(email: str) -> tuple[int, str]:
    with session_scope() as s:
        u = s.query(models.User).filter(models.User.email == email).first()
        if u is None:
            u = models.User(email=email, password_hash=auth.hash_password("pw123456"),
                            tier="free", role="user", email_verified_at=datetime.now())
            s.add(u)
            s.flush()
        uid, sv = u.id, u.session_version
    return uid, auth.issue_token(uid, sv)


def _seed_two_versions():
    """同日同股寫入 v1 與現行版兩列 ledger（PK 含 model_version，可並存）。"""
    with session_scope() as s:
        if not s.query(models.Stock).filter(models.Stock.id == "9001").first():
            s.add(models.Stock(id="9001", name="測試股", is_etf=False, market="上市"))
        for ver, score in (("l1_lgbm_v1", 0.9),
                           (routes_level1.CURRENT_MODEL_VERSION, 0.8)):
            s.merge(models.Level1Prediction(
                prediction_date=PRED_DATE, stock_id="9001", horizon=5,
                model_version=ver, score=score, rank=1, pct_rank=1.0,
                universe_size=564, feature_version="test"))


def test_board_returns_only_current_model_version(client):
    _seed_two_versions()
    _, tok = _mk_user(EMAIL)
    client.cookies.set(auth.SESSION_COOKIE, tok)

    r = client.get("/api/level1/board?horizon=5&k=20")
    assert r.status_code == 200
    ids = [it["stock_id"] for it in r.json()["items"]]
    assert len(ids) == len(set(ids)), f"同一股票回傳多列（版本未隔離）：{ids}"


def test_current_model_version_matches_predict_script():
    """端點的版本常數必須與生產 pipeline 一致，否則畫面會永遠是空的。"""
    from scripts import level1_predict
    assert routes_level1.CURRENT_MODEL_VERSION == level1_predict.MODEL_VERSION
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/Scripts/python.exe -m pytest tests/test_level1_board_api.py -v`
Expected: FAIL，`AttributeError: module 'app.api.routes_level1' has no attribute 'CURRENT_MODEL_VERSION'`

- [ ] **Step 3: 加常數**

在 `backend/app/api/routes_level1.py` 的 `_HORIZONS = (1, 5, 10)` 之後加入：

```python
# 展示層只認一個版本。ledger PK 含 model_version，換版時舊列並存——不過濾會使同一
# 支股票回傳多列、rank 重複，Top-K 直接失真（設計 §8.1）。
# 此常數必須與 scripts/level1_predict.py 的 MODEL_VERSION 一致。
CURRENT_MODEL_VERSION = "l1_lgbm_v2"
```

- [ ] **Step 4: `board()` 兩處加過濾**

把：

```python
    d = session.execute(
        select(func.max(P.prediction_date)).where(P.horizon == horizon)
    ).scalar()
```

改為：

```python
    d = session.execute(
        select(func.max(P.prediction_date)).where(
            P.horizon == horizon, P.model_version == CURRENT_MODEL_VERSION)
    ).scalar()
```

把：

```python
        .where(P.horizon == horizon, P.prediction_date == d)
```

改為：

```python
        .where(P.horizon == horizon, P.prediction_date == d,
               P.model_version == CURRENT_MODEL_VERSION)
```

- [ ] **Step 5: `performance()` 三處加過濾**

把：

```python
    dates = session.execute(
        select(P.prediction_date).distinct()
        .where(P.horizon == horizon, P.actual_return.is_not(None))
```

改為：

```python
    dates = session.execute(
        select(P.prediction_date).distinct()
        .where(P.horizon == horizon, P.actual_return.is_not(None),
               P.model_version == CURRENT_MODEL_VERSION)
```

把：

```python
            .where(P.horizon == horizon, P.prediction_date == d,
                   P.rank <= k, P.actual_return.is_not(None))
```

改為：

```python
            .where(P.horizon == horizon, P.prediction_date == d,
                   P.rank <= k, P.actual_return.is_not(None),
                   P.model_version == CURRENT_MODEL_VERSION)
```

把：

```python
            .where(P.horizon == horizon, P.prediction_date == d,
                   P.actual_return.is_not(None))
```

改為：

```python
            .where(P.horizon == horizon, P.prediction_date == d,
                   P.actual_return.is_not(None),
                   P.model_version == CURRENT_MODEL_VERSION)
```

- [ ] **Step 6: 跑測試確認通過**

Run: `.venv/Scripts/python.exe -m pytest tests/test_level1_board_api.py -v`
Expected: PASS（2 條）

- [ ] **Step 7: 跑全套測試**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 全綠

- [ ] **Step 8: Commit**

```bash
git add backend/app/api/routes_level1.py backend/tests/test_level1_board_api.py
git commit -m "fix(level1): board/performance 過濾 model_version——版本並存不再打爆 Top-K"
```

---

## Task 8: A/B 一致性回歸測試

把本次的一次性稽核轉成永久護欄，防止研究與生產再度分叉。

**Files:**
- Create: `backend/tests/test_level1_pipeline_parity.py`

**Interfaces:**
- Consumes: `universe.build_tradable_universe`、`universe.load_turnover`、`universe.load_punish_windows`、`universe.tradable_mask`、`universe.punish_mask`、`universe.eligible_ids`、`universe.load_stocks`、`universe.load_close_prices`、`universe.close_matrix`、`walkforward.train_slice_for_date`、`walkforward.train_slice`、`features.assemble_dataset`

- [ ] **Step 1: 寫測試**

建立 `backend/tests/test_level1_pipeline_parity.py`：

```python
"""OOS 路徑與 Production 路徑的一致性護欄（設計 §10）。

本次改版的三個裂縫（訓練窗、推論母體、快照漂移）皆源於兩條路徑各自實作等價邏輯。
這裡把「必須共用同一個函式」的契約釘成測試。
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from app.research.level1 import universe as uv, walkforward as wf
from app.research.level1.features import assemble_dataset


def _memory_db():
    """兩檔高量普通股，20 個交易日，無處置。"""
    con = sqlite3.connect(":memory:")
    con.executescript(
        "CREATE TABLE stocks (id TEXT, name TEXT, is_etf INT, market TEXT,"
        " industry_category TEXT, listed_date TEXT);"
        "CREATE TABLE daily_prices (stock_id TEXT, date TEXT, close REAL, turnover REAL);"
        "CREATE TABLE attention_listings (stock_id TEXT, date TEXT, kind TEXT,"
        " begin_date TEXT, end_date TEXT);"
    )
    con.executemany("INSERT INTO stocks VALUES (?,?,?,?,?,?)", [
        ("2330", "甲", 0, "上市", "半導體業", "1994-09-05"),
        ("2317", "乙", 0, "上市", "電子業", "1991-06-18"),
    ])
    rows = []
    for d in range(1, 21):
        day = f"2025-01-{d:02d}"
        rows += [("2330", day, 100.0 + d, 4e9), ("2317", day, 50.0 + d, 3e9)]
    con.executemany("INSERT INTO daily_prices VALUES (?,?,?,?)", rows)
    return con


def test_universe_comes_from_a_single_entry_point():
    """手動組裝與 build_tradable_universe 必須逐格相同——任何呼叫端都不該自己拼。"""
    con = _memory_db()
    close, mask = uv.build_tradable_universe(con)

    elig = uv.eligible_ids(uv.load_stocks(con))
    manual_close = uv.close_matrix(uv.load_close_prices(con, elig))
    manual = uv.tradable_mask(
        manual_close,
        uv.load_turnover(con, elig, manual_close.index, manual_close.columns),
        uv.punish_mask(uv.load_punish_windows(con), manual_close.index,
                       manual_close.columns),
    )
    con.close()
    assert close.equals(manual_close)
    assert mask.equals(manual)


def test_build_tradable_universe_is_deterministic():
    """同一個 con 呼叫兩次必須完全相同（無隨機、無時間相依）。"""
    con = _memory_db()
    c1, m1 = uv.build_tradable_universe(con)
    c2, m2 = uv.build_tradable_universe(con)
    con.close()
    assert c1.equals(c2)
    assert m1.equals(m2)


def _synthetic_ranked(dates, cols, seed=0):
    rng = np.random.default_rng(seed)
    return {f"f{i}": pd.DataFrame(rng.random((len(dates), len(cols))),
                                  index=dates, columns=cols) for i in range(3)}


def test_training_set_never_contains_prediction_date_or_later():
    """Production 訓練集不得含 pred_date 當日或之後的任何列（P0 leakage 防線）。"""
    dates = pd.Index([f"2025-03-{d:02d}" for d in range(1, 29)])
    cols = pd.Index(["2330", "2317"])
    ranked = _synthetic_ranked(dates, cols)
    # target 全期皆有值——若訓練窗有誤，未來列會直接混進訓練集
    pct = pd.DataFrame(0.5, index=dates, columns=cols)

    pred_date = "2025-03-20"
    train_dates = wf.train_slice_for_date(dates, pred_date, embargo=5)
    _, _, meta = assemble_dataset(ranked, pct, train_dates)

    assert len(meta) > 0
    assert meta["date"].max() < pred_date
    assert meta["date"].max() == "2025-03-14"       # index 19 − 5 → dates[:14]


def test_production_and_oos_training_windows_are_identical():
    """同一預測日下，兩條路徑取得的訓練集必須逐列相同。"""
    dates = pd.Index([f"2025-03-{d:02d}" for d in range(1, 29)])
    cols = pd.Index(["2330", "2317"])
    ranked = _synthetic_ranked(dates, cols)
    pct = pd.DataFrame(0.5, index=dates, columns=cols)

    i = 19
    x_prod, y_prod, m_prod = assemble_dataset(
        ranked, pct, wf.train_slice_for_date(dates, dates[i], embargo=5))
    x_oos, y_oos, m_oos = assemble_dataset(
        ranked, pct, wf.train_slice(dates, i, embargo=5))

    assert np.array_equal(x_prod, x_oos)
    assert np.array_equal(y_prod, y_oos)
    assert m_prod.equals(m_oos)
```

- [ ] **Step 2: 跑測試**

Run: `.venv/Scripts/python.exe -m pytest tests/test_level1_pipeline_parity.py -v`
Expected: PASS（5 條）。若 `test_training_set_never_contains_prediction_date_or_later` 失敗，代表 Task 1 未正確接上。

- [ ] **Step 3: 跑全套測試**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 全綠

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_level1_pipeline_parity.py
git commit -m "test(level1): A/B 一致性護欄——單一 Universe 入口與訓練窗共用契約"
```

---

## Task 9: 上線最新交易日並驗收

**Files:** 無程式碼變更（產生 ledger 資料）

**Interfaces:**
- Consumes: Task 6 的 `level1_predict.py`、Task 7 的 `routes_level1.py`

- [ ] **Step 1: 跑生產 pipeline**

Run: `.venv/Scripts/python.exe -m scripts.level1_predict`
Expected: 三個 horizon 各印出「訓練 N 列（迄 YYYY-MM-DD）、寫入 M 檔」。**檢查 M 應在 500–800 之間**（新 U_t 規模），不是 1801。訓練窗終點應為預測日往前 N 個交易日。

- [ ] **Step 2: 驗證版本並存且新版規模正確**

Run:
```bash
.venv/Scripts/python.exe -c "
import sqlite3
c = sqlite3.connect('data/twa.db')
for row in c.execute('''select model_version, horizon, count(distinct prediction_date),
                        max(prediction_date), count(*), max(universe_size)
                        from level1_predictions group by 1, 2 order by 1, 2'''):
    print(row)
"
```
Expected: `l1_lgbm_v1` 的舊列原封不動；`l1_lgbm_v2` 有新列，`universe_size` 在 500–800 之間。

- [ ] **Step 3: 瀏覽器實測畫面**

用 `preview_start` 開 `http://127.0.0.1:8000/app/level1`，以 `read_page` 檢查三點：

1. 榜單有資料且無重複股票（版本過濾生效）
2. 標頭的 Universe 檔數為新 U_t 規模（500–800），不是 1801
3. Top-20 由具規模的股票組成，不再是日成交 10 萬元的殼股

再以 `read_console_messages` 確認無錯誤。

Expected: 三點皆符合、console 乾淨。

- [ ] **Step 4: 跑全套測試**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 全綠

- [ ] **Step 5: Commit**

```bash
git commit --allow-empty -m "chore(level1): l1_lgbm_v2 上線最新交易日，v1 ledger 並存留檔"
```

- [ ] **Step 6: 回報並交還畫面設計**

回報使用者：新 U_t 規模、Top-20 實際內容、Task 5 的停損點判定結果、10D 是否建議降級。

本計畫至此結束。`/app/level1` 的畫面設計（兩籤：今日榜單 / 模型體檢）另行規劃——
其中「已濾掉 N 檔」的展示層過濾構想**已作廢**（設計 §13：不在展示層做過濾，
否則 Production 會變回 Model + Rule Filter）。
