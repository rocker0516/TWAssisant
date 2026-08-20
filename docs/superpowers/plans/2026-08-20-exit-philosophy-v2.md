# 出場建議 v2 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 波段軌出場改為論點失效狀態機（停損/兌現/到期重審三出口），長線軌改長線分數滑落為主訊號，UI 與設定頁同步。

**Architecture:** 新增 `Holding.thesis` JSON 欄位存論點快照與狀態；新模組 `thesis_engine.py` 為純函式狀態機；`ExitEngine.evaluate()` 依 track 分流（wave→論點機、long→現有訊號聚合），`ExitEngine.run()`（日更步驟）負責狀態轉移與到期重審的持久化。長線軌只改 `FundamentalWeakSignal` 與設定。

**Tech Stack:** FastAPI + SQLAlchemy(SQLite) + pytest；前端 React + TypeScript。

**Spec:** `docs/superpowers/specs/2026-08-20-exit-philosophy-v2-design.md`

## Global Constraints

- 天數口徑：訊號隔日＝第 1 天，交易日計（`opened_date` 或重審日視為 clock_start，clock_start 當天＝第 1 天）。
- 同日碰停損與目標 → 先判停損（與 `strategy_engine._judge` 一致）。
- 重審上限 2 次，超過直接 🔴。
- 價格錨用 `avg_cost`（非回測的隔日 high）。
- 波段軌停用：TrailingStopSignal、TechWeakSignal、一般利空；保留處置警示（🟠）。
- 手動/推薦來源持股預設：10 日 / +10% / 停損 8%（settings `exit.wave_defaults` 可改）。
- schema 遷移走 `database.py` 的 `_COLUMN_ADDITIONS`（PRAGMA 查了才 ALTER，冪等）。
- 測試在 `backend/` 目錄下跑：`python -m pytest tests/<file> -v`。
- commit 訊息結尾加 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。

---

### Task 1: Holding.thesis 欄位與論點快照

**Files:**
- Modify: `backend/app/storage/models.py`（Holding 加 `thesis` 欄）
- Modify: `backend/app/storage/database.py`（`_COLUMN_ADDITIONS["holdings"]` 加 `"thesis": "JSON"`）
- Modify: `backend/app/services/holding_service.py`（`_capture_thesis` + `create()` 接 `strategy_id`）
- Modify: `backend/app/api/schemas.py`（`HoldingCreate` 加 `strategy_id: int | None = None`）
- Modify: `backend/app/api/routes_holdings.py`（create 端點把 `strategy_id` 傳給 service）
- Test: `backend/tests/test_thesis_snapshot.py`

**Interfaces:**
- Produces: `Holding.thesis: dict | None`，結構：
  ```json
  {
    "target_pct": 10.0, "horizon_days": 10, "stop_pct": 8.0,
    "source": "strategy" | "rec" | "manual",
    "strategy_id": 3,                  // source=strategy 才有
    "conditions": [...],               // source=strategy 才有（凍結副本）
    "clock_start": "2026-08-20",
    "reaudit_count": 0,
    "state": "active"                  // active/fulfilled/expired/refuted
  }
  ```
- Produces: `HoldingService.create(..., strategy_id: int | None = None)`；`_capture_thesis(session, *, user_id, track, date_, strategy_id) -> dict | None`（track != "wave" 回 None）。

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_thesis_snapshot.py
"""建倉論點快照：strategy 來源凍結參數；manual 用預設；long 軌無 thesis。"""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services.holding_service import HoldingService
from app.storage import models

D = date(2026, 8, 20)


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add_all([
        models.User(id=1, email="u@x", password_hash="h"),
        models.Stock(id="1101", name="甲", is_etf=False),
        models.UserStrategy(id=3, user_id=1, name="S", conditions=[{"field": "close", "op": "gt", "value": 100}],
                            target_pct=12.0, horizon_days=7, stop_pct=6.0),
    ])
    s.commit()
    yield s
    s.close()


def test_strategy_source_freezes_params(session):
    h = HoldingService().create(session, user_id=1, stock_id="1101", track="wave",
                                date_=D, price=100, shares=1, strategy_id=3)
    t = h.thesis
    assert t["source"] == "strategy" and t["strategy_id"] == 3
    assert (t["target_pct"], t["horizon_days"], t["stop_pct"]) == (12.0, 7, 6.0)
    assert t["conditions"] == [{"field": "close", "op": "gt", "value": 100}]
    assert t["clock_start"] == D.isoformat()
    assert t["reaudit_count"] == 0 and t["state"] == "active"


def test_manual_source_uses_defaults(session):
    h = HoldingService().create(session, user_id=1, stock_id="1101", track="wave",
                                date_=D, price=100, shares=1)
    t = h.thesis
    assert t["source"] == "manual"
    assert (t["target_pct"], t["horizon_days"], t["stop_pct"]) == (10.0, 10, 8.0)


def test_long_track_has_no_thesis(session):
    h = HoldingService().create(session, user_id=1, stock_id="1101", track="long",
                                date_=D, price=100, shares=1)
    assert h.thesis is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_thesis_snapshot.py -v`（在 `backend/`）
Expected: FAIL（`thesis` 欄位不存在 / create 不接 strategy_id）

- [ ] **Step 3: 實作**

`models.py` Holding 加（放在 `entry_snapshot` 旁）：

```python
    # 波段論點快照+狀態機（spec 2026-08-20-exit-philosophy-v2）。long 軌為 None。
    thesis: Mapped[dict | None] = mapped_column(JSON)
```

`database.py`：`_COLUMN_ADDITIONS["holdings"]` 改為
`{"entry_snapshot": "JSON", "thesis": "JSON", "user_id": "INTEGER REFERENCES users(id)"}`。

`holding_service.py` 加：

```python
WAVE_THESIS_DEFAULTS = {"target_pct": 10.0, "horizon_days": 10, "stop_pct": 8.0}


def _capture_thesis(session: Session, *, user_id: int, track: str,
                    date_: date, strategy_id: int | None) -> dict | None:
    """波段建倉論點快照。strategy 來源凍結該策略參數與條件；其餘用全域預設。"""
    if track != "wave":
        return None
    base = {"clock_start": date_.isoformat(), "reaudit_count": 0, "state": "active"}
    if strategy_id is not None:
        st = session.get(models.UserStrategy, strategy_id)
        if st is not None and st.user_id == user_id:
            return {**base, "source": "strategy", "strategy_id": st.id,
                    "conditions": list(st.conditions or []),
                    "target_pct": st.target_pct, "horizon_days": st.horizon_days,
                    "stop_pct": st.stop_pct if st.stop_pct is not None
                    else WAVE_THESIS_DEFAULTS["stop_pct"]}
    row = session.get(models.Setting, "exit")
    cfg = (row.value or {}).get("wave_defaults", {}) if row and isinstance(row.value, dict) else {}
    return {**base, "source": "manual", **{**WAVE_THESIS_DEFAULTS, **{k: v for k, v in cfg.items() if v is not None}}}
```

`create()` 簽名加 `strategy_id: int | None = None`，建構 Holding 時加
`thesis=_capture_thesis(session, user_id=user_id, track=track, date_=date_, strategy_id=strategy_id)`。

`schemas.py` `HoldingCreate` 加 `strategy_id: int | None = None`；
`routes_holdings.py` create 端點呼叫 `_svc.create(...)` 處把 `strategy_id=body.strategy_id` 傳入。

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_thesis_snapshot.py -v`
Expected: PASS（3 tests）

- [ ] **Step 5: Commit**

```bash
git add backend/app/storage/models.py backend/app/storage/database.py backend/app/services/holding_service.py backend/app/api/schemas.py backend/app/api/routes_holdings.py backend/tests/test_thesis_snapshot.py
git commit -m "feat(exit): Holding.thesis 論點快照——strategy 凍結參數、manual 用預設"
```

---

### Task 2: thesis_engine 純函式狀態機

**Files:**
- Create: `backend/app/engines/thesis_engine.py`
- Test: `backend/tests/test_thesis_engine.py`

**Interfaces:**
- Consumes: Task 1 的 thesis dict 結構。
- Produces:

```python
@dataclass
class ThesisEval:
    state: str        # active / expiring / awaiting_reaudit / fulfilled / expired / refuted
    level: str        # green / yellow / orange / red
    days_elapsed: int # clock_start 當天=1，交易日計
    days_left: int    # horizon - days_elapsed，最小 0
    reaudit_count: int
    target_price: float
    stop_price: float
    messages: list[str]

def evaluate_thesis(thesis: dict, *, avg_cost: float, hi_since_clock: float,
                    lo_today: float | None, days_elapsed: int,
                    reaudit_max: int = 2) -> ThesisEval
```

優先序：已終結狀態（fulfilled/expired/refuted 原樣回報）→ 停損（`lo_today <= stop_price`，同日雙碰先判停損）→ 兌現（`hi_since_clock >= target_price`）→ 到期（`days_elapsed >= horizon`：`reaudit_count >= reaudit_max` → expired🔴，否則 awaiting_reaudit🟠）→ 倒數 2 日內 expiring🟡 → active🟢。

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_thesis_engine.py
"""論點狀態機：三出口優先序、到期重審、倒數預告。"""
from app.engines.thesis_engine import evaluate_thesis

T = {"target_pct": 10.0, "horizon_days": 10, "stop_pct": 8.0,
     "source": "manual", "clock_start": "2026-08-20", "reaudit_count": 0, "state": "active"}


def ev(**kw):
    args = dict(avg_cost=100.0, hi_since_clock=100.0, lo_today=100.0, days_elapsed=1)
    args.update(kw)
    return evaluate_thesis({**T, **kw.pop("thesis", {})} if "thesis" in kw else dict(T), **args)


def test_active_green():
    r = ev(days_elapsed=3)
    assert (r.state, r.level) == ("active", "green")
    assert r.target_price == 110.0 and r.stop_price == 92.0
    assert r.days_left == 7


def test_stop_beats_target_same_day():
    r = ev(hi_since_clock=111.0, lo_today=91.0)
    assert (r.state, r.level) == ("refuted", "red")


def test_fulfilled_on_target_touch():
    r = ev(hi_since_clock=110.0)
    assert (r.state, r.level) == ("fulfilled", "red")


def test_expiring_yellow_last_two_days():
    assert ev(days_elapsed=9).state == "expiring"
    assert ev(days_elapsed=9).level == "yellow"
    assert ev(days_elapsed=8).state == "expiring"
    assert ev(days_elapsed=7).state == "active"


def test_expiry_awaits_reaudit_then_hard_expires():
    r = ev(days_elapsed=10)
    assert (r.state, r.level) == ("awaiting_reaudit", "orange")
    r2 = evaluate_thesis({**T, "reaudit_count": 2}, avg_cost=100.0,
                         hi_since_clock=100.0, lo_today=100.0, days_elapsed=10)
    assert (r2.state, r2.level) == ("expired", "red")


def test_terminal_state_passthrough():
    r = evaluate_thesis({**T, "state": "fulfilled"}, avg_cost=100.0,
                        hi_since_clock=120.0, lo_today=80.0, days_elapsed=12)
    assert (r.state, r.level) == ("fulfilled", "red")
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_thesis_engine.py -v`
Expected: FAIL（模組不存在）

- [ ] **Step 3: 實作 `backend/app/engines/thesis_engine.py`**

```python
"""波段論點狀態機（spec 2026-08-20-exit-philosophy-v2）。

純函式：不碰 DB。三出口優先序＝反證(停損) → 兌現 → 過期，
同日雙碰先判停損（與 strategy_engine._judge 回測口徑一致）。
狀態持久化由 ExitEngine.run() 負責，本模組只算。
"""

from __future__ import annotations

from dataclasses import dataclass, field

TERMINAL = {"fulfilled", "expired", "refuted"}


@dataclass
class ThesisEval:
    state: str
    level: str
    days_elapsed: int
    days_left: int
    reaudit_count: int
    target_price: float
    stop_price: float
    messages: list[str] = field(default_factory=list)


def evaluate_thesis(thesis: dict, *, avg_cost: float, hi_since_clock: float,
                    lo_today: float | None, days_elapsed: int,
                    reaudit_max: int = 2) -> ThesisEval:
    horizon = int(thesis["horizon_days"])
    target = round(avg_cost * (1 + thesis["target_pct"] / 100), 2)
    stop = round(avg_cost * (1 - thesis["stop_pct"] / 100), 2)
    n = int(thesis.get("reaudit_count", 0))
    left = max(0, horizon - days_elapsed)

    def out(state, level, msgs):
        return ThesisEval(state, level, days_elapsed, left, n, target, stop, msgs)

    prior = thesis.get("state", "active")
    if prior in TERMINAL:
        msg = {"fulfilled": "論點已兌現", "expired": "論點已過期", "refuted": "論點已反證"}[prior]
        return out(prior, "red", [msg])
    if lo_today is not None and lo_today <= stop:
        return out("refuted", "red", [f"論點反證：觸及停損價 {stop:.2f}，建議出場"])
    if hi_since_clock >= target:
        return out("fulfilled", "red", [f"論點兌現：觸及目標價 {target:.2f}，建議獲利了結"])
    if days_elapsed >= horizon:
        if n >= reaudit_max:
            return out("expired", "red", [f"論點過期：已重審 {n}/{reaudit_max} 次，建議出場"])
        return out("awaiting_reaudit", "orange", [f"第 {horizon} 天未兌現，待重審進場條件"])
    if left <= 2:
        return out("expiring", "yellow", [f"論點倒數 {left} 天（第 {days_elapsed}/{horizon} 天）"])
    return out("active", "green", [])
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_thesis_engine.py -v`
Expected: PASS（6 tests）

- [ ] **Step 5: Commit**

```bash
git add backend/app/engines/thesis_engine.py backend/tests/test_thesis_engine.py
git commit -m "feat(exit): thesis_engine 論點狀態機——三出口優先序與到期重審狀態"
```

---

### Task 3: ExitEngine 分流——波段走論點機、日更做狀態轉移與重審

**Files:**
- Modify: `backend/app/engines/exit_engine.py`
- Test: `backend/tests/test_exit_engine_wave.py`

**Interfaces:**
- Consumes: `evaluate_thesis`（Task 2）、thesis dict（Task 1）、`strategy_engine.evaluate(session, conditions, [td])`、`strategy_engine.trading_dates(session, start, end)`。
- Produces: `ExitStatus` 加欄位：

```python
    thesis_state: str | None = None
    days_left: int | None = None
    reaudit_count: int | None = None
    target_price: float | None = None
    stop_price: float | None = None
```

`evaluate()`：track=="wave" 且 `holding.thesis` 非 None → 論點路徑（不跑 ALL_SIGNALS，僅附加處置警示檢查：`ctx.events` 中 `category=="處置警示"` → 至少 🟠）。track=="long" 或無 thesis（尚未日更補快照的舊持股）→ 原有訊號聚合路徑不變。

`run()`：日更最高價之外，對每筆 open+wave 持股：(1) `thesis is None` → 補預設快照（`clock_start=trading_date`，冪等一次性遷移）；(2) 用 `evaluate_thesis` 判定，`awaiting_reaudit` → 重審：strategy 來源＝`strategy_engine.evaluate(session, thesis["conditions"], [trading_date])` 含該股；rec/manual 來源＝該股當日（≤trading_date 最新）wave Score `passed_filter is True`。通過 → `clock_start=trading_date`、`reaudit_count+=1`；不通過 → `state="expired"`。(3) refuted/fulfilled 判定成立 → 寫回 `state`。thesis 為 JSON 欄位，**必須整個 dict 重新指派**（`h.thesis = {**h.thesis, ...}`）才會標記 dirty。

天數計算：`days_elapsed = len(trading_dates(session, clock_start, td))`（含首尾）。`hi_since_clock = highest_since(session, sid, clock_start, td)`。`lo_today` = 當日 DailyPrice.low。

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_exit_engine_wave.py
"""ExitEngine 波段分流：論點路徑、日更狀態轉移、重審、舊持股補快照。"""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.engines.exit_engine import ExitEngine
from app.storage import models

D0 = date(2026, 8, 3)  # 週一


def _mk_days(n):
    """n 個連續平日（近似交易日）。"""
    out, d = [], D0
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    days = _mk_days(12)
    s.add_all([models.User(id=1, email="u@x", password_hash="h"),
               models.Stock(id="1101", name="甲", is_etf=False)])
    for d in days:  # 12 日橫盤：不觸目標不觸停損
        s.add(models.MarketIndex(date=d, close=20000))
        s.add(models.DailyPrice(stock_id="1101", date=d, open=100, high=102,
                                low=99, close=100, volume=1000))
    s.commit()
    s.days = days
    yield s
    s.close()


def _holding(s, thesis, opened):
    h = models.Holding(user_id=1, stock_id="1101", track="wave", status="open",
                       opened_date=opened, thesis=thesis)
    s.add(h)
    s.add(models.Transaction(user_id=1, holding_id=None, type="buy",
                             date=opened, price=100, shares=1))
    s.flush()
    return h


BASE = {"target_pct": 10.0, "horizon_days": 10, "stop_pct": 8.0,
        "source": "manual", "reaudit_count": 0, "state": "active"}


def test_evaluate_wave_uses_thesis_not_signals(session):
    h = _holding(session, {**BASE, "clock_start": session.days[0].isoformat()}, session.days[0])
    st = ExitEngine().evaluate(session, h, session.days[2], avg_cost=100.0, close=100.0)
    assert st.thesis_state == "active" and st.level == "green"
    assert st.target_price == 110.0 and st.stop_price == 92.0
    assert st.days_left == 10 - 3


def test_run_reaudit_manual_fails_without_score(session):
    # 第 10 個交易日到期；無 wave Score → 重審不過 → expired
    h = _holding(session, {**BASE, "clock_start": session.days[0].isoformat()}, session.days[0])
    ExitEngine().run(session, session.days[9])
    assert h.thesis["state"] == "expired"


def test_run_reaudit_manual_passes_with_score_resets_clock(session):
    session.add(models.Score(stock_id="1101", date=session.days[9], track="wave",
                             passed_filter=True, passed=True))
    session.commit()
    h = _holding(session, {**BASE, "clock_start": session.days[0].isoformat()}, session.days[0])
    ExitEngine().run(session, session.days[9])
    assert h.thesis["state"] == "active"
    assert h.thesis["reaudit_count"] == 1
    assert h.thesis["clock_start"] == session.days[9].isoformat()


def test_run_backfills_thesis_for_legacy_wave_holding(session):
    h = _holding(session, None, session.days[0])
    ExitEngine().run(session, session.days[3])
    assert h.thesis is not None
    assert h.thesis["clock_start"] == session.days[3].isoformat()  # 時鐘自遷移日起算
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_exit_engine_wave.py -v`
Expected: FAIL（ExitStatus 無 thesis_state 等欄位）

- [ ] **Step 3: 實作**

`exit_engine.py`：

1. `ExitStatus` 加上述 5 個欄位（皆預設 None）。
2. 頂部 import：`from .thesis_engine import evaluate_thesis`、
   `from ..services.strategy_engine import evaluate as eval_conditions, trading_dates`。
3. 私有 helper：

```python
def _reaudit_max(session: Session) -> int:
    row = session.get(models.Setting, "exit")
    v = (row.value or {}) if row and isinstance(row.value, dict) else {}
    return int(v.get("reaudit_max", 2))


def _days_elapsed(session: Session, clock_start: date, td: date) -> int:
    return max(1, len(trading_dates(session, clock_start, td)))


def _low_today(session: Session, stock_id: str, td: date) -> float | None:
    return session.execute(
        select(models.DailyPrice.low).where(
            models.DailyPrice.stock_id == stock_id, models.DailyPrice.date == td)
    ).scalar()


def _reaudit_pass(session: Session, h: models.Holding, td: date) -> bool:
    t = h.thesis
    if t.get("source") == "strategy" and t.get("conditions"):
        hit = eval_conditions(session, t["conditions"], [td])
        return h.stock_id in hit.get(td, [])
    sc = session.execute(
        select(models.Score).where(
            models.Score.stock_id == h.stock_id, models.Score.track == "wave",
            models.Score.date <= td).order_by(models.Score.date.desc()).limit(1)
    ).scalars().first()
    return bool(sc and sc.passed_filter)
```

4. `evaluate()`：在載入 config 後分流——

```python
        if holding.track == "wave" and holding.thesis:
            return self._evaluate_wave(session, holding, td, avg_cost=avg_cost, close=close)
```

`_evaluate_wave`：組 `clock_start = date.fromisoformat(thesis["clock_start"])`、
`hi = self.highest_since(session, sid, clock_start, td) or close`、`lo = _low_today(...)`、
`days = _days_elapsed(...)`，呼叫 `evaluate_thesis(..., reaudit_max=_reaudit_max(session))`。
處置警示：查 `models.Event`（近 10 日、`is_risk`、`category=="處置警示"`），有 → level 至少
orange（green/yellow 升為 orange）並附訊息。回傳 ExitStatus（`hard_stop=ev.stop_price`、
`signals=ev.messages`、thesis 五欄填入、`highest=hi`、`drawdown_pct` 照舊算、
`trail_active=False`）。燈號 emoji 用既有 `_LIGHT[level]`。

5. `run()`：迴圈內對 `h.track == "wave"` 加狀態轉移（在日更 highest 之後）：

```python
            if h.track != "wave":
                continue
            if h.thesis is None:  # 一次性遷移：舊持股補預設快照
                from ..services.holding_service import WAVE_THESIS_DEFAULTS
                h.thesis = {**WAVE_THESIS_DEFAULTS, "source": "manual",
                            "clock_start": trading_date.isoformat(),
                            "reaudit_count": 0, "state": "active"}
                continue
            if h.thesis.get("state") in ("fulfilled", "expired", "refuted"):
                continue
            pos_avg = self_svc_avg_cost  # 用 HoldingService().position(session, h).avg_cost
            ...
            ev = evaluate_thesis(h.thesis, avg_cost=pos_avg, hi_since_clock=hi,
                                 lo_today=lo, days_elapsed=days, reaudit_max=rmax)
            if ev.state in ("refuted", "fulfilled"):
                h.thesis = {**h.thesis, "state": ev.state,
                            "settled_date": trading_date.isoformat()}
            elif ev.state == "awaiting_reaudit":
                if _reaudit_pass(session, h, trading_date):
                    h.thesis = {**h.thesis, "clock_start": trading_date.isoformat(),
                                "reaudit_count": int(h.thesis.get("reaudit_count", 0)) + 1}
                else:
                    h.thesis = {**h.thesis, "state": "expired",
                                "settled_date": trading_date.isoformat()}
```

（`avg_cost` 取 `HoldingService().position(session, h).avg_cost`；無成本/無股數者跳過。
模組層建一個 `_svc = HoldingService()` 即可，注意避免循環 import——holding_service 不
import exit_engine，方向安全。）

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_exit_engine_wave.py tests/test_thesis_engine.py -v`
Expected: PASS

- [ ] **Step 5: 跑既有測試防退化**

Run: `python -m pytest tests/ -v`
Expected: 全部 PASS（長線路徑未動）

- [ ] **Step 6: Commit**

```bash
git add backend/app/engines/exit_engine.py backend/tests/test_exit_engine_wave.py
git commit -m "feat(exit): ExitEngine 波段分流——論點狀態機取代訊號聚合，日更做重審與遷移"
```

---

### Task 4: 長線軌小修——分數滑落主訊號、營收降級、法人窗拉長

**Files:**
- Modify: `backend/app/engines/exit_signals.py`
- Modify: `backend/app/engines/context.py`（若無取多日 Score 的介面，改由訊號自查——見下）
- Test: `backend/tests/test_exit_signals_long.py`

**Interfaces:**
- Consumes: `holding.entry_snapshot["total_score"]`（既有）、`ctx`。
- Produces: 新 `ScoreSlipSignal(ExitSignal)`（tracks=("long",)），需要 session 查分數 →
  **StockContext 不含 Score**，因此改為在 `_build_context` 附掛：`ctx` 加欄位
  `long_scores: list[float]`（該股 long 軌最近 5 筆 total_score，新→舊）。
  `exit_engine._build_context` 查詢後塞入。
- 設定鍵：`exit.long.score_slip_warn`（預設 15，單位＝分）。

行為定義：
- `baseline = entry_snapshot["total_score"]`；無快照或無近 5 日分數 → 訊號不觸發。
- `cur = mean(long_scores)`（近 5 筆均值，防發布日跳動）。
- `baseline - cur >= slip_warn` → WARN「長線分數自進場 X 降至 Y」。
- 同時最新一筆 Score `passed_filter` 為 False → CRITICAL「分數滑落且跌破持有門檻」。
- `FundamentalWeakSignal`：`yoy < -10` 由 CRITICAL 降為 **WARN**；`inst_sum` 窗 10 → **20**
  （分母同步 `vma_lots * 20`）。

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_exit_signals_long.py
"""長線出場小修：分數滑落主訊號、營收 yoy 降級、法人 20 日窗。"""
import pandas as pd
import pytest

from app.engines import exit_signals as xs
from app.engines.exit_signals import Position, ScoreSlipSignal, FundamentalWeakSignal, Sev
from app.storage import models


class Ctx:  # 最小假 context
    def __init__(self, long_scores=None, revenue=None, inst=None, ind=None):
        self.long_scores = long_scores or []
        self.revenue = revenue
        self.inst = inst if inst is not None else pd.DataFrame(
            columns=["foreign_net", "trust_net", "dealer_net", "total_net"])
        self._ind = ind

    @property
    def ind(self):
        return self._ind

    def inst_sum(self, col, n):
        return float(self.inst[col].tail(n).sum()) if not self.inst.empty else 0.0


def _h(entry_score=70.0):
    h = models.Holding(stock_id="1101", track="long",
                       entry_snapshot={"total_score": entry_score})
    return h


POS = Position(shares=1, avg_cost=100, highest=100, close=100)


def test_score_slip_warn():
    hits = ScoreSlipSignal().check(_h(70), POS, Ctx(long_scores=[54, 55, 55, 56, 55]))
    assert len(hits) == 1 and hits[0].sev == Sev.WARN


def test_score_slip_critical_when_filter_lost():
    ctx = Ctx(long_scores=[50, 51, 52, 50, 51])
    ctx.long_passed_filter = False
    hits = ScoreSlipSignal().check(_h(70), POS, ctx)
    assert hits[0].sev == Sev.CRITICAL


def test_score_slip_silent_without_snapshot():
    h = models.Holding(stock_id="1101", track="long", entry_snapshot=None)
    assert ScoreSlipSignal().check(h, POS, Ctx(long_scores=[50] * 5)) == []


def test_yoy_deep_negative_is_warn_not_critical():
    ctx = Ctx(revenue=pd.Series({"yoy": -20.0}))
    hits = FundamentalWeakSignal().check(_h(), POS, ctx)
    assert any(h.code == "rev_drop" and h.sev == Sev.WARN for h in hits)
```

（`ScoreSlipSignal` 讀 `ctx.long_scores: list[float]`（新→舊）與
`ctx.long_passed_filter: bool | None`（最新一筆的 passed_filter，取不到＝None 視為 True）。）

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_exit_signals_long.py -v`
Expected: FAIL（ScoreSlipSignal 不存在）

- [ ] **Step 3: 實作**

`exit_signals.py`：

1. `DEFAULTS["long"]` 加 `"score_slip_warn": 15.0`；`set_config` 對 `score_slip_warn`
   直接原值寫入（單位是分，不除 100）。
2. 新增：

```python
class ScoreSlipSignal(ExitSignal):
    """長線分數滑落（主基本面訊號）：近 5 日均值 vs 進場快照分數。"""

    tracks = ("long",)

    def check(self, holding, pos, ctx):
        snap = holding.entry_snapshot or {}
        baseline = snap.get("total_score")
        scores = getattr(ctx, "long_scores", None) or []
        if baseline is None or len(scores) < 5:
            return []
        cur = sum(scores[:5]) / 5
        slip = float(baseline) - cur
        warn_at = _ACTIVE["long"].get("score_slip_warn", 15.0)
        if slip < warn_at:
            return []
        passed = getattr(ctx, "long_passed_filter", None)
        if passed is False:
            return [Hit("score_slip", Sev.CRITICAL,
                        f"長線分數自 {baseline:.0f} 降至 {cur:.0f} 且跌破持有門檻")]
        return [Hit("score_slip", Sev.WARN, f"長線分數自 {baseline:.0f} 降至 {cur:.0f}")]
```

3. `FundamentalWeakSignal`：`yoy < -10` 的 Sev.CRITICAL 改 Sev.WARN；
   `inst_sum(..., 10)` 兩處改 20、分母 `vma_lots * 10` 改 `* 20`。
4. `ALL_SIGNALS` 在 `FundamentalWeakSignal()` 前插入 `ScoreSlipSignal()`。
5. `exit_engine._build_context`：查該股 long 軌最近 5 筆 Score（date desc），
   `ctx.long_scores = [s.total_score for s in rows if s.total_score is not None]`、
   `ctx.long_passed_filter = rows[0].passed_filter if rows else None`；
   `StockContext` 若是 dataclass 不便加欄，直接 `object.__setattr__` 或在 dataclass
   加兩個預設欄位（`long_scores: list = field(default_factory=list)`、
   `long_passed_filter: bool | None = None`）——採後者。

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_exit_signals_long.py -v`
Expected: PASS（4 tests）

- [ ] **Step 5: Commit**

```bash
git add backend/app/engines/exit_signals.py backend/app/engines/exit_engine.py backend/app/engines/context.py backend/tests/test_exit_signals_long.py
git commit -m "feat(exit): 長線分數滑落主訊號＋營收yoy降級＋法人窗拉長20日"
```

---

### Task 5: API DTO 與設定鍵

**Files:**
- Modify: `backend/app/api/schemas.py`（ExitStatus DTO 加 thesis 五欄）
- Modify: `backend/app/api/routes_holdings.py`（`build_item` 把新欄位帶出）
- Modify: 設定相關 route/schema（找到 `Setting key=="exit"` 的讀寫端點；波段鍵改
  `wave_defaults: {target_pct, horizon_days, stop_pct}` + `reaudit_max`，長線鍵加
  `score_slip_warn`；舊的 wave `stop_cap/trail_trigger/trail_pullback` 讀入時忽略）
- Test: `backend/tests/test_holdings_api_thesis.py`

**Interfaces:**
- Consumes: Task 3 的 ExitStatus 新欄位。
- Produces: HoldingItem JSON 內 `exit_status` 物件多出
  `thesis_state / days_left / reaudit_count / target_price / stop_price`（long 軌為 null）。

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_holdings_api_thesis.py
"""持股 API 帶出論點欄位。走 build_item 層級（不起 HTTP）。"""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes_holdings import build_item
from app.storage import models

D = date(2026, 8, 20)


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add_all([models.User(id=1, email="u@x", password_hash="h"),
               models.Stock(id="1101", name="甲", is_etf=False),
               models.MarketIndex(date=D, close=20000),
               models.DailyPrice(stock_id="1101", date=D, open=100, high=102,
                                 low=99, close=100, volume=1000)])
    s.commit()
    yield s
    s.close()


def test_build_item_exposes_thesis_fields(session):
    h = models.Holding(user_id=1, stock_id="1101", track="wave", status="open",
                       opened_date=D,
                       thesis={"target_pct": 10.0, "horizon_days": 10, "stop_pct": 8.0,
                               "source": "manual", "clock_start": D.isoformat(),
                               "reaudit_count": 0, "state": "active"})
    session.add(h)
    session.flush()
    session.add(models.Transaction(user_id=1, holding_id=h.id, type="buy",
                                   date=D, price=100, shares=1))
    session.commit()
    item = build_item(session, h, D)
    ex = item.exit_status
    assert ex.thesis_state == "active"
    assert ex.target_price == 110.0 and ex.stop_price == 92.0
    assert ex.days_left == 9
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_holdings_api_thesis.py -v`
Expected: FAIL（DTO 無欄位）

- [ ] **Step 3: 實作**

- `schemas.py` 找到 exit status 的 DTO（`build_item` 塞 `exit_status` 用的 model），加：

```python
    thesis_state: str | None = None
    days_left: int | None = None
    reaudit_count: int | None = None
    target_price: float | None = None
    stop_price: float | None = None
```

- `routes_holdings.py` `build_item`：組 exit_status DTO 處把 `st.thesis_state` 等五欄帶入
  （`ExitStatus` fallback 分支——`shares==0` 那條——五欄留 None，不用改）。
- 設定端點：找到讀寫 `Setting("exit")` 的 route（`grep -rn '"exit"' backend/app/api`）。
  schema 加 `wave_defaults`（三欄皆 optional float/int）、`reaudit_max: int = 2`、
  long 區塊加 `score_slip_warn: float = 15.0`。寫入照原樣存 JSON；`set_config`
  已在 Task 4 支援 `score_slip_warn`。舊 wave 三鍵不再出現在 schema——存量 JSON 留著
  無害（`set_config` 只讀有列出的鍵）。

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_holdings_api_thesis.py -v`；再跑 `python -m pytest tests/ -v` 防退化
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/schemas.py backend/app/api/routes_holdings.py backend/tests/test_holdings_api_thesis.py
git commit -m "feat(exit): API 帶出論點五欄＋設定鍵改版（wave_defaults/reaudit_max/score_slip_warn）"
```

---

### Task 6: 前端——論點進度條、已命中徽章、設定頁改版

**Files:**
- Modify: `frontend/src/api/types.ts`（ExitStatus 型別加五欄）
- Modify: `frontend/src/pages/HoldingsPage.tsx`（波段持股卡：訊號列表 → 論點進度條）
- Modify: `frontend/src/pages/SettingsPage.tsx`（移除波段 stop_cap/trail 三項，加
  wave_defaults 三項＋reaudit_max＋長線 score_slip_warn）
- Test: 無自動測試（沿用專案現況——前端無測試框架），以 build + 瀏覽器驗證代替。

**Interfaces:**
- Consumes: Task 5 的 API 欄位。

- [ ] **Step 1: types.ts 加欄位**

```typescript
// ExitStatus 介面內追加
thesis_state?: 'active' | 'expiring' | 'awaiting_reaudit' | 'fulfilled' | 'expired' | 'refuted' | null
days_left?: number | null
reaudit_count?: number | null
target_price?: number | null
stop_price?: number | null
```

- [ ] **Step 2: HoldingsPage 波段卡改版**

`track === 'wave' && exit_status.thesis_state` 時，隱藏原訊號列表，改渲染論點區塊：

```tsx
{ex.thesis_state && (
  <div className="mt-2 space-y-1">
    <div className="flex justify-between text-xs text-slate-400">
      <span>論點第 {horizon - (ex.days_left ?? 0)}/{horizon} 天
        {(ex.reaudit_count ?? 0) > 0 && `（已重審 ${ex.reaudit_count}/2）`}</span>
      <span>目標 {ex.target_price} ／ 停損 {ex.stop_price}</span>
    </div>
    <div className="h-1.5 rounded bg-slate-700">
      <div className="h-1.5 rounded bg-sky-500"
           style={{ width: `${Math.min(100, (horizon - (ex.days_left ?? 0)) / horizon * 100)}%` }} />
    </div>
    {ex.thesis_state === 'fulfilled' && (
      <span className="inline-block rounded bg-emerald-600/20 px-1.5 py-0.5 text-xs text-emerald-400">
        已命中 +{targetPct}%
      </span>
    )}
  </div>
)}
```

（`horizon` 從 `days_left + 已過天數` 推不出——直接由 `ex` 帶的欄位算：
後端 `days_left = horizon - days_elapsed`，前端另需 horizon；**在 Task 5 的 DTO 再加
`horizon_days: number | null`**，此處一併補上；實作時回頭在 schemas.py 與 build_item
加這一欄。fulfilled 徽章的 targetPct 用 `(target_price / avg_cost - 1) * 100` 四捨五入。）

- [ ] **Step 3: SettingsPage 出場設定改版**

- 移除波段區塊的 stop_cap / trail_trigger / trail_pullback 三個輸入。
- 新增「非策略持股預設」：目標 %（wave_defaults.target_pct）、天期（horizon_days）、
  停損 %（stop_pct）三個數字輸入 ＋「重審上限」（reaudit_max）。
- 長線區塊加「分數滑落警戒（分）」（score_slip_warn）。
- 送出 payload 對齊 Task 5 的設定 schema。

- [ ] **Step 4: build 驗證**

Run: `cd frontend; npm run build`
Expected: 無 TypeScript 錯誤

- [ ] **Step 5: 瀏覽器驗證（launch.json 起 dev server）**

持股頁：波段持股顯示進度條與目標/停損價；設定頁：新欄位可存讀。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/pages/HoldingsPage.tsx frontend/src/pages/SettingsPage.tsx backend/app/api/schemas.py backend/app/api/routes_holdings.py
git commit -m "feat(exit): 前端論點進度條＋已命中徽章＋設定頁改版"
```

---

### Task 7: 通知——到期預告

**Files:**
- Modify: `backend/app/scheduler/steps.py`（NotifyStep：現有 🔴🟠 推播邏輯處）
- Test: `backend/tests/test_notify_expiry.py`（若 NotifyStep 有可測的訊息組裝函式則測之；
  若推播訊息組裝內嵌難拆，抽出 `format_exit_lines(items) -> list[str]` 再測）

**Interfaces:**
- Consumes: ExitStatus（Task 3）。

行為：NotifyStep 收集出場燈號時，波段持股 `thesis_state == "expiring"` 且
`days_left == 1` → 追加一行預告「⏳ {股名} 論點明日到期（第 N/N-1 天未兌現）」。
🔴🟠 推播規則不變（awaiting_reaudit 本身是 🟠 會自然入列）。

- [ ] **Step 1: 讀 `steps.py` 的 NotifyStep**，找到出場提醒訊息組裝點；若為內嵌字串拼接，
  先抽 `format_exit_lines(rows: list[tuple[str, ExitStatus]]) -> list[str]`（純函式）。

- [ ] **Step 2: 寫失敗測試**

```python
# backend/tests/test_notify_expiry.py
from app.engines.exit_engine import ExitStatus
from app.scheduler.steps import format_exit_lines


def _st(**kw):
    base = dict(level="green", light="🟢", signals=[])
    base.update(kw)
    return ExitStatus(**base)


def test_expiring_tomorrow_gets_notice_line():
    rows = [("甲", _st(level="yellow", light="🟡", thesis_state="expiring", days_left=1))]
    lines = format_exit_lines(rows)
    assert any("明日到期" in ln for ln in lines)


def test_expiring_two_days_left_no_notice():
    rows = [("甲", _st(level="yellow", light="🟡", thesis_state="expiring", days_left=2))]
    assert not any("明日到期" in ln for ln in format_exit_lines(rows))
```

- [ ] **Step 3: 跑測試確認失敗 → 實作 → 跑到過**

Run: `python -m pytest tests/test_notify_expiry.py -v`

- [ ] **Step 4: Commit**

```bash
git add backend/app/scheduler/steps.py backend/tests/test_notify_expiry.py
git commit -m "feat(exit): Discord 到期日前一日預告"
```

---

### Task 8: 全量回歸與收尾

- [ ] **Step 1: 後端全測** `python -m pytest tests/ -v` → 全 PASS
- [ ] **Step 2: 前端 build** `cd frontend; npm run build` → 無錯
- [ ] **Step 3: 起 dev server 走一遍**：建一筆波段持股（帶/不帶 strategy_id）→ 持股頁
  看進度條；設定頁改 wave_defaults 存讀。
- [ ] **Step 4: Commit**（若有收尾修正）

---

## 後續（不在本計畫）

- backtest lab「出場規則消融」實驗（純三出口 vs ＋破月線反證）。
- `score_slip_warn` 用歷史分數分佈校準。
- fulfilled 樣本的命中率統計面板。
