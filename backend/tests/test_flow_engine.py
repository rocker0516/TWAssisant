"""FlowEngine 算法測試：連買天數、累積、phase 週期判斷、法人 vs 指數量化關係（無未來函數）。"""

from __future__ import annotations

import pytest

from app.engines.flow_engine import FlowEngine, _consec, _pearson, _ranks


def test_consec_buy_run_positive():
    # 尾端 3 天連買（同號），更早的賣超不算
    assert _consec([-5.0, -2.0, 3.0, 1.0, 4.0]) == 3


def test_consec_sell_run_negative():
    assert _consec([2.0, -1.0, -3.0]) == -2


def test_consec_breaks_on_zero_or_none():
    assert _consec([1.0, 2.0, 0.0, 3.0]) == 1   # 0 中斷
    assert _consec([1.0, None, 2.0]) == 1        # None 中斷
    assert _consec([]) == 0


def test_pearson_perfect_and_degenerate():
    assert _pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert _pearson([1, 1, 1, 1], [1, 2, 3, 4]) is None  # 零變異 → None


def test_ranks_handles_ties():
    # 1,2,2,3 → 序位 1, 2.5, 2.5, 4
    assert _ranks([1.0, 2.0, 2.0, 3.0]) == [1.0, 2.5, 2.5, 4.0]


def test_tail_sum_skips_none():
    eng = FlowEngine()
    assert eng._tail_sum([1.0, 2.0, None, 3.0], 3) == 5.0  # 近3取 [2,None,3]→5
    assert eng._tail_sum([], 5) is None


def test_phase_classification():
    eng = FlowEngine()
    assert eng._phase([1.0] * 120) == "持續買超循環"
    assert eng._phase([-1.0] * 120) == "持續賣超循環"
    # 近20正、近60整體仍負 → 由賣轉買
    daily = [-3.0] * 100 + [2.0] * 20
    assert eng._phase(daily) == "由賣轉買"


def test_market_relation_no_lookahead():
    """法人累積買超後指數確實上漲 → avg_ret_pos>0、勝率高、相關為正；且不偷看未來。"""
    eng = FlowEngine()
    n = 120
    daily = [1.0] * n              # 全程買超 → 每日 cum20>0
    index = [100.0 + i for i in range(n)]  # 指數穩定上升
    rel = eng._market_relation(daily, index, h=20)
    assert rel is not None
    assert rel["samples"] > 0
    assert rel["avg_ret_pos"] is not None and rel["avg_ret_pos"] > 0
    assert rel["winrate_pos"] == 1.0  # 上升趨勢中每個樣本未來都漲


def test_market_relation_insufficient_samples():
    eng = FlowEngine()
    assert eng._market_relation([1.0] * 10, [100.0] * 10, h=20) is None
