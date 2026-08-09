"""進場時機分數（EntryTimingScore）+ context 籌碼時機 helper 測試（PIT、無未來函數）。

驗證研究定版的三訊號合成：法人翻買近期性(0.4)＋外資投信共識(0.3)＋投信連買(0.3)。
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from app.engines.context import StockContext
from app.engines.rules.wave import EntryTimingScore
from app.storage import models


def _ctx(foreign: list[float], trust: list[float]) -> StockContext:
    """用法人日序列建最小 context（時機規則只讀 ctx.inst）。"""
    n = len(foreign)
    base = date(2025, 1, 1)
    inst = pd.DataFrame({
        "date": [base + timedelta(days=i) for i in range(n)],
        "foreign_net": foreign,
        "trust_net": trust,
    })
    return StockContext(
        stock=models.Stock(id="9999", name="測試"),
        date=base + timedelta(days=n - 1),
        prices=pd.DataFrame(), inds=pd.DataFrame(), inst=inst,
    )


def test_consecutive_buy_counts_tail_run():
    ctx = _ctx([0] * 5, [-1, 2, 0, 3, 5])  # 尾端 3,5 連買，中間 0 中斷
    assert ctx.inst_consecutive_buy("trust_net") == 2


def test_flip_recency_fresh_flip_is_one():
    # 前 24 天每天 -1（20 日累計恆負），最後一天大買 → 當天 20 日累計剛翻正
    ft = [-1.0] * 24 + [25.0]
    ctx = _ctx([x / 2 for x in ft], [x / 2 for x in ft])
    assert ctx.inst_cum_flip_recency() == 1.0  # days_since=0 → 1.0


def test_flip_recency_zero_when_currently_net_seller():
    ctx = _ctx([-1.0] * 25, [-1.0] * 25)  # 一路淨賣，累計恆負
    assert ctx.inst_cum_flip_recency() == 0.0


def test_flip_recency_none_when_insufficient_history():
    ctx = _ctx([1.0] * 10, [1.0] * 10)  # < window+1 筆
    assert ctx.inst_cum_flip_recency() is None


def test_score_high_on_fresh_flip_with_consensus():
    ft = [-1.0] * 24 + [25.0]
    ctx = _ctx([x / 2 for x in ft], [x / 2 for x in ft])
    rule = EntryTimingScore()
    s = rule.score(ctx)
    # recency=1(0.4) + 共識=1(0.3) + 投信連買1/8(0.0375) → ~73.75
    assert s is not None and 70 <= s <= 78
    assert rule.reason(ctx, s) == "法人剛翻買進場"
    assert "剛由賣轉買" in (rule.evidence(ctx, s) or "")


def test_score_zero_when_net_seller():
    ctx = _ctx([-2.0] * 25, [-2.0] * 25)
    rule = EntryTimingScore()
    assert rule.score(ctx) == 0.0
    assert rule.reason(ctx, 0.0) is None
    assert rule.evidence(ctx, 0.0) is None  # 無時機訊號 → None，不污染行情白話


def test_score_none_without_inst_data():
    ctx = StockContext(
        stock=models.Stock(id="9999", name="測試"),
        date=date(2025, 2, 1),
        prices=pd.DataFrame(), inds=pd.DataFrame(),
        inst=pd.DataFrame(columns=["date", "foreign_net", "trust_net"]),
    )
    assert EntryTimingScore().score(ctx) is None  # 缺料剔除，不灌 0
