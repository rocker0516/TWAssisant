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
    def score(self, ctx: StockContext) -> float | None:
        """回 0~100；資料不足回 None（不灌 0，從加權分母剔除，並反映在可信度）。"""

    def reason(self, ctx: StockContext, value: float) -> str | None:
        """分數夠高時回理由 chip 文字，否則 None。"""
        return None

    def evidence(self, ctx: StockContext, value: float) -> str | None:
        """展開區用：不論分數高低，回一句『帶數字』的客觀證據描述，否則 None。

        與 reason 不同——reason 只在達標時給「賣點」標籤；evidence 是把引擎已算出
        但平常丟掉的數字（買超張數、量能倍數、突破價、乖離）攤開，給展開詳情當證據。
        """
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


def score_confidence(
    sub_scores: dict[str, float],
    total_categories: int,
    weights: dict[str, float] | None = None,
) -> tuple[float, float]:
    """分數可信度 = 資料完整度 × 共識度，回 (coverage 0~1, confidence 0~100)。

    coverage = 有資料的維度 / 應有維度（缺料的 scorer 回 None 已被剔除）。
    consensus = 1 − 子分數母體標準差/40（夾 0~1）：各面向越一致越可信，
    單一維度灌爆則離散度大、可信度低。衡量「這個分數可不可信」，非看多程度
    ——全面偏弱但有料且一致，仍是高可信（可信地說它弱）。

    weights：某風格的配分。給了就**只看權重>0的維度**算完整度與共識度——因為
    風格刻意不押的維度（如「會噴」零權的籌碼/位階）本就無關，不該污染可信度。
    不給（None）= 看全部維度（向後相容，breakout/pullback 全維度有權即等價）。
    """
    if weights is not None:
        active = {c for c, w in weights.items() if w > 0}
        sub_scores = {c: v for c, v in sub_scores.items() if c in active}
        total_categories = len(active)
    present = len(sub_scores)
    if total_categories <= 0 or present == 0:
        return 0.0, 0.0
    coverage = present / total_categories
    vals = list(sub_scores.values())
    mean = sum(vals) / present
    std = (sum((v - mean) ** 2 for v in vals) / present) ** 0.5
    consensus = clamp(1.0 - std / 40.0, 0.0, 1.0)
    return round(coverage, 3), round(100.0 * coverage * consensus, 1)
