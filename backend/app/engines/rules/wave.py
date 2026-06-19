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


class PulledBack(FilterRule):
    """已回檔：收盤落在近20日『收盤』區間下緣，且未過熱、未過度延伸（非追高）。

    刻意用『收盤』而非盤中高低算區間——盤中一根影線/急拉會把區間撐大，讓剛噴上去
    貼著高點的股票偽裝成中位（實測 興富發 盤中 pir 0.64 但收盤 pir 0.86）。再加
    KD 未過熱、距季線乖離未過大兩道守門，擋掉「剛拉一波、過熱、過度延伸」的假回檔。
    回檔風格用，刻意不要求量增（縮量回測常見）。
    """

    name = "pulled_back"
    _MAX_PIR = 0.5      # 收盤區間位階上限：>0.5 視為仍在中上緣、非回檔低接
    _MAX_KD = 75.0      # KD 過熱上限：回檔買點不該追在過熱區
    _MAX_BIAS60 = 12.0  # 距季線乖離上限（%）：過度延伸非回檔

    def passes(self, ctx: StockContext) -> bool:
        if ctx.close is None or ctx.n_bars < 20:
            return False
        closes = ctx.prices["close"].iloc[-20:]
        hi, lo = float(closes.max()), float(closes.min())
        rng = hi - lo
        if rng <= 0:
            return False
        if (ctx.close - lo) / rng > self._MAX_PIR:
            return False
        ind = ctx.ind
        if ind is not None:
            k = ind.get("kd_k")
            if k is not None and k >= self._MAX_KD:
                return False
            b = ind.get("bias_60")
            if b is not None and abs(b) >= self._MAX_BIAS60:
                return False
        return True


# 突破追強（預設）：站上上揚月線 + 量增 + 距季線<15%
WAVE_FILTERS_BREAKOUT: list[FilterRule] = [AboveRisingMa20(), VolumeIncrease(), NearMa60()]
# 回檔低接：站上上揚月線(守支撐) + 已回檔到區間下緣 + 距季線<15%；不要求量增（縮量回測常見）
WAVE_FILTERS_PULLBACK: list[FilterRule] = [AboveRisingMa20(), PulledBack(), NearMa60()]
WAVE_FILTERS: list[FilterRule] = WAVE_FILTERS_BREAKOUT  # 向後相容

# ─────────────── 評分 ───────────────


class TrendScore(ScoreRule):
    category = "trend"
    default_weight = 25.0

    def score(self, ctx: StockContext) -> float | None:
        ind, prev = ctx.ind, ctx.ind_ago(5)
        if ind is None:
            return None
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

    def evidence(self, ctx, value):
        ind = ctx.ind
        if ind is None or ind.get("ma20") is None:
            return None
        ma20 = ind["ma20"]
        ma5, ma10, ma60 = ind.get("ma5"), ind.get("ma10"), ind.get("ma60")
        aligned = all(x is not None for x in (ma5, ma10, ma60)) and ma5 > ma10 > ma20 > ma60
        head = "均線多頭排列" if aligned else ("站上月線" if ctx.close and ctx.close > ma20 else "月線下方")
        prev = ctx.ind_ago(5)
        rising = prev is not None and prev.get("ma20") is not None and ma20 > prev["ma20"]
        return f"{head}，月線 {ma20:.2f}{'（上揚）' if rising else ''}"


class MomentumScore(ScoreRule):
    category = "momentum"
    default_weight = 25.0

    def score(self, ctx: StockContext) -> float | None:
        ind, prev = ctx.ind, ctx.ind_ago(1)
        if ind is None:
            return None
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

    def evidence(self, ctx, value):
        ind = ctx.ind
        if ind is None:
            return None
        bits = []
        macd, hist = ind.get("macd"), ind.get("macd_hist")
        k, d = ind.get("kd_k"), ind.get("kd_d")
        if macd is not None:
            bits.append("MACD翻多" if (macd > 0 and (hist or 0) > 0) else "MACD未翻多")
        if k is not None and d is not None:
            bits.append(f"KD{'黃金交叉' if k > d else '死叉'}（K{k:.0f}）")
        return "、".join(bits) if bits else None


class VolumeScore(ScoreRule):
    category = "volume"
    default_weight = 20.0

    def score(self, ctx: StockContext) -> float | None:
        ind = ctx.ind
        vma = ind.get("vol_ma20") if ind is not None else None
        vol = float(ctx.prices["volume"].iloc[-1]) if ctx.n_bars else None
        if not vma or vol is None:
            return None
        ratio = vol / vma
        return clamp((ratio - 0.5) / 1.5 * 100)

    def reason(self, ctx, value):
        return "量能放大" if value >= 65 else None

    def evidence(self, ctx, value):
        ind = ctx.ind
        vma = ind.get("vol_ma20") if ind is not None else None
        vol = float(ctx.prices["volume"].iloc[-1]) if ctx.n_bars else None
        if not vma or vol is None:
            return None
        ratio = vol / vma
        return f"{'量能放大' if ratio >= 1 else '量縮'} {ratio:.1f} 倍均量"


class ChipScore(ScoreRule):
    category = "chip"
    default_weight = 20.0

    @staticmethod
    def _holder_nudge(ctx: StockContext) -> float:
        """大戶（≥400 張）占比『近月趨勢』輕推（±12）：籌碼往大戶集中視為偏多。

        刻意只用趨勢、不用絕對占比——高占比常是結構性（外資保管行/單一大股東），不代表
        多空。集保為週資料、來源只給最新快照，故史料不足（無趨勢）時回 0、不動既有分數，
        待每週累積出歷史後此訊號才生效（避免一上線就擾動已校準的波段軌）。
        """
        trend = ctx.holding_trend("big_pct", 4)
        if trend is None:
            return 0.0
        return clamp(trend * 4, -12, 12)

    def score(self, ctx: StockContext) -> float | None:
        ind = ctx.ind
        vma_lots = (ind.get("vol_ma20") or 0) / 1000 if ind is not None else 0
        if vma_lots <= 0:
            base = 50.0  # 量能基準缺：法人佔比不可算，給中性（非缺料剔除，維持籌碼維度存在）
        else:
            # 短期(5日)+持續(20日累計)法人淨買佔量比例 6:4 混合——兼顧近日動向與「累計
            # 偷偷進/出貨」趨勢（即籌碼趨勢圖那條累計線）。
            ratio5 = (ctx.inst_sum("foreign_net", 5) + ctx.inst_sum("trust_net", 5)) / (vma_lots * 5)
            ratio20 = (ctx.inst_sum("foreign_net", 20) + ctx.inst_sum("trust_net", 20)) / (vma_lots * 20)
            base = clamp(50 + (0.6 * ratio5 + 0.4 * ratio20) * 500)
        return clamp(base + self._holder_nudge(ctx))

    def reason(self, ctx, value):
        f5 = ctx.inst_sum("foreign_net", 5)
        t5 = ctx.inst_sum("trust_net", 5)
        if f5 > 0 and t5 > 0:
            return "外資投信同步買超"
        if (ctx.holding_trend("big_pct", 4) or 0) >= 0.5:
            return "大戶持股增加"
        if value >= 65:
            return "法人買超"
        return None

    def evidence(self, ctx, value):
        f5 = ctx.inst_sum("foreign_net", 5)
        t5 = ctx.inst_sum("trust_net", 5)
        parts = []
        if abs(f5) >= 1:
            parts.append(f"外資5日{'買' if f5 > 0 else '賣'}超 {abs(int(round(f5))):,} 張")
        if abs(t5) >= 1:
            parts.append(f"投信{'買' if t5 > 0 else '賣'}超 {abs(int(round(t5))):,} 張")
        f20 = ctx.inst_sum("foreign_net", 20) + ctx.inst_sum("trust_net", 20)
        if abs(f20) >= 1:
            parts.append(f"法人20日累計{'買' if f20 > 0 else '賣'}超 {abs(int(round(f20))):,} 張")
        big = ctx.holding_latest("big_pct")
        if big is not None:
            seg = f"大戶持股 {big:.0f}%"
            trend = ctx.holding_trend("big_pct", 4)
            if trend is not None and abs(trend) >= 0.3:
                seg += f"（近月{'增' if trend > 0 else '減'} {abs(trend):.1f} 個百分點）"
            parts.append(seg)
        return "、".join(parts) if parts else "法人無明顯進出"


class MarginScore(ScoreRule):
    """融資融券籌碼面：融資餘額近月變化 + 券資比。

    籌碼面常識讀法——上升趨勢中『融資增=散戶槓桿追高、籌碼鬆動（偏空）；融資減/沉澱=
    籌碼乾淨惜售（偏多）』；券資比高=空單累積、有軋空潛力（小幅偏多）。門檻仍是 heuristic
    常數（Phase 2 單因子 IC 會用歷史報酬相關性決定此因子權重，沒預測力自然被降權）。
    """

    category = "margin"
    default_weight = 10.0
    _CHG_K = 1.5      # 融資 20 日變化率 → 分數斜率（每 +1% 融資扣 1.5 分）
    _CHG_CAP = 30.0   # 融資變化貢獻上下限
    _SR_K = 0.8       # 券資比 → 加分斜率
    _SR_CAP = 12.0    # 券資比加分上限

    def score(self, ctx: StockContext) -> float | None:
        chg = ctx.margin_change_pct(20)
        if chg is None:
            return None  # 無融資資料（如多數 ETF）：缺料剔除、不灌中性
        s = 50.0 - clamp(chg * self._CHG_K, -self._CHG_CAP, self._CHG_CAP)
        sr = ctx.short_margin_ratio()
        if sr is not None:
            s += clamp(sr * self._SR_K, 0.0, self._SR_CAP)
        return clamp(s)

    def reason(self, ctx, value):
        chg = ctx.margin_change_pct(20)
        if chg is not None and chg <= -8:
            return "融資退場籌碼沉澱"
        return None

    def evidence(self, ctx, value):
        chg = ctx.margin_change_pct(20)
        if chg is None:
            return None
        parts = [f"融資餘額20日{'增' if chg > 0 else '減'} {abs(chg):.0f}%"]
        sr = ctx.short_margin_ratio()
        if sr is not None and sr >= 1:
            parts.append(f"券資比 {sr:.0f}%")
        return "、".join(parts)


class PatternScore(ScoreRule):
    category = "pattern"
    default_weight = 10.0

    def score(self, ctx: StockContext) -> float | None:
        if ctx.n_bars < 2:
            return None
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

    def evidence(self, ctx, value):
        if ctx.n_bars < 2 or ctx.close is None:
            return None
        c = ctx.close
        prev_high20 = float(ctx.prices["high"].iloc[:-1].iloc[-20:].max())
        if prev_high20 <= 0:
            return None
        if c > prev_high20:
            return f"突破前高 {prev_high20:.2f}"
        gap = (prev_high20 - c) / c * 100
        return f"距前高 {prev_high20:.2f}（{gap:.0f}%）"


class PositionScore(ScoreRule):
    """位階/回檔分：上升趨勢中，獎勵『相對低位、貼近支撐、未過熱』的回檔買點。

    與 PatternScore（突破/追強）刻意對立——配分由使用者調整：想做回檔低接就把『位階』
    調高、『型態(突破)』調低。趨勢成立由硬篩(站上上揚月線)保證，此處只問『買在相對低
    還是已噴出』：區間位階低、乖離小(貼月線)、KD 未過熱者得分高。
    """

    category = "position"
    default_weight = 15.0

    def score(self, ctx: StockContext) -> float | None:
        ind = ctx.ind
        if ind is None or ctx.close is None or ctx.n_bars < 20:
            return None
        c = ctx.close
        highs = ctx.prices["high"].iloc[-20:]
        lows = ctx.prices["low"].iloc[-20:]
        hi, lo = float(highs.max()), float(lows.min())
        rng = hi - lo
        pir = (c - lo) / rng if rng > 0 else 0.5  # 區間位階：0=區間低 1=區間高
        s = clamp((1.0 - pir) * 60, 0, 60)  # 越低位分越高（最多 60）
        b = ind.get("bias_20")
        if b is not None:
            if b <= 4:
                s += 25  # 貼月線、剛拉回
            elif b <= 8:
                s += 12
        k = ind.get("kd_k")
        if k is not None:
            if k < 50:
                s += 15  # 未過熱、低檔翻揚空間大
            elif k < 70:
                s += 8
        return clamp(s)

    def reason(self, ctx, value):
        return "回檔相對低位" if value >= 60 else None

    def evidence(self, ctx, value):
        ind = ctx.ind
        if ind is None or ctx.close is None or ctx.n_bars < 20:
            return None
        c = ctx.close
        hi = float(ctx.prices["high"].iloc[-20:].max())
        lo = float(ctx.prices["low"].iloc[-20:].min())
        rng = hi - lo
        pir = (c - lo) / rng if rng > 0 else 0.5
        parts = ["區間低位" if pir <= 0.4 else ("區間中位" if pir <= 0.65 else "區間高位")]
        b = ind.get("bias_20")
        if b is not None:
            parts.append(f"乖離月線 {b:.0f}%")
        k = ind.get("kd_k")
        if k is not None:
            parts.append("KD未過熱" if k < 70 else "KD偏高")
        return "、".join(parts)


WAVE_SCORERS: list[ScoreRule] = [
    TrendScore(),
    MomentumScore(),
    VolumeScore(),
    ChipScore(),
    MarginScore(),
    PatternScore(),
    PositionScore(),
]
