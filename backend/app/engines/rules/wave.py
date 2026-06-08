"""波段軌規則（技術 + 籌碼）。

硬篩：站上20日線且上揚、量增(>5日均量×1.3)、距季線乖離<15%。
評分5類預設配分：趨勢25 / 動能25 / 量能20 / 籌碼20 / 型態10。
（內部計分邏輯固定不開放；配分可由 settings 調整。）
"""

from __future__ import annotations

from .base import FilterRule, ScoreRule, clamp
from ..context import StockContext

# ─────────────── 硬篩 ───────────────


class AboveRisingMa20(FilterRule):
    name = "above_rising_ma20"

    def passes(self, ctx: StockContext) -> bool:
        ind, prev = ctx.ind, ctx.ind_ago(5)
        if ind is None or ind.get("ma20") is None or ctx.close is None:
            return False
        above = ctx.close > ind["ma20"]
        rising = prev is not None and prev.get("ma20") is not None and ind["ma20"] > prev["ma20"]
        return above and rising


class VolumeIncrease(FilterRule):
    name = "volume_increase"

    def passes(self, ctx: StockContext) -> bool:
        ind = ctx.ind
        v5 = ind.get("vol_ma5") if ind is not None else None
        vol = float(ctx.prices["volume"].iloc[-1]) if ctx.n_bars else None
        return v5 is not None and vol is not None and vol > v5 * 1.3


class NearMa60(FilterRule):
    name = "near_ma60"

    def passes(self, ctx: StockContext) -> bool:
        ind = ctx.ind
        b = ind.get("bias_60") if ind is not None else None
        return b is not None and abs(b) < 15


WAVE_FILTERS: list[FilterRule] = [AboveRisingMa20(), VolumeIncrease(), NearMa60()]

# ─────────────── 評分 ───────────────


class TrendScore(ScoreRule):
    category = "trend"
    default_weight = 25.0

    def score(self, ctx: StockContext) -> float:
        ind, prev = ctx.ind, ctx.ind_ago(5)
        if ind is None:
            return 0.0
        ma5, ma10, ma20, ma60 = (ind.get(k) for k in ("ma5", "ma10", "ma20", "ma60"))
        s = 0.0
        if ma5 and ma10 and ma5 > ma10:
            s += 10
        if ma10 and ma20 and ma10 > ma20:
            s += 15
        if ma20 and ma60 and ma20 > ma60:
            s += 20
        if ma20 and ctx.close and ctx.close > ma20:
            s += 25
        if prev is not None and ma20 and prev.get("ma20") and ma20 > prev["ma20"]:
            s += 30
        return clamp(s)

    def reason(self, ctx, value):
        ind = ctx.ind
        if ind is not None and all(ind.get(k) for k in ("ma5", "ma10", "ma20", "ma60")):
            if ind["ma5"] > ind["ma10"] > ind["ma20"] > ind["ma60"]:
                return "均線多頭排列"
        return "站上月線且上揚" if value >= 60 else None


class MomentumScore(ScoreRule):
    category = "momentum"
    default_weight = 25.0

    def score(self, ctx: StockContext) -> float:
        ind, prev = ctx.ind, ctx.ind_ago(1)
        if ind is None:
            return 0.0
        s = 0.0
        macd, hist = ind.get("macd"), ind.get("macd_hist")
        k, d = ind.get("kd_k"), ind.get("kd_d")
        if macd is not None and macd > 0:
            s += 20
        if hist is not None and hist > 0:
            s += 15
        if prev is not None and hist is not None and prev.get("macd_hist") is not None and hist > prev["macd_hist"]:
            s += 15
        if k is not None and d is not None and k > d:
            s += 20
        if prev is not None and k is not None and prev.get("kd_k") is not None and k > prev["kd_k"]:
            s += 15
        if k is not None and 20 < k < 80:
            s += 15
        return clamp(s)

    def reason(self, ctx, value):
        ind = ctx.ind
        if ind is None:
            return None
        bits = []
        if ind.get("macd") is not None and ind["macd"] > 0 and ind.get("macd_hist", 0) > 0:
            bits.append("MACD 翻多")
        if ind.get("kd_k") is not None and ind.get("kd_d") is not None and ind["kd_k"] > ind["kd_d"]:
            bits.append("KD 黃金交叉")
        return "、".join(bits) if bits else None


class VolumeScore(ScoreRule):
    category = "volume"
    default_weight = 20.0

    def score(self, ctx: StockContext) -> float:
        ind = ctx.ind
        vma = ind.get("vol_ma20") if ind is not None else None
        vol = float(ctx.prices["volume"].iloc[-1]) if ctx.n_bars else None
        if not vma or vol is None:
            return 0.0
        ratio = vol / vma
        return clamp((ratio - 0.5) / 1.5 * 100)

    def reason(self, ctx, value):
        return "量能放大" if value >= 65 else None


class ChipScore(ScoreRule):
    category = "chip"
    default_weight = 20.0

    def score(self, ctx: StockContext) -> float:
        ind = ctx.ind
        vma_lots = (ind.get("vol_ma20") or 0) / 1000 if ind is not None else 0
        if vma_lots <= 0:
            return 50.0
        net5 = ctx.inst_sum("foreign_net", 5) + ctx.inst_sum("trust_net", 5)
        ratio = net5 / (vma_lots * 5)  # 近5日法人淨買佔5日量比例
        return clamp(50 + ratio * 500)

    def reason(self, ctx, value):
        f5 = ctx.inst_sum("foreign_net", 5)
        t5 = ctx.inst_sum("trust_net", 5)
        if f5 > 0 and t5 > 0:
            return "外資投信同步買超"
        if value >= 65:
            return "法人買超"
        return None


class PatternScore(ScoreRule):
    category = "pattern"
    default_weight = 10.0

    def score(self, ctx: StockContext) -> float:
        if ctx.n_bars < 2:
            return 0.0
        o = float(ctx.prices["open"].iloc[-1])
        c = ctx.close
        highs = ctx.prices["high"]
        high20 = float(highs.iloc[-20:].max())
        prev_high20 = float(highs.iloc[:-1].iloc[-20:].max()) if ctx.n_bars > 1 else high20
        s = 0.0
        if c is not None and c > o:
            s += 30
        if high20:
            s += clamp((c / high20 - 0.9) / 0.1 * 40, 0, 40)
        if prev_high20 and c is not None and c > prev_high20:
            s += 30
        return clamp(s)

    def reason(self, ctx, value):
        return "突破前高" if value >= 70 else None


WAVE_SCORERS: list[ScoreRule] = [
    TrendScore(),
    MomentumScore(),
    VolumeScore(),
    ChipScore(),
    PatternScore(),
]
