# 回測實驗室＋使用者自訂軌 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使用者在策略室自訂篩選條件與回測目標，驗證後掛成進場推薦的第三軌。

**Architecture:** 方案 A——條件存 JSON、後端規則求值器共用於「歷史回測」與「當日清單」兩條路徑，即時計算＋(策略,條件雜湊,日期) 記憶體快取，不動盤後 pipeline。Spec：`docs/superpowers/specs/2026-08-20-backtest-lab-design.md`。

**Tech Stack:** FastAPI + SQLAlchemy(SQLite) + pydantic；React + TanStack Query + Tailwind。

## Global Constraints

- 回測口徑與波段軌 KPI 定版一致：訊號日收盤符合條件 → 隔日高進場錨 → `horizon_days` 內最高價碰 `+target_pct%` 算命中；停損先碰算失敗，同日皆碰保守記失敗。
- 使用者資料查詢只准經過 `UserData`（storage/user_data.py），route 層不得裸查 `user_strategies`。
- 查無資料回 404 不回 403（不洩漏 ID 存在性）。
- 新 API 全部掛在 `prefix="/lab/strategies"` 的新 router（spec ⑤ 的「routes_lab.py 補 prefix」會改既有前端路徑，**本計畫刻意不動**，另開 task 處理——見「非本計畫範圍」）。
- 回測範圍上限 12 個月；基率對照組以每 5 個訊號日抽樣一次計算（控制同步端點延遲）。
- 註解密度、命名跟隨現有檔案（中文 docstring、繁中註解）。

**非本計畫範圍**：routes_lab.py 既有端點加 prefix（牽動 client.ts 多處路徑，獨立小 PR 處理）；Free/Pro gating。

---

### Task 1: UserStrategy 資料模型＋UserData scoped CRUD

**Files:**
- Modify: `backend/app/storage/models.py`（User class 之後、Holding 之前附近，約 :580）
- Modify: `backend/app/storage/user_data.py`（檔尾加 strategies 區段）
- Test: `backend/tests/test_user_strategies.py`

**Interfaces:**
- Produces: `models.UserStrategy`（欄位見下）；`UserData.strategies() -> list[UserStrategy]`、`UserData.strategy(sid: int) -> UserStrategy | None`、`UserData.create_strategy(**fields) -> UserStrategy`、`UserData.set_active_strategy(sid: int) -> UserStrategy | None`（同 user 其他策略全關）、`UserData.active_strategy() -> UserStrategy | None`、`UserData.delete_strategy(sid: int) -> bool`

- [ ] **Step 1: Write the failing test**

```python
"""backend/tests/test_user_strategies.py — ownership 與 is_active 單一性。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.storage import models
from app.storage.user_data import UserData


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    for i, email in enumerate(["a@t.twa", "b@t.twa"], start=1):
        s.add(models.User(id=i, email=email, password_hash="x"))
    s.commit()
    yield s
    s.close()


def _mk(ud: UserData, name: str = "測試策略") -> models.UserStrategy:
    return ud.create_strategy(
        name=name,
        conditions=[{"field": "close", "op": "gt", "value": 100}],
        sort_field="turnover", sort_desc=True, top_n=30,
        target_pct=10.0, horizon_days=10, stop_pct=None,
    )


def test_ownership_isolation(session):
    ua, ub = UserData(session, 1), UserData(session, 2)
    st = _mk(ua)
    session.commit()
    assert ub.strategy(st.id) is None          # B 讀不到 A 的
    assert ua.strategy(st.id) is not None
    assert ub.delete_strategy(st.id) is False  # B 刪不掉 A 的
    assert ua.strategies() and not ub.strategies()


def test_set_active_exclusive(session):
    ua = UserData(session, 1)
    s1, s2 = _mk(ua, "一"), _mk(ua, "二")
    session.commit()
    ua.set_active_strategy(s1.id)
    ua.set_active_strategy(s2.id)
    session.commit()
    active = ua.active_strategy()
    assert active is not None and active.id == s2.id
    assert ua.strategy(s1.id).is_active is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_user_strategies.py -v`
Expected: FAIL（`UserStrategy` / `create_strategy` 不存在）

- [ ] **Step 3: 加 model 與 UserData 方法**

models.py（放在 User class 定義之後）：

```python
class UserStrategy(Base):
    """回測實驗室的使用者自訂策略（spec 2026-08-20-backtest-lab）。

    conditions＝AND 條件清單 JSON；is_active＝掛成進場推薦第三軌
    （同一 user 至多一個 true，由 UserData.set_active_strategy 保證，
    不靠 DB 約束——SQLite partial unique index 對既有庫遷移不友善）。
    """
    __tablename__ = "user_strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(30), default="我的策略")
    conditions: Mapped[list] = mapped_column(JSON, default=list)
    sort_field: Mapped[str] = mapped_column(String(30), default="turnover")
    sort_desc: Mapped[bool] = mapped_column(Boolean, default=True)
    top_n: Mapped[int] = mapped_column(Integer, default=30)
    target_pct: Mapped[float] = mapped_column(Float, default=10.0)
    horizon_days: Mapped[int] = mapped_column(Integer, default=10)
    stop_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(),
                                                 onupdate=func.now())
```

（`JSON` 需加進 models.py 既有的 sqlalchemy import 列；該檔已 import `Integer, String, Float, Boolean, DateTime, ForeignKey, func`。）

user_data.py 檔尾：

```python
    # ── strategies（回測實驗室）─────────────────────────

    def strategies(self) -> list[models.UserStrategy]:
        return list(self.session.execute(
            select(models.UserStrategy)
            .where(models.UserStrategy.user_id == self.user_id)
            .order_by(models.UserStrategy.id)
        ).scalars().all())

    def strategy(self, sid: int) -> models.UserStrategy | None:
        return self.session.execute(
            select(models.UserStrategy).where(
                models.UserStrategy.id == sid,
                models.UserStrategy.user_id == self.user_id)
        ).scalars().first()

    def active_strategy(self) -> models.UserStrategy | None:
        return self.session.execute(
            select(models.UserStrategy).where(
                models.UserStrategy.user_id == self.user_id,
                models.UserStrategy.is_active == True)  # noqa: E712
        ).scalars().first()

    def create_strategy(self, **fields) -> models.UserStrategy:
        st = models.UserStrategy(user_id=self.user_id, **fields)
        self.session.add(st)
        self.session.flush()  # 讓呼叫端立刻拿到 id
        return st

    def set_active_strategy(self, sid: int) -> models.UserStrategy | None:
        """啟用 sid、同 user 其他全關。回 None＝非本人策略。"""
        target = self.strategy(sid)
        if target is None:
            return None
        for st in self.strategies():
            st.is_active = st.id == sid
        return target

    def delete_strategy(self, sid: int) -> bool:
        st = self.strategy(sid)
        if st is None:
            return False
        self.session.delete(st)
        return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_user_strategies.py -v`
Expected: PASS ×2

- [ ] **Step 5: Commit**

```bash
git add backend/app/storage/models.py backend/app/storage/user_data.py backend/tests/test_user_strategies.py
git commit -m "feat(lab): UserStrategy 模型＋UserData scoped CRUD（回測實驗室 task1）"
```

---

### Task 2: 欄位註冊表＋規則求值器

**Files:**
- Create: `backend/app/services/strategy_engine.py`
- Test: `backend/tests/test_strategy_engine.py`

**Interfaces:**
- Consumes: `models.DailyPrice / Indicator / InstitutionalFlow(法人表，實作時以 models.py :119 附近實名為準) / ShareholderConcentration(:149 附近) / MonthlyRevenue(:212 附近)`——**實作第一步先打開 models.py 核對這三張表的 class 名與欄名**，下方程式以查到的實名代入。
- Produces:
  - `FIELD_REGISTRY: dict[str, Field]`，`Field = (label, group, unit, loader)`；`loader(session, dates: list[date]) -> dict[tuple[str, date], float]`
  - `registry_meta() -> list[dict]`（給 API：key/label/group/unit）
  - `evaluate(session, conditions: list[dict], dates: list[date]) -> dict[date, list[str]]`（每個日期→符合全部條件的股號清單，未排序）
  - `trading_dates(session, start: date, end: date) -> list[date]`（MarketIndex 有收盤的日）

- [ ] **Step 1: Write the failing test**

```python
"""backend/tests/test_strategy_engine.py — 求值器：op 語意與 null 處理。"""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services import strategy_engine as se
from app.storage import models

D1, D2 = date(2026, 1, 5), date(2026, 1, 6)


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    # 兩檔股票兩天：A 收盤 105/110、B 收盤 95/None
    s.add_all([
        models.Stock(id="1101", name="甲", is_etf=False),
        models.Stock(id="2202", name="乙", is_etf=False),
        models.DailyPrice(stock_id="1101", date=D1, close=105, high=106, low=104,
                          open=105, volume=1000, turnover=5e8),
        models.DailyPrice(stock_id="1101", date=D2, close=110, high=111, low=109,
                          open=110, volume=1000, turnover=6e8),
        models.DailyPrice(stock_id="2202", date=D1, close=95, high=96, low=94,
                          open=95, volume=1000, turnover=1e8),
        models.DailyPrice(stock_id="2202", date=D2, close=None, high=None, low=None,
                          open=None, volume=None, turnover=None),
    ])
    s.commit()
    yield s
    s.close()


def test_gt_and_null_excluded(session):
    out = se.evaluate(session, [{"field": "close", "op": "gt", "value": 100}], [D1, D2])
    assert out[D1] == ["1101"]
    assert out[D2] == ["1101"]  # B 的 None 不符合任何條件


def test_and_semantics(session):
    conds = [{"field": "close", "op": "gt", "value": 100},
             {"field": "turnover", "op": "gte", "value": 6e8}]
    out = se.evaluate(session, conds, [D1, D2])
    assert out[D1] == [] and out[D2] == ["1101"]


def test_streak_op(session):
    # 連 2 日 close > 100：D2 的 A 成立（105,110），D1 不成立（只有一天）
    conds = [{"field": "close", "op": "streak_gt", "value": {"n": 2, "threshold": 100}}]
    out = se.evaluate(session, conds, [D1, D2])
    assert out[D1] == [] and out[D2] == ["1101"]


def test_unknown_field_rejected(session):
    with pytest.raises(ValueError):
        se.evaluate(session, [{"field": "nope", "op": "gt", "value": 1}], [D1])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_strategy_engine.py -v`
Expected: FAIL（模組不存在）

- [ ] **Step 3: 實作 strategy_engine.py（註冊表＋求值器）**

```python
"""回測實驗室的規則引擎（spec 2026-08-20-backtest-lab ②③）。

兩條路徑共用：歷史回測逐日掃、推薦頁自訂軌當日清單。
欄位註冊表是前端下拉的唯一真相——新增欄位只改這裡。

loader 契約：loader(session, dates) -> {(stock_id, date): float}
  - 日頻表直接撈；週/月頻（大戶占比、營收）forward-fill 到查詢日；
  - 衍生欄（如 收盤/MA20-1）在 loader 內算好，求值器一律拿現成數字比大小。
streak op 需要往前 n-1 個交易日的歷史，evaluate 會自動擴大載入範圍。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..storage import models

Series = dict[tuple[str, date], float]
Loader = Callable[[Session, list[date]], Series]


@dataclass(frozen=True)
class Field:
    label: str
    group: str   # 技術 / 籌碼 / 基本面 / 市場
    unit: str
    loader: Loader


def _daily(col_map: Callable[[], tuple]) -> Loader:
    """日頻直取：col_map() 回 (model欄位, 取值函式)。"""
    def load(session: Session, dates: list[date]) -> Series:
        model_cols, fn = col_map()
        rows = session.execute(
            select(*model_cols).where(model_cols[1].in_(dates))
        ).all()
        out: Series = {}
        for r in rows:
            v = fn(r)
            if v is not None:
                out[(r[0], r[1])] = float(v)
        return out
    return load


def _price(attr: str) -> Loader:
    def cols():
        c = getattr(models.DailyPrice, attr)
        return ((models.DailyPrice.stock_id, models.DailyPrice.date, c),
                lambda r: r[2])
    return _daily(cols)


def _indicator_gap(ma_attr: str) -> Loader:
    """收盤/MA - 1（%）。close 與 MA 任一缺就略過該筆。"""
    def load(session: Session, dates: list[date]) -> Series:
        ma = getattr(models.Indicator, ma_attr)
        rows = session.execute(
            select(models.Indicator.stock_id, models.Indicator.date, ma,
                   models.DailyPrice.close)
            .join(models.DailyPrice,
                  (models.DailyPrice.stock_id == models.Indicator.stock_id)
                  & (models.DailyPrice.date == models.Indicator.date))
            .where(models.Indicator.date.in_(dates))
        ).all()
        return {(sid, d): (cl / m - 1.0) * 100
                for sid, d, m, cl in rows if m and cl}
    return load


def _indicator(attr: str) -> Loader:
    def load(session: Session, dates: list[date]) -> Series:
        col = getattr(models.Indicator, attr)
        rows = session.execute(
            select(models.Indicator.stock_id, models.Indicator.date, col)
            .where(models.Indicator.date.in_(dates))
        ).all()
        return {(sid, d): float(v) for sid, d, v in rows if v is not None}
    return load


def _atr_pct(session: Session, dates: list[date]) -> Series:
    rows = session.execute(
        select(models.Indicator.stock_id, models.Indicator.date,
               models.Indicator.atr14, models.DailyPrice.close)
        .join(models.DailyPrice,
              (models.DailyPrice.stock_id == models.Indicator.stock_id)
              & (models.DailyPrice.date == models.Indicator.date))
        .where(models.Indicator.date.in_(dates))
    ).all()
    return {(sid, d): atr / cl * 100 for sid, d, atr, cl in rows if atr and cl}


def _inst_roll(attr: str, n: int) -> Loader:
    """法人 n 日累計（張）。往前多載 n-1 個交易日再滾動加總。"""
    def load(session: Session, dates: list[date]) -> Series:
        since = min(dates) - timedelta(days=n * 3)  # 交易日緩衝，粗放無妨
        col = getattr(models.InstitutionalFlow, attr)
        rows = session.execute(
            select(models.InstitutionalFlow.stock_id, models.InstitutionalFlow.date, col)
            .where(models.InstitutionalFlow.date >= since,
                   models.InstitutionalFlow.date <= max(dates))
            .order_by(models.InstitutionalFlow.date)
        ).all()
        by_sid: dict[str, list[tuple[date, float]]] = {}
        for sid, d, v in rows:
            if v is not None:
                by_sid.setdefault(sid, []).append((d, float(v)))
        want = set(dates)
        out: Series = {}
        for sid, seq in by_sid.items():
            for i, (d, _) in enumerate(seq):
                if d in want and i + 1 >= n:
                    out[(sid, d)] = sum(v for _, v in seq[i - n + 1: i + 1])
        return out
    return load


def _ffill(model, value_attr: str, date_expr: Callable) -> Loader:
    """週/月頻資料 forward-fill：查詢日取「≤該日最近一筆」。

    date_expr(row) -> date：月營收沒有 date 欄，用 (year, month) 折算成
    次月 10 日（公告時點的保守近似，避免前視）。
    """
    def load(session: Session, dates: list[date]) -> Series:
        rows = [r for r in session.execute(select(model)).scalars().all()
                if getattr(r, value_attr) is not None]
        by_sid: dict[str, list[tuple[date, float]]] = {}
        for r in rows:
            by_sid.setdefault(r.stock_id, []).append(
                (date_expr(r), float(getattr(r, value_attr))))
        out: Series = {}
        for sid, seq in by_sid.items():
            seq.sort()
            for d in dates:
                latest = None
                for ed, v in seq:
                    if ed <= d:
                        latest = v
                    else:
                        break
                if latest is not None:
                    out[(sid, d)] = latest
        return out
    return load


def _rev_date(r) -> date:
    y, m = (r.year, r.month + 1) if r.month < 12 else (r.year + 1, 1)
    return date(y, m, 10)


FIELD_REGISTRY: dict[str, Field] = {
    # 技術
    "close":        Field("收盤價", "技術", "元", _price("close")),
    "turnover":     Field("成交金額", "技術", "元", _price("turnover")),
    "ma20_gap":     Field("收盤/月線乖離", "技術", "%", _indicator_gap("ma20")),
    "ma60_gap":     Field("收盤/季線乖離", "技術", "%", _indicator_gap("ma60")),
    "bias_20":      Field("20日乖離", "技術", "%", _indicator("bias_20")),
    "bias_60":      Field("60日乖離", "技術", "%", _indicator("bias_60")),
    "kd_k":         Field("KD K值", "技術", "", _indicator("kd_k")),
    "atr_pct":      Field("波動度 ATR", "技術", "%", _atr_pct),
    # 籌碼（class 名以 models.py 實名為準）
    "foreign_net_5": Field("外資5日累計買超", "籌碼", "張", _inst_roll("foreign_net", 5)),
    "trust_net_5":   Field("投信5日累計買超", "籌碼", "張", _inst_roll("trust_net", 5)),
    "foreign_net_1": Field("外資當日買超", "籌碼", "張", _inst_roll("foreign_net", 1)),
    "trust_net_1":   Field("投信當日買超", "籌碼", "張", _inst_roll("trust_net", 1)),
    "big_pct": Field("大戶占比", "籌碼", "%",
                     _ffill(models.ShareholderConcentration, "big_pct",
                            lambda r: r.date)),
    "margin_chg": Field("融資增減", "籌碼", "張", _table_col_placeholder),  # ← 見下註
    "sbl_chg":    Field("借券餘額變化", "籌碼", "張", _table_col_placeholder),  # ← 見下註
    # 基本面
    "rev_yoy": Field("月營收YoY", "基本面", "%",
                     _ffill(models.MonthlyRevenue, "yoy", _rev_date)),
    "rev_mom": Field("月營收MoM", "基本面", "%",
                     _ffill(models.MonthlyRevenue, "mom", _rev_date)),
}
```

註：`margin_chg`／`sbl_chg` 的 loader 比照 `_indicator` 寫一個泛化版
`_table_col(model, attr)`（select stock_id/date/attr、過濾 None），分別掛
融資表 `margin_change` 與借券表 `sbl_change`（class 名以 models.py :132／:167
實名為準）；上面兩行的 `None` 佔位在實作時換成 `_table_col(...)`。
spec 欄位清單中的「當沖占比」（需跨表除法）與「大盤 bias」（市場級欄位，
語意是全域開關而非個股比較）**v1 緩列**，記在 spec 的非目標補充。

```python

_OPS = {"gt", "lt", "gte", "lte", "streak_gt", "streak_lt"}
_CMP = {"gt": lambda a, b: a > b, "lt": lambda a, b: a < b,
        "gte": lambda a, b: a >= b, "lte": lambda a, b: a <= b}


def registry_meta() -> list[dict]:
    return [{"key": k, "label": f.label, "group": f.group, "unit": f.unit}
            for k, f in FIELD_REGISTRY.items()]


def trading_dates(session: Session, start: date, end: date) -> list[date]:
    rows = session.execute(
        select(models.MarketIndex.date)
        .where(models.MarketIndex.date >= start, models.MarketIndex.date <= end)
        .order_by(models.MarketIndex.date)
    ).scalars().all()
    return list(rows)


def _validate(conditions: list[dict]) -> None:
    for c in conditions:
        if c.get("field") not in FIELD_REGISTRY:
            raise ValueError(f"未知欄位：{c.get('field')}")
        if c.get("op") not in _OPS:
            raise ValueError(f"未知運算子：{c.get('op')}")
        if c["op"].startswith("streak"):
            v = c.get("value")
            if not isinstance(v, dict) or "n" not in v or "threshold" not in v:
                raise ValueError("streak op 的 value 需為 {n, threshold}")


def evaluate(session: Session, conditions: list[dict],
             dates: list[date]) -> dict[date, list[str]]:
    """AND 求值。streak op 自動往前擴載 n-1 個資料日（以 DailyPrice 日曆近似）。"""
    _validate(conditions)
    if not conditions or not dates:
        return {d: [] for d in dates}

    max_streak = max((c["value"]["n"] for c in conditions
                      if c["op"].startswith("streak")), default=1)
    load_dates = dates
    if max_streak > 1:
        # 擴大載入視窗：取 dates 之前的交易日補足 streak 歷史
        earliest = min(dates)
        prior = session.execute(
            select(models.DailyPrice.date).distinct()
            .where(models.DailyPrice.date < earliest)
            .order_by(models.DailyPrice.date.desc()).limit(max_streak - 1)
        ).scalars().all()
        load_dates = sorted(set(dates) | set(prior))

    series = {f: FIELD_REGISTRY[f].loader(session, load_dates)
              for f in {c["field"] for c in conditions}}  # 每欄只載一次

    ordered = sorted(load_dates)
    idx = {d: i for i, d in enumerate(ordered)}
    out: dict[date, list[str]] = {}
    universe = {sid for s in series.values() for (sid, _) in s}
    for d in dates:
        hit: list[str] = []
        for sid in universe:
            ok = True
            for c in conditions:
                s = series[c["field"]]
                if c["op"] in _CMP:
                    v = s.get((sid, d))
                    if v is None or not _CMP[c["op"]](v, float(c["value"])):
                        ok = False
                        break
                else:  # streak_gt / streak_lt
                    n, th = c["value"]["n"], float(c["value"]["threshold"])
                    cmp = _CMP["gt" if c["op"] == "streak_gt" else "lt"]
                    i = idx[d]
                    if i + 1 < n:
                        ok = False
                        break
                    window = [s.get((sid, ordered[j])) for j in range(i - n + 1, i + 1)]
                    if any(v is None or not cmp(v, th) for v in window):
                        ok = False
                        break
            if ok:
                hit.append(sid)
        out[d] = sorted(hit)
    return out
```

（測試用 in-memory DB 沒有 MarketIndex 資料，`trading_dates` 在 Task 3 的回測測試才會用到並種資料。）

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_strategy_engine.py -v`
Expected: PASS ×4

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/strategy_engine.py backend/tests/test_strategy_engine.py
git commit -m "feat(lab): 欄位註冊表＋規則求值器（回測實驗室 task2）"
```

---

### Task 3: 回測引擎（run_backtest）

**Files:**
- Modify: `backend/app/services/strategy_engine.py`（檔尾加回測區段）
- Test: `backend/tests/test_strategy_engine.py`（追加）

**Interfaces:**
- Consumes: `evaluate`、`trading_dates`（Task 2）
- Produces: `run_backtest(session, conditions, sort_field, sort_desc, top_n, target_pct, horizon_days, stop_pct, start, end) -> BacktestResult`
  - `BacktestResult`（dataclass）：`samples:int, hits:int, hit_rate:float|None, base_rate:float|None, lift:float|None, avg_max_drawdown:float|None, monthly:list[dict(month,samples,hits)], recent:list[dict(date,stock_id,name,entry,hit,stopped,max_gain_pct,max_dd_pct)], warn_loose:bool, signal_days:int`

- [ ] **Step 1: Write the failing test（口徑三案例：命中／停損先碰同日保守記失敗／未達標）**

```python
# 追加到 backend/tests/test_strategy_engine.py

def _seed_prices(s, sid: str, base: date, closes, highs, lows):
    """從 base 起連續交易日種價格（同步種 MarketIndex 當交易日曆）。"""
    from datetime import timedelta
    d = base
    i = 0
    while i < len(closes):
        if d.weekday() < 5:  # 平日當交易日
            s.merge(models.DailyPrice(stock_id=sid, date=d, close=closes[i],
                                      high=highs[i], low=lows[i], open=closes[i],
                                      volume=1000, turnover=1e8 * closes[i]))
            s.merge(models.MarketIndex(date=d, close=20000))
            i += 1
        d += timedelta(days=1)


def test_backtest_hit_stop_and_miss(session):
    from app.services.strategy_engine import run_backtest
    base = date(2026, 2, 2)  # 週一
    # 訊號日 close=200 觸發條件；隔日 high=210 是進場錨。
    # hitcase：第 4 根 high 231 ≥ 210*1.10=231 → 命中
    _seed_prices(session, "1101", base,
                 closes=[200, 205, 210, 220, 231, 230],
                 highs=[201, 210, 215, 225, 231, 232],
                 lows=[199, 204, 209, 219, 224, 229])
    # stopcase：進場錨 110；同一日 high 121(=110*1.10) 且 low 99(=110*0.9)
    # → 同日皆碰，保守記失敗（stop 優先）
    _seed_prices(session, "2202", base,
                 closes=[100, 105, 110, 100, 100, 100],
                 highs=[101, 110, 121, 101, 101, 101],
                 lows=[99, 104, 99, 95, 95, 95])
    session.commit()

    r = run_backtest(
        session,
        conditions=[{"field": "close", "op": "gte", "value": 100}],
        sort_field="turnover", sort_desc=True, top_n=10,
        target_pct=10.0, horizon_days=5, stop_pct=10.0,
        start=base, end=base,  # 只有一個訊號日
    )
    assert r.samples == 2
    assert r.hits == 1              # 1101 命中；2202 同日雙碰記失敗
    assert r.hit_rate == 0.5
    assert r.signal_days == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_strategy_engine.py::test_backtest_hit_stop_and_miss -v`
Expected: FAIL（run_backtest 不存在）

- [ ] **Step 3: 實作 run_backtest**

```python
# 追加到 strategy_engine.py

@dataclass
class BacktestResult:
    samples: int
    hits: int
    hit_rate: float | None
    base_rate: float | None
    lift: float | None
    avg_max_drawdown: float | None
    monthly: list[dict]
    recent: list[dict]
    warn_loose: bool
    signal_days: int


def _load_bars(session: Session, sids: set[str], start: date, end: date):
    """{sid: (dates升冪, [(high, low)])}。end 之後多抓 horizon 由呼叫端控制。"""
    rows = session.execute(
        select(models.DailyPrice.stock_id, models.DailyPrice.date,
               models.DailyPrice.high, models.DailyPrice.low)
        .where(models.DailyPrice.stock_id.in_(sids),
               models.DailyPrice.date >= start, models.DailyPrice.date <= end)
        .order_by(models.DailyPrice.date)
    ).all()
    px: dict[str, tuple[list[date], list[tuple]]] = {}
    for sid, d, hi, lo in rows:
        dates, bars = px.setdefault(sid, ([], []))
        dates.append(d)
        bars.append((hi, lo))
    return px


def _judge(dates: list[date], bars: list[tuple], signal: date,
           target_pct: float, horizon: int, stop_pct: float | None):
    """單一樣本：回 (entry, hit, stopped, max_gain, max_dd) 或 None（無隔日資料）。

    進場錨＝訊號隔一交易日的 high；停損先碰記失敗、同日皆碰保守記失敗。
    """
    try:
        i0 = dates.index(signal)
    except ValueError:
        return None
    if i0 + 1 >= len(dates) or bars[i0 + 1][0] is None:
        return None
    entry = bars[i0 + 1][0]
    tgt = entry * (1 + target_pct / 100)
    stp = entry * (1 - stop_pct / 100) if stop_pct is not None else None
    hit = stopped = False
    max_gain = max_dd = 0.0
    for j in range(i0 + 1, min(i0 + 1 + horizon, len(dates))):
        hi, lo = bars[j]
        if hi is None or lo is None:
            continue
        max_gain = max(max_gain, (hi / entry - 1) * 100)
        max_dd = min(max_dd, (lo / entry - 1) * 100)
        if stp is not None and lo <= stp:
            stopped = True   # 同日 hi 也達標時保守記失敗 → 先判停損
            break
        if hi >= tgt:
            hit = True
            break
    return entry, hit, stopped, round(max_gain, 2), round(max_dd, 2)


def run_backtest(session: Session, conditions: list[dict], sort_field: str,
                 sort_desc: bool, top_n: int, target_pct: float,
                 horizon_days: int, stop_pct: float | None,
                 start: date, end: date) -> BacktestResult:
    sig_dates = trading_dates(session, start, end)
    per_day = evaluate(session, conditions, sig_dates)

    # 排序值：沿用註冊表 loader（排序欄不一定在條件裡）
    sort_series = FIELD_REGISTRY[sort_field].loader(session, sig_dates)
    picks: list[tuple[date, str]] = []
    warn_loose = False
    for d in sig_dates:
        cands = per_day.get(d, [])
        if len(cands) > 200:
            warn_loose = True
        cands = sorted(cands, key=lambda sid: sort_series.get((sid, d), float("-inf")),
                       reverse=sort_desc)[:top_n]
        picks.extend((d, sid) for sid in cands)

    sids = {sid for _, sid in picks}
    horizon_pad = timedelta(days=horizon_days * 2 + 14)
    px = _load_bars(session, sids, start, end + horizon_pad)

    names = dict(session.execute(
        select(models.Stock.id, models.Stock.name).where(models.Stock.id.in_(sids))
    ).all()) if sids else {}

    hits = 0
    dds: list[float] = []
    monthly: dict[str, list[int]] = {}
    details: list[dict] = []
    for d, sid in picks:
        if sid not in px:
            continue
        judged = _judge(*px[sid], d, target_pct, horizon_days, stop_pct)
        if judged is None:
            continue
        entry, hit, stopped, mg, mdd = judged
        hits += int(hit)
        dds.append(mdd)
        m = d.strftime("%Y-%m")
        monthly.setdefault(m, [0, 0])
        monthly[m][0] += 1
        monthly[m][1] += int(hit)
        details.append({"date": d.isoformat(), "stock_id": sid,
                        "name": names.get(sid, sid), "entry": entry, "hit": hit,
                        "stopped": stopped, "max_gain_pct": mg, "max_dd_pct": mdd})
    samples = len(dds)

    # 基率對照：每 5 個訊號日抽 1 日、全市場同口徑（控制同步延遲）
    base_rate = None
    base_days = sig_dates[::5]
    if base_days:
        all_sids = set(session.execute(
            select(models.DailyPrice.stock_id).distinct()
            .where(models.DailyPrice.date.in_(base_days))
        ).scalars().all())
        bpx = _load_bars(session, all_sids, start, end + horizon_pad)
        bn = bh = 0
        for d in base_days:
            for sid, (dts, bars) in bpx.items():
                j = _judge(dts, bars, d, target_pct, horizon_days, stop_pct)
                if j is not None:
                    bn += 1
                    bh += int(j[1])
        base_rate = round(bh / bn, 3) if bn else None

    hit_rate = round(hits / samples, 3) if samples else None
    return BacktestResult(
        samples=samples, hits=hits, hit_rate=hit_rate, base_rate=base_rate,
        lift=(round(hit_rate / base_rate, 2)
              if hit_rate is not None and base_rate else None),
        avg_max_drawdown=round(sum(dds) / len(dds), 2) if dds else None,
        monthly=[{"month": m, "samples": v[0], "hits": v[1]}
                 for m, v in sorted(monthly.items())],
        recent=details[-60:],
        warn_loose=warn_loose, signal_days=len(sig_dates),
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd backend && python -m pytest tests/test_strategy_engine.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/strategy_engine.py backend/tests/test_strategy_engine.py
git commit -m "feat(lab): 回測引擎——隔日高錨/停損保守判/基率抽樣對照（task3）"
```

---

### Task 4: API 路由＋schema＋當日清單快取

**Files:**
- Create: `backend/app/api/routes_strategies.py`
- Modify: `backend/app/api/schemas.py`（檔尾追加）
- Modify: `backend/app/main.py`（include_router 區）
- Test: `backend/tests/test_strategies_api.py`

**Interfaces:**
- Consumes: Task 1 的 `UserData.*`、Task 2/3 的 `registry_meta / evaluate / trading_dates / run_backtest`、deps 的 `get_user_data / get_user_data_write / get_session`
- Produces（全部掛 `prefix="/lab/strategies"`，即 `/api/lab/strategies/...`）：
  - `GET  /fields` → `list[FieldInfo]`
  - `GET  /` → `list[StrategyDTO]`；`POST /` (StrategyCreate) → StrategyDTO
  - `PATCH /{sid}` (StrategyPatch) → StrategyDTO；`DELETE /{sid}` → {ok}
  - `POST /{sid}/activate` → StrategyDTO；`POST /deactivate` → {ok}
  - `POST /{sid}/backtest` (BacktestRequest: start,end) → BacktestResponse
  - `GET  /active/daily` → StrategyDailyResponse（無啟用策略→ `{"strategy": null, ...}`）

- [ ] **Step 1: schemas.py 檔尾追加**

```python
# ── 回測實驗室（spec 2026-08-20-backtest-lab）──


class FieldInfo(BaseModel):
    key: str
    label: str
    group: str
    unit: str


class ConditionDTO(BaseModel):
    field: str
    op: Literal["gt", "lt", "gte", "lte", "streak_gt", "streak_lt"]
    value: float | dict  # streak op 用 {n, threshold}


class StrategyDTO(BaseModel):
    id: int
    name: str
    conditions: list[ConditionDTO]
    sort_field: str
    sort_desc: bool
    top_n: int
    target_pct: float
    horizon_days: int
    stop_pct: float | None
    is_active: bool


class StrategyCreate(BaseModel):
    name: str = "我的策略"
    conditions: list[ConditionDTO] = []
    sort_field: str = "turnover"
    sort_desc: bool = True
    top_n: int = 30
    target_pct: float = 10.0
    horizon_days: int = 10
    stop_pct: float | None = None


class StrategyPatch(BaseModel):
    name: str | None = None
    conditions: list[ConditionDTO] | None = None
    sort_field: str | None = None
    sort_desc: bool | None = None
    top_n: int | None = None
    target_pct: float | None = None
    horizon_days: int | None = None
    stop_pct: float | None = None
    clear_stop: bool = False  # PATCH 語意下 null 無法表達「清掉停損」，用旗標


class BacktestRequest(BaseModel):
    start: date
    end: date


class BacktestMonthly(BaseModel):
    month: str
    samples: int
    hits: int


class BacktestDetail(BaseModel):
    date: str
    stock_id: str
    name: str
    entry: float
    hit: bool
    stopped: bool
    max_gain_pct: float
    max_dd_pct: float


class BacktestResponse(BaseModel):
    samples: int
    hits: int
    hit_rate: float | None
    base_rate: float | None
    lift: float | None
    avg_max_drawdown: float | None
    monthly: list[BacktestMonthly]
    recent: list[BacktestDetail]
    warn_loose: bool
    signal_days: int


class StrategyDailyItem(BaseModel):
    stock_id: str
    name: str
    close: float | None
    sort_value: float | None


class StrategyDailyResponse(BaseModel):
    strategy: StrategyDTO | None
    date: str | None
    items: list[StrategyDailyItem]
```

- [ ] **Step 2: Write the failing API test**

```python
"""backend/tests/test_strategies_api.py — CRUD/啟用/404 語意。

沿用 tests 既有的 app client 慣例（參考 test_multiuser.py 的 fixture 寫法，
實作時先讀該檔，用同一套 TestClient＋登入方式）。核心斷言：
"""
# 1) POST / 建策略 → 200，GET / 看得到
# 2) 用戶 B PATCH 用戶 A 的策略 → 404（不是 403）
# 3) POST /{sid}/activate 後 GET /active/daily 的 strategy.id == sid
# 4) POST /{sid}/backtest body {start,end} 範圍 > 366 天 → 422 或 400
# 5) GET /fields 至少含 close/turnover/rev_yoy 三鍵
```

（此測試檔的 fixture 依 test_multiuser.py 現況照搬，斷言如上五條，實作者補完整程式——五條都要真的寫出來，不得省略。）

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_strategies_api.py -v`
Expected: FAIL（路由不存在 404）

- [ ] **Step 4: 實作 routes_strategies.py**

```python
"""回測實驗室 API（spec 2026-08-20-backtest-lab ③④⑤）。

所有使用者資料經 UserData；查無回 404。當日清單以
(策略id, 條件雜湊, 日期) 進程內快取——條件一改雜湊即變，天然失效。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..services import strategy_engine as se
from ..storage import models
from ..storage.user_data import UserData
from . import schemas
from .deps import get_session, get_user_data, get_user_data_write

router = APIRouter(prefix="/lab/strategies", tags=["strategies"])

_daily_cache: dict[tuple[int, str, str], schemas.StrategyDailyResponse] = {}


def _dto(st: models.UserStrategy) -> schemas.StrategyDTO:
    return schemas.StrategyDTO(
        id=st.id, name=st.name, conditions=st.conditions or [],
        sort_field=st.sort_field, sort_desc=st.sort_desc, top_n=st.top_n,
        target_pct=st.target_pct, horizon_days=st.horizon_days,
        stop_pct=st.stop_pct, is_active=st.is_active)


def _cond_hash(st: models.UserStrategy) -> str:
    raw = json.dumps([st.conditions, st.sort_field, st.sort_desc, st.top_n],
                     sort_keys=True, default=str)
    return hashlib.md5(raw.encode()).hexdigest()[:12]


@router.get("/fields", response_model=list[schemas.FieldInfo])
def fields() -> list[schemas.FieldInfo]:
    return [schemas.FieldInfo(**m) for m in se.registry_meta()]


@router.get("/", response_model=list[schemas.StrategyDTO])
def list_strategies(ud: UserData = Depends(get_user_data)):
    return [_dto(s) for s in ud.strategies()]


@router.post("/", response_model=schemas.StrategyDTO)
def create_strategy(body: schemas.StrategyCreate,
                    ud: UserData = Depends(get_user_data_write)):
    if len(ud.strategies()) >= 20:
        raise HTTPException(400, "策略數量已達上限（20）")
    st = ud.create_strategy(**body.model_dump())
    st.conditions = [c.model_dump() for c in body.conditions]
    return _dto(st)


@router.patch("/{sid}", response_model=schemas.StrategyDTO)
def patch_strategy(sid: int, body: schemas.StrategyPatch,
                   ud: UserData = Depends(get_user_data_write)):
    st = ud.strategy(sid)
    if st is None:
        raise HTTPException(404, "not found")
    data = body.model_dump(exclude_unset=True, exclude={"clear_stop"})
    if "conditions" in data:
        data["conditions"] = [dict(c) for c in data["conditions"]]
        se._validate(data["conditions"])  # 寫入前擋掉未知欄位/op（ValueError→由全域 handler 轉 422，或包 try 轉 400）
    for k, v in data.items():
        setattr(st, k, v)
    if body.clear_stop:
        st.stop_pct = None
    return _dto(st)


@router.delete("/{sid}")
def delete_strategy(sid: int, ud: UserData = Depends(get_user_data_write)):
    if not ud.delete_strategy(sid):
        raise HTTPException(404, "not found")
    return {"ok": True}


@router.post("/{sid}/activate", response_model=schemas.StrategyDTO)
def activate(sid: int, ud: UserData = Depends(get_user_data_write)):
    st = ud.set_active_strategy(sid)
    if st is None:
        raise HTTPException(404, "not found")
    return _dto(st)


@router.post("/deactivate")
def deactivate(ud: UserData = Depends(get_user_data_write)):
    st = ud.active_strategy()
    if st is not None:
        st.is_active = False
    return {"ok": True}


@router.post("/{sid}/backtest", response_model=schemas.BacktestResponse)
def backtest(sid: int, body: schemas.BacktestRequest,
             ud: UserData = Depends(get_user_data),
             session: Session = Depends(get_session)):
    st = ud.strategy(sid)
    if st is None:
        raise HTTPException(404, "not found")
    if not st.conditions:
        raise HTTPException(400, "策略沒有任何條件")
    if body.end <= body.start:
        raise HTTPException(400, "結束日需晚於起始日")
    if (body.end - body.start).days > 366:
        raise HTTPException(400, "回測範圍上限 12 個月")
    r = se.run_backtest(
        session, st.conditions, st.sort_field, st.sort_desc, st.top_n,
        st.target_pct, st.horizon_days, st.stop_pct, body.start, body.end)
    return schemas.BacktestResponse(**r.__dict__)


@router.get("/active/daily", response_model=schemas.StrategyDailyResponse)
def active_daily(ud: UserData = Depends(get_user_data),
                 session: Session = Depends(get_session)):
    st = ud.active_strategy()
    if st is None or not st.conditions:
        return schemas.StrategyDailyResponse(strategy=None, date=None, items=[])
    latest = session.execute(
        select(models.DailyPrice.date).order_by(models.DailyPrice.date.desc()).limit(1)
    ).scalar()
    if latest is None:
        return schemas.StrategyDailyResponse(strategy=_dto(st), date=None, items=[])
    key = (st.id, _cond_hash(st), latest.isoformat())
    if key in _daily_cache:
        return _daily_cache[key]

    cands = se.evaluate(session, st.conditions, [latest]).get(latest, [])
    sort_s = se.FIELD_REGISTRY[st.sort_field].loader(session, [latest])
    cands = sorted(cands, key=lambda s: sort_s.get((s, latest), float("-inf")),
                   reverse=st.sort_desc)[: st.top_n]
    rows = dict(session.execute(
        select(models.DailyPrice.stock_id, models.DailyPrice.close)
        .where(models.DailyPrice.stock_id.in_(cands),
               models.DailyPrice.date == latest)).all()) if cands else {}
    names = dict(session.execute(
        select(models.Stock.id, models.Stock.name)
        .where(models.Stock.id.in_(cands))).all()) if cands else {}
    resp = schemas.StrategyDailyResponse(
        strategy=_dto(st), date=latest.isoformat(),
        items=[schemas.StrategyDailyItem(
            stock_id=s, name=names.get(s, s), close=rows.get(s),
            sort_value=sort_s.get((s, latest))) for s in cands])
    _daily_cache.clear()  # 只留最新一份，避免無界成長
    _daily_cache[key] = resp
    return resp
```

main.py：import `router as strategies_router`，在 settings_router 之後加
`app.include_router(strategies_router, prefix=_API)`。

- [ ] **Step 5: Run tests**

Run: `cd backend && python -m pytest tests/test_strategies_api.py tests/test_user_strategies.py -v`
Expected: 全 PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes_strategies.py backend/app/api/schemas.py backend/app/main.py backend/tests/test_strategies_api.py
git commit -m "feat(lab): 策略 CRUD/回測/當日清單 API（/api/lab/strategies，task4）"
```

---

### Task 5: 前端 API hooks

**Files:**
- Modify: `frontend/src/api/client.ts`（「── 帳號 ──」區段之前加「── 回測實驗室 ──」）

**Interfaces:**
- Consumes: `getJson / sendJson / useQuery / useMutation / useQueryClient`（檔內既有）
- Produces: `type Condition / Strategy / BacktestResult / StrategyDaily / FieldMeta`；hooks `useStrategyFields() / useStrategies() / useCreateStrategy() / usePatchStrategy() / useDeleteStrategy() / useActivateStrategy() / useDeactivateStrategy() / useBacktest() / useActiveStrategyDaily()`

- [ ] **Step 1: 實作（openapi 型別未重跑，手寫小型別——檔內已有先例）**

```typescript
// ── 回測實驗室 ──

export type Condition = { field: string; op: string; value: number | { n: number; threshold: number } };
export type Strategy = {
  id: number; name: string; conditions: Condition[];
  sort_field: string; sort_desc: boolean; top_n: number;
  target_pct: number; horizon_days: number; stop_pct: number | null; is_active: boolean;
};
export type FieldMeta = { key: string; label: string; group: string; unit: string };
export type BacktestResult = {
  samples: number; hits: number; hit_rate: number | null; base_rate: number | null;
  lift: number | null; avg_max_drawdown: number | null;
  monthly: { month: string; samples: number; hits: number }[];
  recent: { date: string; stock_id: string; name: string; entry: number; hit: boolean;
            stopped: boolean; max_gain_pct: number; max_dd_pct: number }[];
  warn_loose: boolean; signal_days: number;
};
export type StrategyDaily = {
  strategy: Strategy | null; date: string | null;
  items: { stock_id: string; name: string; close: number | null; sort_value: number | null }[];
};

export function useStrategyFields() {
  return useQuery({ queryKey: ["strategy-fields"], staleTime: Infinity,
    queryFn: () => getJson<FieldMeta[]>("/lab/strategies/fields") });
}
export function useStrategies() {
  return useQuery({ queryKey: ["strategies"],
    queryFn: () => getJson<Strategy[]>("/lab/strategies/") });
}
function useStrategyMutation<T, A>(fn: (a: A) => Promise<T>) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: fn, onSuccess: () => {
    qc.invalidateQueries({ queryKey: ["strategies"] });
    qc.invalidateQueries({ queryKey: ["strategy-daily"] });
  }});
}
export function useCreateStrategy() {
  return useStrategyMutation((body: Partial<Strategy>) =>
    sendJson<Strategy>("POST", "/lab/strategies/", body));
}
export function usePatchStrategy() {
  return useStrategyMutation(({ id, ...body }: Partial<Strategy> & { id: number; clear_stop?: boolean }) =>
    sendJson<Strategy>("PATCH", `/lab/strategies/${id}`, body));
}
export function useDeleteStrategy() {
  return useStrategyMutation((id: number) =>
    sendJson<{ ok: boolean }>("DELETE", `/lab/strategies/${id}`));
}
export function useActivateStrategy() {
  return useStrategyMutation((id: number) =>
    sendJson<Strategy>("POST", `/lab/strategies/${id}/activate`));
}
export function useDeactivateStrategy() {
  return useStrategyMutation(() =>
    sendJson<{ ok: boolean }>("POST", "/lab/strategies/deactivate"));
}
export function useBacktest() {
  return useMutation({ mutationFn: ({ id, start, end }: { id: number; start: string; end: string }) =>
    sendJson<BacktestResult>("POST", `/lab/strategies/${id}/backtest`, { start, end }) });
}
export function useActiveStrategyDaily() {
  return useQuery({ queryKey: ["strategy-daily"],
    queryFn: () => getJson<StrategyDaily>("/lab/strategies/active/daily") });
}
```

（`sendJson` 若現簽名不支援 DELETE，照檔內實作確認；不支援就補 method 參數。）

- [ ] **Step 2: 驗證**

Run: `cd frontend && npx tsc --noEmit`
Expected: 無錯誤

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/client.ts
git commit -m "feat(lab): 回測實驗室前端 API hooks（task5）"
```

---

### Task 6: 回測實驗室 UI（策略室新 section）

**Files:**
- Create: `frontend/src/components/BacktestLab.tsx`
- Modify: `frontend/src/pages/LabPage.tsx`（`<PaperSection>` 之前掛 `<BacktestLabSection />`；import 同檔頭慣例）

**Interfaces:**
- Consumes: Task 5 全部 hooks；`Modal / inputCls`（components/Modal）；`fmtPct / changeColor`（lib/format）
- Produces: `export function BacktestLabSection()`（無 props）

結構（單一檔案內部拆小元件，全部私有）：

```
BacktestLabSection
├─ StrategyList     左欄：策略清單＋新增/刪除/啟用開關（useStrategies 等）
├─ StrategyEditor   右欄：
│   ├─ 名稱 input（改名即 PATCH）
│   ├─ 模式切換（簡單｜專業）——皆操作同一份 conditions 陣列
│   │   ├─ SimpleMode：TEMPLATES 常數（3 個起步模板，見下）＋「＋加條件」
│   │   └─ ProMode：條件列 [欄位下拉(useStrategyFields 分組)｜op 下拉｜數值 input｜刪]
│   ├─ 目標區：N 日（number）＋X%（number）＋停損開關與 -Y%（number）
│   ├─ 排序區：欄位下拉＋方向＋top_n
│   └─ 回測區：3/6/12 個月鈕（end=今天、start=today-90/180/365）＋自訂起訖 date input
│       └─ ResultPanel：命中率 vs 基率 vs lift 大字卡＋樣本數（<30 顯示「樣本不足」
│          徽章）＋warn_loose 警示條＋逐月長條（純 div bar，不用 echarts）＋
│          最近訊號明細表（date/股票/entry/命中|停損/最高漲/最深回撤）
```

簡單模式模板常數（編譯成 conditions，可再加條件微調）：

```typescript
const TEMPLATES: { name: string; desc: string; conditions: Condition[] }[] = [
  { name: "法人進駐低位股", desc: "投信5日買超>0、股價低於季線",
    conditions: [
      { field: "trust_net_5", op: "gt", value: 0 },
      { field: "ma60_gap", op: "lt", value: 0 },
    ]},
  { name: "營收動能股", desc: "營收YoY>20%、站上月線",
    conditions: [
      { field: "rev_yoy", op: "gt", value: 20 },
      { field: "ma20_gap", op: "gt", value: 0 },
    ]},
  { name: "大戶吸籌", desc: "大戶占比>40%、外資5日買超>0",
    conditions: [
      { field: "big_pct", op: "gt", value: 40 },
      { field: "foreign_net_5", op: "gt", value: 0 },
    ]},
];
```

編輯採 draft 模式（比照 SettingsPage）：改動先進 local state，「儲存」才 PATCH；
「跑回測」前若 draft 髒了先自動存。op 顯示中文：`>`→「大於」、`streak_gt`→「連N日大於」
（選它時數值區變 n＋threshold 兩格）。

- [ ] **Step 1: 實作 BacktestLab.tsx**（依上述結構完整實作；樣式沿用現有 `rounded-xl border border-edge bg-panel p-4` 卡片語彙）
- [ ] **Step 2: LabPage.tsx 掛載**

```tsx
import { BacktestLabSection } from "../components/BacktestLab";
// return 內、<PaperSection since={since} /> 之前：
<BacktestLabSection />
```

- [ ] **Step 3: 驗證**

Run: `cd frontend && npx tsc --noEmit`
Expected: 無錯誤
再以瀏覽器實測：建策略→加 2 條件→跑 3 個月回測→看到命中率/基率/明細。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/BacktestLab.tsx frontend/src/pages/LabPage.tsx
git commit -m "feat(lab): 回測實驗室 UI——策略庫/雙模式編輯器/回測面板（task6）"
```

---

### Task 7: 進場推薦第三軌

**Files:**
- Modify: `frontend/src/pages/RecommendationsPage.tsx`（頁籤列 :306 附近、內容區）

**Interfaces:**
- Consumes: `useActiveStrategyDaily`（Task 5）
- Produces: 推薦頁第三頁籤（標題＝策略名），內容為當日清單表格

- [ ] **Step 1: 實作**

頁面 state `track` 型別放寬為 `Track | "custom"`。頁籤列改為：

```tsx
const { data: custom } = useActiveStrategyDaily();
// 既有 (["wave","long"] as Track[]).map(...) 之後追加：
{custom?.strategy && (
  <button key="custom"
    onClick={() => setTrack("custom")}
    className={`border-b-2 px-1 pb-2 text-sm font-medium transition ${
      track === "custom" ? "border-amber-500 text-amber-300"
                         : "border-transparent text-muted hover:text-gray-300"}`}>
    🧪 {custom.strategy.name}
    {track === "custom" && <span className="ml-1.5 text-xs">({custom.items.length})</span>}
  </button>
)}
```

`track === "custom"` 時捨棄既有卡片流，渲染簡表（不進 lookback／不吃 wave 的
標籤與滑桿邏輯——所有 `track === "wave"` 守衛原樣自然跳過）：

```tsx
{track === "custom" && custom?.strategy && (
  <div className="rounded-xl border border-edge bg-panel p-4">
    <p className="mb-3 text-xs text-muted">
      自訂策略「{custom.strategy.name}」· {custom.date} 收盤符合條件前 {custom.strategy.top_n} 檔
      · 目標 {custom.strategy.horizon_days} 日 +{custom.strategy.target_pct}%
      {custom.strategy.stop_pct != null && ` · 停損 -${custom.strategy.stop_pct}%`}
      　<Link to="/lab" className="text-sky-400 hover:underline">→ 回實驗室調整</Link>
    </p>
    {custom.items.length === 0 ? (
      <p className="text-sm text-muted">今日無符合條件的股票</p>
    ) : (
      <table className="w-full text-sm">
        <thead><tr className="text-xs text-muted">
          <th className="py-1 text-left font-normal">股票</th>
          <th className="py-1 text-right font-normal">收盤</th>
          <th className="py-1 text-right font-normal">排序值</th>
        </tr></thead>
        <tbody>
          {custom.items.map((it) => (
            <tr key={it.stock_id} className="border-t border-edge/60">
              <td className="py-1.5">
                <Link to={`/stocks/${it.stock_id}`} className="hover:underline">
                  <span className="tabular-nums text-muted">{it.stock_id}</span> {it.name}
                </Link>
              </td>
              <td className="py-1.5 text-right tabular-nums">{it.close ?? "—"}</td>
              <td className="py-1.5 text-right tabular-nums text-muted">
                {it.sort_value == null ? "—" : Math.round(it.sort_value).toLocaleString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    )}
  </div>
)}
```

既有內容區最外層若以 `track` 分支，確認 `custom` 分支時不渲染 wave/long 區塊
（依現檔結構在對應條件補 `track !== "custom"` 守衛，最小侵入）。

- [ ] **Step 2: 驗證**

Run: `cd frontend && npx tsc --noEmit`
瀏覽器：啟用一個策略→推薦頁出現第三頁籤→點開看到清單→停用後頁籤消失。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/RecommendationsPage.tsx
git commit -m "feat(recs): 使用者自訂第三軌頁籤（task7）"
```

---

### Task 8: 端到端驗證＋打包

- [ ] **Step 1: 後端全測**

Run: `cd backend && python -m pytest tests/ -v --timeout=120`
Expected: 全 PASS（既有測試不得變紅）

- [ ] **Step 2: 前端打包**

Run: `cd frontend && npx tsc --noEmit && npm run build`

- [ ] **Step 3: 瀏覽器端到端**（dev server，pro@test.twa 登入）
  1. 策略室→回測實驗室：套「營收動能股」模板→跑 6 個月回測→結果出現、樣本>0
  2. 改名為「我的動能策略」→啟用
  3. 進場推薦→出現「🧪 我的動能策略」頁籤→清單有內容
  4. 用 free@test.twa 登入→看不到 pro 的策略（策略庫空）
  5. 修改條件後回推薦頁→清單內容變（快取失效）

- [ ] **Step 4: Commit（若有零星修正）＋更新 memory**

```bash
git add -A && git commit -m "feat(lab): 回測實驗室端到端收尾（task8）"
```

memory `multi-tenant-blockers.md` 補：回測實驗室新 API 已按 UserData 隔離；
routes_lab 舊端點 prefix 仍未補（獨立待辦）。
