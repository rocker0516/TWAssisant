"""既有長線持倉的出場照顧（長線「推薦」已移除，出場訊號保留）：
FundamentalWeakSignal 營收 yoy 降級、法人 20 日窗。ScoreSlipSignal 已隨
Score(track='long') 停產而刪除。"""
import pandas as pd

from app.engines.exit_signals import Position, FundamentalWeakSignal, Sev
from app.storage import models


class Ctx:  # 最小假 context
    def __init__(self, revenue=None, inst=None, ind=None):
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
    return models.Holding(stock_id="1101", track="long",
                          entry_snapshot={"total_score": entry_score})


POS = Position(shares=1, avg_cost=100, highest=100, close=100)


def test_yoy_deep_negative_is_warn_not_critical():
    ctx = Ctx(revenue=pd.Series({"yoy": -20.0}))
    hits = FundamentalWeakSignal().check(_h(), POS, ctx)
    assert any(h.code == "rev_drop" and h.sev == Sev.WARN for h in hits)


def test_yoy_mild_negative_is_early():
    ctx = Ctx(revenue=pd.Series({"yoy": -3.0}))
    hits = FundamentalWeakSignal().check(_h(), POS, ctx)
    assert any(h.code == "rev_neg" and h.sev == Sev.EARLY for h in hits)
