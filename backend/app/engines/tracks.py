"""雙軌 Track（架構③）。

- 長線軌 LongTrack：沿用共骨架 evaluate()——硬篩 → 各類 0~100 → 配分加權 →
  類股修正 → 買賣區間/停損 → 理由。
- 波段軌 WaveTrack：重定錨為「會噴」，總分改由 ScoringEngine 當天全市場橫截面
  rank 算（見 WaveTrack.evaluate / scoring.py）；此處只算硬篩、原始 rank 輸入、
  6 因子 evidence 與買賣區間。
每檔每軌都產一列（詳情頁顯示用），passed 標記是否進推薦。
"""

from __future__ import annotations

from abc import ABC
from datetime import date

from .context import StockContext
from .rules.base import FilterRule, ScoreRule, WeightedScorer, score_confidence
from .rules.common import COMMON_FILTERS
from .rules.long import LONG_FILTERS, LONG_SCORERS
from .rules.wave import (
    WAVE_FILTERS, WAVE_SCORERS, crash_cand_ok, explosive_ok, hyst_inputs,
    pop_atr_pct, pop_ma_align, pop_pb, pop_pos_52w, story_ok, strong_ok,
)
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
        """單一配分加權（長線軌）。波段軌覆寫此法改走會噴 rank。"""
        config = config or {}
        threshold = config.get("threshold", self.default_threshold)

        common_ok = all(f.passes(ctx) for f in COMMON_FILTERS)
        passed_filter = common_ok and all(f.passes(ctx) for f in self.filters)

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

        sector_adjust, sector_reason = self._sector_adjust(ctx)
        if sector_reason:
            reasons.append(sector_reason)

        weights = self._weights(config)
        total = float(WeightedScorer.weighted_total(sub_scores, weights))
        total = float(round(min(100.0, max(0.0, total + sector_adjust)), 2))
        coverage, confidence = score_confidence(sub_scores, len(self.scorers), weights)

        plan = self._stoploss.compute(ctx, self.track_key)
        passed = passed_filter and total >= threshold

        return {
            "stock_id": ctx.stock.id,
            "date": ctx.date,
            "track": self.track_key,
            "passed_filter": passed_filter,
            "strict_filter": passed_filter,  # 長線無遲滯，與原始硬篩相同（批次 upsert 欄位鍵需一致）
            "passed_styles": [],  # 風格只在波段軌有意義（批次 upsert 欄位鍵需一致）
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
            "details": details,
        }


class WaveTrack(Track):
    """波段軌＝會噴。total_score 不在此算——逐檔吐 pop_inputs(atr_pct, ma_align)，
    由 ScoringEngine 當天全市場橫截面 rank 合成。6 因子只算 evidence 供參考。"""

    track_key = "wave"
    filters = WAVE_FILTERS
    scorers = WAVE_SCORERS

    def evaluate(self, ctx: StockContext, config: dict | None = None) -> dict:
        common_ok = all(f.passes(ctx) for f in COMMON_FILTERS)
        strict = common_ok and all(f.passes(ctx) for f in self.filters)

        # 6 因子：不計入會噴分數，只攤成 evidence/sub_scores 供個股詳情參考。
        sub_scores: dict[str, float] = {}
        reasons: list[str] = []
        details: list[dict] = []
        for sc in self.scorers:
            raw = sc.score(ctx)
            if raw is None:
                continue
            val = float(round(raw, 1))
            sub_scores[sc.category] = val
            r = sc.reason(ctx, val)
            if r:
                reasons.append(r)
            details.append({"category": sc.category, "score": val, "evidence": sc.evidence(ctx, val)})

        plan = self._stoploss.compute(ctx, self.track_key)
        return {
            "stock_id": ctx.stock.id,
            "date": ctx.date,
            "track": self.track_key,
            # passed_filter 先放當日原始硬篩；ScoringEngine 再用昨日狀態套遲滯改寫
            "passed_filter": strict,
            "strict_filter": strict,
            # 爆發風格：極高波動+上揚月線、不看季線乖離（與會噴硬篩獨立，無遲滯）
            # 各風格純門檻互相獨立；crash_cand 為深跌反攻的個股端，市場端由引擎補判
            "passed_styles": ([] if not common_ok else [
                st for st, ok in (
                    ("explosive", explosive_ok(ctx)), ("strong", strong_ok(ctx)),
                    ("story", story_ok(ctx)), ("crash_cand", crash_cand_ok(ctx)),
                ) if ok
            ]),
            "hyst_inputs": hyst_inputs(ctx, strict),  # transient，引擎用完即拔
            "passed": False,        # 引擎橫截面 rank 後再定
            "total_score": None,    # 引擎填（會噴 rank 分數）
            "pop_inputs": {"atr_pct": pop_atr_pct(ctx), "ma_align": pop_ma_align(ctx),
                           "pos_52w": pop_pos_52w(ctx), "pb": pop_pb(ctx)},
            "sub_scores": sub_scores,
            "sector_adjust": 0.0,   # 會噴分數＝純 rank，不加類股修正（與回測一致）
            "coverage": None,       # 引擎填
            "confidence": None,     # 引擎填
            "buy_low": plan.buy_low,
            "buy_high": plan.buy_high,
            "stop_loss": plan.stop_loss,
            "loss_pct": plan.loss_pct,
            "reasons": reasons,
            "details": details,
        }


class LongTrack(Track):
    track_key = "long"
    filters = LONG_FILTERS
    scorers = LONG_SCORERS
