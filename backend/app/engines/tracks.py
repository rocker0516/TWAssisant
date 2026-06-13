"""雙軌 Track（架構③）。

Track.evaluate() 共骨架：硬篩 → 各類 0~100 → 配分換算 → 類股修正(P3) →
買賣區間/停損 → 理由。子類只給 track_key + filters + scorers + 預設門檻。
每檔每軌都產一列（詳情頁顯示用），passed 標記是否進推薦。
"""

from __future__ import annotations

from abc import ABC
from datetime import date

from .context import StockContext
from .rules.base import FilterRule, ScoreRule, WeightedScorer, score_confidence
from .rules.common import COMMON_FILTERS
from .rules.long import LONG_FILTERS, LONG_SCORERS
from .rules.wave import WAVE_FILTERS, WAVE_FILTERS_BREAKOUT, WAVE_FILTERS_PULLBACK, WAVE_SCORERS
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

    def _select_filters(self, config: dict) -> list[FilterRule]:
        """硬篩集合（子類可依 config 切換，如波段軌的進場風格）。"""
        return self.filters

    def _sector_adjust(self, ctx: StockContext) -> tuple[float, str | None]:
        """類股修正（設計定案）：波段=強勢加分/弱勢扣分但不排除；長線=輕加分。±5~10。"""
        sd = ctx.sector
        if sd is None:
            return 0.0, None
        strength = sd.strength_score if sd.strength_score is not None else 50.0
        if self.track_key == "wave":
            if strength >= 65 or sd.trend_short == "偏多":
                return 5.0, "類股偏多 +5"
            if strength <= 40 or sd.trend_short == "偏空":
                return -8.0, "類股偏空 −8"
            return 0.0, None
        # long：輕加分、不排除
        if sd.trend_long == "偏多":
            return 5.0, "類股中長多 +5"
        if sd.trend_long == "偏空":
            return -3.0, "類股中長空 −3"
        return 0.0, None

    def evaluate(self, ctx: StockContext, config: dict | None = None) -> dict:
        config = config or {}
        threshold = config.get("threshold", self.default_threshold)

        passed_filter = all(f.passes(ctx) for f in COMMON_FILTERS + self._select_filters(config))

        sub_scores: dict[str, float] = {}
        reasons: list[str] = []
        for sc in self.scorers:
            raw = sc.score(ctx)
            if raw is None:  # 資料不足：不灌 0，從加權剔除（總分只用有料維度，可信度反映缺口）
                continue
            val = float(round(raw, 1))
            sub_scores[sc.category] = val
            r = sc.reason(ctx, val)
            if r:
                reasons.append(r)

        weights = self._weights(config)
        total = float(WeightedScorer.weighted_total(sub_scores, weights))
        coverage, confidence = score_confidence(sub_scores, len(self.scorers))

        sector_adjust, sector_reason = self._sector_adjust(ctx)
        if sector_reason:
            reasons.append(sector_reason)
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
            "coverage": coverage,
            "confidence": confidence,
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

    def _select_filters(self, config: dict) -> list:
        """進場風格：breakout 突破追強(量增) / pullback 回檔低接(已回檔、不要求量增)。"""
        style = (config or {}).get("style", "breakout")
        return WAVE_FILTERS_PULLBACK if style == "pullback" else WAVE_FILTERS_BREAKOUT


class LongTrack(Track):
    track_key = "long"
    filters = LONG_FILTERS
    scorers = LONG_SCORERS
