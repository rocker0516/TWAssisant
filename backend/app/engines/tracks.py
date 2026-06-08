"""雙軌 Track（架構③）。

Track.evaluate() 共骨架：硬篩 → 各類 0~100 → 配分換算 → 類股修正(P3) →
買賣區間/停損 → 理由。子類只給 track_key + filters + scorers + 預設門檻。
每檔每軌都產一列（詳情頁顯示用），passed 標記是否進推薦。
"""

from __future__ import annotations

from abc import ABC
from datetime import date

from .context import StockContext
from .rules.base import FilterRule, ScoreRule, WeightedScorer
from .rules.common import COMMON_FILTERS
from .rules.long import LONG_FILTERS, LONG_SCORERS
from .rules.wave import WAVE_FILTERS, WAVE_SCORERS
from .stoploss import StopLossCalculator

_DEFAULT_THRESHOLD = 70.0


class Track(ABC):
    track_key: str = "base"
    filters: list[FilterRule] = []
    scorers: list[ScoreRule] = []
    default_threshold: float = _DEFAULT_THRESHOLD

    def __init__(self, stoploss: StopLossCalculator | None = None) -> None:
        self._stoploss = stoploss or StopLossCalculator()
        self._all_filters = COMMON_FILTERS + self.filters

    def _weights(self, config: dict) -> dict[str, float]:
        overrides = (config or {}).get("weights", {})
        return {s.category: overrides.get(s.category, s.default_weight) for s in self.scorers}

    def evaluate(self, ctx: StockContext, config: dict | None = None) -> dict:
        config = config or {}
        threshold = config.get("threshold", self.default_threshold)

        passed_filter = all(f.passes(ctx) for f in self._all_filters)

        sub_scores: dict[str, float] = {}
        reasons: list[str] = []
        for sc in self.scorers:
            val = float(round(sc.score(ctx), 1))
            sub_scores[sc.category] = val
            r = sc.reason(ctx, val)
            if r:
                reasons.append(r)

        weights = self._weights(config)
        total = float(WeightedScorer.weighted_total(sub_scores, weights))

        sector_adjust = 0.0  # P3 SectorEngine 接入
        total = float(round(min(100.0, max(0.0, total + sector_adjust)), 2))

        plan = self._stoploss.compute(ctx, self.track_key)
        passed = passed_filter and total >= threshold

        return {
            "stock_id": ctx.stock.id,
            "date": ctx.date,
            "track": self.track_key,
            "passed_filter": passed_filter,
            "passed": passed,
            "total_score": total,
            "sub_scores": sub_scores,
            "sector_adjust": sector_adjust,
            "buy_low": plan.buy_low,
            "buy_high": plan.buy_high,
            "stop_loss": plan.stop_loss,
            "loss_pct": plan.loss_pct,
            "reasons": reasons,
        }


class WaveTrack(Track):
    track_key = "wave"
    filters = WAVE_FILTERS
    scorers = WAVE_SCORERS


class LongTrack(Track):
    track_key = "long"
    filters = LONG_FILTERS
    scorers = LONG_SCORERS
