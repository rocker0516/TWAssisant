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

    def style_weights(self) -> dict[str, dict[str, float]]:
        """各風格的配分覆寫（相對預設只蓋差異的維度）。預設無，子類可給。"""
        return {}

    def _weights_for(self, style: str, config: dict) -> dict[str, float]:
        """某風格的最終配分 = 預設/設定配分 ← 風格內建覆寫 ← 設定的 weights_<style> 覆寫。"""
        base = self._weights(config)
        merged = {**base, **self.style_weights().get(style, {})}
        cfg_over = (config or {}).get(f"weights_{style}", {})
        merged.update(cfg_over)
        return {c: float(merged.get(c, base.get(c, 0.0))) for c in base}

    def styles(self) -> list[tuple[str, list[FilterRule]]]:
        """此軌支援的進場風格 → (風格名, 該風格專屬硬篩)。

        預設單一 default；子類可給多種（如波段軌 breakout/pullback）。評分與風格無關，
        差別只在『過哪一組硬篩』——批次時逐風格判定，存 passed_styles 供前端切換。
        """
        return [("default", self.filters)]

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

        # 共用硬篩過了才逐風格判定（評分與風格無關，只差過哪組風格硬篩）
        common_ok = all(f.passes(ctx) for f in COMMON_FILTERS)
        passed_styles = (
            [name for name, filt in self.styles() if all(f.passes(ctx) for f in filt)]
            if common_ok
            else []
        )
        passed_filter = len(passed_styles) > 0

        sub_scores: dict[str, float] = {}
        reasons: list[str] = []
        details: list[dict] = []  # 展開區：各面向分數 + 帶數字證據
        for sc in self.scorers:
            raw = sc.score(ctx)
            if raw is None:  # 資料不足：不灌 0，從加權剔除（總分只用有料維度，可信度反映缺口）
                continue
            val = float(round(raw, 1))
            sub_scores[sc.category] = val
            r = sc.reason(ctx, val)
            if r:
                reasons.append(r)
            details.append({"category": sc.category, "score": val, "evidence": sc.evidence(ctx, val)})

        coverage, confidence = score_confidence(sub_scores, len(self.scorers))
        sector_adjust, sector_reason = self._sector_adjust(ctx)
        if sector_reason:
            reasons.append(sector_reason)

        def _weighted(w: dict[str, float]) -> float:
            t = float(WeightedScorer.weighted_total(sub_scores, w))
            return float(round(min(100.0, max(0.0, t + sector_adjust)), 2))

        # 每個風格用各自配分算總分（sub_scores 與風格無關，只有加權總分會變）
        style_totals = {name: _weighted(self._weights_for(name, config)) for name, _ in self.styles()}
        primary = self.styles()[0][0]  # 主風格（波段=breakout）總分當作 row 的 total_score（向後相容）
        total = style_totals.get(primary, _weighted(self._weights(config)))

        plan = self._stoploss.compute(ctx, self.track_key)
        passed = passed_filter and total >= threshold

        return {
            "stock_id": ctx.stock.id,
            "date": ctx.date,
            "track": self.track_key,
            "passed_filter": passed_filter,
            "passed": passed,
            "passed_styles": passed_styles,
            "total_score": total,
            "style_totals": style_totals,
            "sub_scores": sub_scores,
            "sector_adjust": sector_adjust,
            "coverage": coverage,
            "confidence": confidence,
            "buy_low": plan.buy_low,
            "buy_high": plan.buy_high,
            "stop_loss": plan.stop_loss,
            "loss_pct": plan.loss_pct,
            "reasons": reasons,
            "details": details,
        }


class WaveTrack(Track):
    track_key = "wave"
    filters = WAVE_FILTERS
    scorers = WAVE_SCORERS

    # 回檔低接專屬配分：位階(買在相對低)主導、突破型態與量能放大降權、動能略降避免追過熱；
    # 趨勢/籌碼維持（上升趨勢與法人支撐仍重要）。breakout 用預設配分。
    _PULLBACK_WEIGHTS = {"trend": 25.0, "momentum": 20.0, "volume": 10.0, "chip": 20.0, "margin": 10.0, "pattern": 5.0, "position": 35.0}

    def styles(self) -> list[tuple[str, list]]:
        """進場風格：breakout 突破追強(量增) / pullback 回檔低接(已回檔、不要求量增)。

        兩風格共用同一套各因子原始分數，差別在硬篩 + 加權配分（回檔低接位階主導）。
        批次逐風格算總分(style_totals)，前端可即時切換。
        """
        return [("breakout", WAVE_FILTERS_BREAKOUT), ("pullback", WAVE_FILTERS_PULLBACK)]

    def style_weights(self) -> dict[str, dict[str, float]]:
        return {"pullback": self._PULLBACK_WEIGHTS}


class LongTrack(Track):
    track_key = "long"
    filters = LONG_FILTERS
    scorers = LONG_SCORERS
