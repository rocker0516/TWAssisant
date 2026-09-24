"""Track（架構③）。長線軌已移除（2026-08-28），僅剩波段軌。

- 波段軌 WaveTrack：「會噴」，總分由 ScoringEngine 當天全市場橫截面 rank 算
  （見 WaveTrack.evaluate / scoring.py）；此處只算硬篩、原始 rank 輸入、
  6 因子 evidence 與買賣區間。
歷史上的 Score(track='long') 列仍在 DB（不刪資料），只是不再產生新列。
"""

from __future__ import annotations

from abc import ABC
from datetime import date

from .context import StockContext
from .rules.base import FilterRule, ScoreRule
from .rules.common import COMMON_FILTERS
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
