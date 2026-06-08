"""長線軌規則（基本面為主）。

硬篩：近4季EPS>0（以本益比>0 代理，TWSE 全市場無逐檔EPS）、月營收年增>0、本益比<30。
評分5類預設配分：獲利25 / 營收成長25 / 估值20 / 體質20 / 趨勢輔助10。

資料來自 context.valuation(pe/pb/dividend_yield) / revenue(yoy) / financials(margins)，
皆為最新一筆 Series（ScoringEngine 載入）。EPS≈收盤價/本益比（trailing）。
"""

from __future__ import annotations

import pandas as pd

from .base import FilterRule, ScoreRule, clamp
from ..context import StockContext


def _get(series: pd.Series | None, key: str) -> float | None:
    if series is None or key not in series:
        return None
    v = series[key]
    return None if pd.isna(v) else float(v)


# ─────────────── 硬篩 ───────────────


class PositiveEpsFilter(FilterRule):
    name = "positive_eps"

    def passes(self, ctx: StockContext) -> bool:
        pe = _get(ctx.valuation, "pe")  # pe>0 ⟺ 近4季 trailing EPS>0
        return pe is not None and pe > 0


class RevenueGrowthFilter(FilterRule):
    name = "revenue_growth"

    def passes(self, ctx: StockContext) -> bool:
        yoy = _get(ctx.revenue, "yoy")
        return yoy is not None and yoy > 0


class PeUnder30Filter(FilterRule):
    name = "pe_under_30"

    def passes(self, ctx: StockContext) -> bool:
        pe = _get(ctx.valuation, "pe")
        return pe is not None and 0 < pe < 30


LONG_FILTERS: list[FilterRule] = [PositiveEpsFilter(), RevenueGrowthFilter(), PeUnder30Filter()]

# ─────────────── 評分 ───────────────


class ProfitScore(ScoreRule):
    category = "profit"
    default_weight = 25.0

    def score(self, ctx: StockContext) -> float:
        nm = _get(ctx.financials, "net_margin")
        return clamp(nm * 4) if nm is not None else 0.0  # 稅後純益率 25%+ → 100

    def reason(self, ctx, value):
        nm = _get(ctx.financials, "net_margin")
        return "高獲利率" if nm is not None and nm >= 20 else None


class GrowthScore(ScoreRule):
    category = "growth"
    default_weight = 25.0

    def score(self, ctx: StockContext) -> float:
        yoy = _get(ctx.revenue, "yoy")
        return clamp(50 + yoy * 2.5) if yoy is not None else 0.0  # ±20% → 0~100

    def reason(self, ctx, value):
        yoy = _get(ctx.revenue, "yoy")
        return f"營收年增 {yoy:+.0f}%" if yoy is not None and yoy >= 10 else None


class ValuationScore(ScoreRule):
    category = "valuation"
    default_weight = 20.0

    def score(self, ctx: StockContext) -> float:
        pe = _get(ctx.valuation, "pe")
        dy = _get(ctx.valuation, "dividend_yield") or 0
        if pe is None or pe <= 0:
            return 0.0
        return clamp((30 - pe) / 30 * 70 + dy * 6)

    def reason(self, ctx, value):
        pe = _get(ctx.valuation, "pe")
        return "本益比偏低" if pe is not None and 0 < pe < 15 else None


class QualityScore(ScoreRule):
    category = "quality"
    default_weight = 20.0

    def score(self, ctx: StockContext) -> float:
        op = _get(ctx.financials, "op_margin")
        gm = _get(ctx.financials, "gross_margin")
        if op is None and gm is None:
            return 0.0
        return clamp((op or 0) * 3 + (gm or 0))

    def reason(self, ctx, value):
        gm = _get(ctx.financials, "gross_margin")
        return "高毛利體質" if gm is not None and gm >= 30 else None


class TrendAuxScore(ScoreRule):
    category = "trend_aux"
    default_weight = 10.0

    def score(self, ctx: StockContext) -> float:
        ind, prev = ctx.ind, ctx.ind_ago(5)
        if ind is None or ind.get("ma60") is None or ctx.close is None:
            return 0.0
        s = 0.0
        if ctx.close > ind["ma60"]:
            s += 50
        if prev is not None and prev.get("ma60") is not None and ind["ma60"] > prev["ma60"]:
            s += 50
        return clamp(s)

    def reason(self, ctx, value):
        return "站上季線多頭" if value >= 100 else None


LONG_SCORERS: list[ScoreRule] = [
    ProfitScore(),
    GrowthScore(),
    ValuationScore(),
    QualityScore(),
    TrendAuxScore(),
]
