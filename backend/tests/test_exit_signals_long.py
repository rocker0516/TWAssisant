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
