"""可插拔規則底層（架構③）。

BaseRule → FilterRule（硬篩，passes 回 bool）/ ScoreRule（評分，0~100 + 理由）。
加新指標只加規則類，不動 Track 骨架。WeightedScorer 把各類分數依配分正規化
（自由給分、不強制加總 100；內部計分邏輯固定不開放）—— 與 SectorEngine 共用。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..context import StockContext


class BaseRule(ABC):
    name: str = "rule"


class FilterRule(BaseRule):
    """硬篩規則：不通過即排除。"""

    @abstractmethod
    def passes(self, ctx: StockContext) -> bool: ...


class ScoreRule(BaseRule):
    """評分規則：一個類別給 0~100 分，並可附理由 chip。"""

    category: str = "misc"
    default_weight: float = 10.0

    @abstractmethod
    def score(self, ctx: StockContext) -> float:
        """回 0~100。"""

    def reason(self, ctx: StockContext, value: float) -> str | None:
        """分數夠高時回理由 chip 文字，否則 None。"""
        return None


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


class WeightedScorer:
    """依配分正規化加權（Track 與 SectorEngine 共用）。

    weights: {category: raw_weight}；total = Σ(score·w) / Σw，回 0~100。
    """

    @staticmethod
    def weighted_total(sub_scores: dict[str, float], weights: dict[str, float]) -> float:
        num = den = 0.0
        for cat, val in sub_scores.items():
            w = weights.get(cat, 0.0)
            num += val * w
            den += w
        return round(num / den, 2) if den else 0.0
