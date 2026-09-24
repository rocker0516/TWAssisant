"""Level 2 模擬引擎測試（FRS §3~§5、§7 重放一致性）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.research.level2 import costs as ct
from app.research.level2 import engine as eg
from app.research.level2 import policy as pl
from app.research.level2.simulate import run_simulation


# ── 成本與價格制度（§3） ──

def test_tick_and_limits():
    assert ct.tick_size(9.99) == 0.01
    assert ct.tick_size(10) == 0.05
    assert ct.tick_size(500) == 1.0
    assert ct.up_limit(100) == 110.0
    assert ct.down_limit(100) == 90.0
    # 9.9 → raw 10.89 落入 0.05 tick 區間，向下貼 10.85
    assert ct.up_limit(9.9) == 10.85
    # 95 → raw 104.5，恰為 0.5 tick 整數倍
    assert ct.up_limit(95) == 104.5


def test_cost_model_min_fee_and_tax():
    c = ct.CostModel()
    assert c.buy_fee(10, 100) == 20.0            # 1000×0.1425%=1.4 → 低消 20
    assert c.buy_fee(100, 1000) == pytest.approx(142.5)
    assert c.sell_tax(100, 1000) == pytest.approx(300.0)


# ── 委託執行（§4） ──

def _cost():
    return ct.CostModel()


def test_buy_fill_and_roundtrip_loses_exactly_costs():
    st = eg.PortfolioState(100_000)
    st, fills = eg.execute_day(st, [eg.Order("A", eg.BUY, 500, "entry")],
                               {"A": 100.0}, {"A": 100.0}, _cost())
    assert fills[0].status == eg.FILLED and st.positions["A"] == 500
    st, fills = eg.execute_day(st, [eg.Order("A", eg.SELL, 500, "exit")],
                               {"A": 100.0}, {"A": 100.0}, _cost())
    assert fills[0].status == eg.FILLED and not st.positions
    expected = 100_000 - 50_000 * 0.001425 * 2 - 50_000 * 0.003
    assert st.cash == pytest.approx(expected)


def test_buy_rejected_at_limit_up():
    st = eg.PortfolioState(100_000)
    st, fills = eg.execute_day(st, [eg.Order("A", eg.BUY, 100, "entry")],
                               {"A": 110.0}, {"A": 100.0}, _cost())
    assert fills[0].status == eg.REJECTED and "limit_up" in fills[0].reason
    assert st.cash == 100_000 and not st.positions


def test_sell_deferred_at_limit_down_then_retried():
    st = eg.PortfolioState(0)
    st.positions["A"] = 100
    st, fills = eg.execute_day(st, [eg.Order("A", eg.SELL, 100, "defense")],
                               {"A": 90.0}, {"A": 100.0}, _cost())
    assert fills[0].status == eg.DEFERRED and st.positions["A"] == 100
    retry = eg.deferred_to_orders(fills)
    assert retry == [eg.Order("A", eg.SELL, 100, "defense")]
    st, fills = eg.execute_day(st, retry, {"A": 85.0}, {"A": 90.0}, _cost())
    assert fills[0].status == eg.FILLED and not st.positions


def test_missing_open_deferred():
    st = eg.PortfolioState(100_000)
    st, fills = eg.execute_day(st, [eg.Order("A", eg.BUY, 100, "entry")],
                               {}, {}, _cost())
    assert fills[0].status == eg.DEFERRED and "no_trade" in fills[0].reason


def test_insufficient_cash_scales_down_then_rejects():
    st = eg.PortfolioState(10_000)
    st, fills = eg.execute_day(st, [eg.Order("A", eg.BUY, 500, "entry")],
                               {"A": 100.0}, {"A": 100.0}, _cost())
    f = fills[0]
    assert f.status == eg.FILLED and f.qty < 500
    assert f.price * f.qty + f.fee <= 10_000
    st2 = eg.PortfolioState(5.0)
    st2, fills = eg.execute_day(st2, [eg.Order("B", eg.BUY, 10, "entry")],
                                {"B": 100.0}, {"B": 100.0}, _cost())
    assert fills[0].status == eg.REJECTED and "insufficient_cash" in fills[0].reason


def test_sells_release_cash_for_same_day_buys():
    st = eg.PortfolioState(0)
    st.positions["A"] = 1000
    orders = [eg.Order("B", eg.BUY, 500, "rebalance"),
              eg.Order("A", eg.SELL, 1000, "rebalance")]
    st, fills = eg.execute_day(st, orders, {"A": 100.0, "B": 100.0},
                               {"A": 100.0, "B": 100.0}, _cost())
    buys = [f for f in fills if f.side == eg.BUY]
    assert buys[0].status == eg.FILLED and buys[0].qty == 500


# ── Baseline v0 策略（§5） ──

def _pct(d: dict) -> pd.Series:
    return pd.Series(d, dtype=float)


def test_select_targets_buffer():
    # 100 檔：pct 由高到低 s001 最強
    pct = _pct({f"s{i:03d}": 1 - i / 100 for i in range(100)})
    params = pl.BaselineParams(k_in=20, k_hold=60, target_n=20,
                               rebalance_every=5)  # 顯式參數：測緩衝邏輯本身
    held = {"s024", "s070"}       # rank 25 應續抱；rank 71 應出場
    t = pl.select_targets(held, pct, params)
    assert "s024" in t and "s070" not in t
    assert len(t) == 20
    # 新進者全部 rank ≤ 20
    ranks = pct.rank(ascending=False, method="first")
    assert all(ranks[s] <= 20 for s in t - held)


def test_defense_only_when_weak_and_enabled():
    st = eg.PortfolioState(0)
    st.positions.update({"A": 100, "B": 100, "C": 100})
    pct1 = _pct({"A": 0.15, "B": 0.55})   # C 缺席（跌出 universe）→ 不觸發
    orders = pl.plan_defense(st, pct1, pl.P5_PARAMS)
    assert [o.stock_id for o in orders] == ["A"]
    assert pl.plan_defense(st, pct1, pl.P1_PARAMS) == []


def test_plan_rebalance_sells_dropouts_buys_by_rank():
    st = eg.PortfolioState(50_000)
    st.positions["zzz"] = 10               # 不在排名內 → rank ∞ → 賣出
    pct = _pct({"aaa": 0.99, "bbb": 0.95, "ccc": 0.90})
    params = pl.BaselineParams(target_n=2, k_in=2, k_hold=2, rebalance_every=5)
    orders = pl.plan_rebalance(st, 51_000, {"aaa": 100.0, "bbb": 50.0}, pct,
                               params)
    assert orders[0] == eg.Order("zzz", eg.SELL, 10, "rebalance")
    buys = [o for o in orders if o.side == eg.BUY]
    # ccc rank 3 > k_in 不進；bbb 有價、aaa 有價，依 rank 序 aaa 先
    assert [o.stock_id for o in buys] == ["aaa", "bbb"]
    assert buys[0].qty == int(51_000 / 2 / 100.0)


# ── 端到端模擬＋重放一致性（§7） ──

def _synthetic_market(n_days=13, stocks=("A", "B", "C", "D")):
    dates = pd.date_range("2026-01-05", periods=n_days, freq="B").strftime(
        "%Y-%m-%d")
    rng = np.random.default_rng(7)
    close = pd.DataFrame(
        100 * np.cumprod(1 + rng.normal(0, 0.01, (n_days, len(stocks))), 0),
        index=dates, columns=list(stocks))
    open_ = close.shift(1).fillna(100.0) * (1 + rng.normal(0, 0.002,
                                                           close.shape))
    return open_.round(2), close.round(2)


def test_run_simulation_replay_and_nav_consistency():
    open_, close = _synthetic_market()
    # 固定排名：A 最強、D 最弱且觸發防禦
    pct5 = pd.DataFrame(
        {"A": 0.99, "B": 0.9, "C": 0.5, "D": 0.1},
        index=close.index)
    pct1 = pct5.copy()
    params = pl.BaselineParams(target_n=2, k_in=2, k_hold=3,
                               rebalance_every=5)
    res = run_simulation(open_, close, pct5, pct1, params,
                         initial_cash=1_000_000)
    # 重放一致性：由 fills 序列重建 == 最終狀態
    fills = [eg.Fill(**{k: r[k] for k in
                        ("stock_id", "side", "qty", "price", "fee", "tax",
                         "status", "reason")})
             for r in res.fills.to_dict("records")]
    replayed = eg.replay(1_000_000, fills)
    assert replayed.cash == pytest.approx(res.final_state.cash)
    assert replayed.positions == res.final_state.positions
    # NAV 會計：最後一日 NAV = cash + Σ qty×close
    last = close.index[-1]
    manual = res.final_state.cash + sum(
        q * close.loc[last, s] for s, q in res.final_state.positions.items())
    assert res.nav.iloc[-1] == pytest.approx(manual)
    # 有買進 A/B（Top-2）、D 從未持有（防禦門檻外且非目標）
    bought = set(res.fills[res.fills.side == "buy"].stock_id)
    assert bought == {"A", "B"}


def test_run_simulation_defense_exit_fires():
    open_, close = _synthetic_market()
    pct5 = pd.DataFrame({"A": 0.99, "B": 0.9, "C": 0.5, "D": 0.1},
                        index=close.index)
    pct1 = pct5.copy()
    # 第 6 天起 B 變最弱 → 防禦出場（不等再平衡日）
    pct1.iloc[6:, pct1.columns.get_loc("B")] = 0.05
    params = pl.BaselineParams(target_n=2, k_in=2, k_hold=4,
                               rebalance_every=10)
    res = run_simulation(open_, close, pct5, pct1, params,
                         initial_cash=1_000_000)
    defense = res.fills[(res.fills.reason.str.startswith("defense"))
                        & (res.fills.side == "sell")]
    assert not defense.empty and set(defense.stock_id) == {"B"}
    assert "B" not in res.final_state.positions


# ── KPI 指標（§6） ──

def test_max_drawdown():
    from app.research.level2 import metrics as mt
    nav = pd.Series([100, 120, 90, 110, 80], dtype=float)
    assert mt.max_drawdown(nav) == pytest.approx(80 / 120 - 1)
    assert mt.max_drawdown(pd.Series([1.0, 2.0, 3.0])) == 0.0


def test_excess_t_stat_direction():
    from app.research.level2 import metrics as mt
    idx = pd.RangeIndex(101)
    rng = np.random.default_rng(3)
    port = pd.Series(np.cumprod(1 + rng.normal(0.003, 0.001, 101)), index=idx)
    bench = pd.Series(np.cumprod(1 + rng.normal(0.001, 0.001, 101)), index=idx)
    mean_ex, t = mt.excess_t_stat(port, bench)
    assert mean_ex > 0 and t > 5           # 穩定正超額 → t 顯著


def test_summarize_fields_and_mdd_constraint_flag():
    from app.research.level2 import metrics as mt
    idx = [f"d{i}" for i in range(50)]
    nav = pd.Series(np.linspace(100, 110, 50), index=idx)
    bench = pd.Series(np.r_[np.linspace(100, 105, 25),
                            np.linspace(105, 95, 25)], index=idx)
    fills = pd.DataFrame([{"status": "filled", "price": 100.0, "qty": 10,
                           "fee": 20.0, "tax": 30.0, "reason": "defense"}])
    s = mt.summarize(nav, bench, fills)
    assert s["mdd_within_bench"] is True   # 單調上升 MDD=0 ≥ 大盤負 MDD
    assert s["total_costs"] == 50 and s["n_defense_exits"] == 1
    assert s["excess_pct"] == pytest.approx(
        (110 / 100 - 1) * 100 - (95 / 100 - 1) * 100, abs=0.01)


# ── captop 市值加權頂分位（Level 2.1 設計 §1） ──

def test_plan_captop_selection_and_sizing():
    from app.research.level2 import capweight as cw
    st = eg.PortfolioState(0)
    st.positions.update({"old": 100, "big": 50})
    pct = _pct({"big": 0.95, "mid": 0.9, "small": 0.85, "weak": 0.3})
    caps = _pct({"big": 9e9, "mid": 4e9, "small": 1e9, "weak": 8e9})
    params = cw.CapTopParams(threshold=0.8, top_n=2, min_trade=1_000)
    ref = {"big": 100.0, "mid": 50.0, "small": 10.0}
    orders = cw.plan_captop(st, 1_000_000, ref, pct, caps, params)
    # weak 過不了門檻（雖然市值大）；target = big, mid；old 出局全賣
    assert orders[0] == eg.Order("old", eg.SELL, 100, "rebalance")
    buys = {o.stock_id: o for o in orders if o.side == eg.BUY}
    assert set(buys) == {"big", "mid"}
    # big 權重 9/13、已持 50 股 → 目標 int(1e6×9/13/100)=6923 → 加碼 6873
    assert buys["big"].qty == int(1_000_000 * 9 / 13 / 100.0) - 50
    # 買單順序：權重大者在前
    buy_list = [o.stock_id for o in orders if o.side == eg.BUY]
    assert buy_list == ["big", "mid"]


def test_plan_captop_min_trade_skips_dust():
    from app.research.level2 import capweight as cw
    st = eg.PortfolioState(0)
    pct = _pct({"a": 0.99, "b": 0.9})
    caps = _pct({"a": 9e9, "b": 1e7})   # b 權重極小 → 部位 < min_trade
    params = cw.CapTopParams(threshold=0.8, top_n=2, min_trade=10_000)
    orders = cw.plan_captop(st, 1_000_000, {"a": 100.0, "b": 100.0},
                            pct, caps, params)
    assert [o.stock_id for o in orders] == ["a"]


def test_plan_captop_trims_overweight():
    from app.research.level2 import capweight as cw
    st = eg.PortfolioState(0)
    st.positions["a"] = 10_000               # 遠超目標 → 減碼賣單
    pct = _pct({"a": 0.99, "b": 0.9})
    caps = _pct({"a": 5e9, "b": 5e9})
    params = cw.CapTopParams(threshold=0.8, top_n=2, min_trade=1_000)
    orders = cw.plan_captop(st, 1_000_000, {"a": 100.0, "b": 100.0},
                            pct, caps, params)
    sells = [o for o in orders if o.side == eg.SELL]
    assert len(sells) == 1 and sells[0].stock_id == "a"
    assert sells[0].qty == 10_000 - int(1_000_000 * 0.5 / 100.0)


def test_run_simulation_with_captop_planner():
    from app.research.level2 import capweight as cw
    open_, close = _synthetic_market()
    pct5 = pd.DataFrame({"A": 0.99, "B": 0.9, "C": 0.5, "D": 0.1},
                        index=close.index)
    caps = pd.DataFrame({"A": 9e9, "B": 3e9, "C": 1e9, "D": 8e9},
                        index=close.index)
    params = cw.CapTopParams(threshold=0.8, top_n=2, rebalance_every=5,
                             min_trade=1_000)
    planner = lambda st, nav, ref, sig, d: cw.plan_captop(  # noqa: E731
        st, nav, ref, sig, caps.loc[d], params)
    res = run_simulation(open_, close, pct5, None, params, 1_000_000,
                         planner=planner)
    held = set(res.final_state.positions)
    assert held == {"A", "B"}
    # 重放一致性同樣成立
    fills = [eg.Fill(**{k: r[k] for k in
                        ("stock_id", "side", "qty", "price", "fee", "tax",
                         "status", "reason")})
             for r in res.fills.to_dict("records")]
    rep = eg.replay(1_000_000, fills)
    assert rep.cash == pytest.approx(res.final_state.cash)
    assert rep.positions == res.final_state.positions
