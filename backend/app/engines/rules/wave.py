"""波段軌規則 — 重定錨為「會噴」（docs/poppability-finding.md 定版）。

進場推薦地基＝會噴（持有期間摸到 +10% 可賣停利點），非會漲（收盤更高）。
- 硬篩：站上上揚月線（趨勢成立）＋ 距季線乖離<15%（非過度延伸）。不要求量增。
- 會噴分數＝橫截面百分位 `(2×rank(atr_pct) + rank(ma_align)) / 3 × 100`，由
  ScoringEngine 當天全市場一起算（rank 自動隨大盤波動水位正規化）。本檔只提供
  逐檔原始值 `pop_atr_pct()` / `pop_ma_align()`。
- 其餘 6 因子（動能/量能/籌碼/融資/型態/位階）**不計入會噴分數**，僅保留
  evidence/sub_scores 供個股詳情參考。
"""

from __future__ import annotations

from .base import FilterRule, ScoreRule, clamp
from ..context import StockContext


def pop_atr_pct(ctx: StockContext) -> float | None:
    """會噴主因子：日均波幅比 atr_pct = atr14/close。缺料回 None。"""
    ind = ctx.ind
    atr = ind.get("atr14") if ind is not None else None
    if atr is None or ctx.close is None or ctx.close <= 0:
        return None
    return float(atr / ctx.close)


def pop_ma_align(ctx: StockContext) -> float | None:
    """會噴方向因子：均線多頭排列數 (ma5>ma10)+(ma10>ma20)+(ma20>ma60) ∈ 0~3。

    doc 證實這是唯一在波動之上把振幅『偏向上』的因子（skew IC t=4.4）。任一均線
    缺料回 None（資料不足，不灌 0）。
    """
    ind = ctx.ind
    if ind is None:
        return None
    ma5, ma10, ma20, ma60 = (ind.get(k) for k in ("ma5", "ma10", "ma20", "ma60"))
    if ma5 is None or ma10 is None or ma20 is None or ma60 is None:
        return None
    return float((ma5 > ma10) + (ma10 > ma20) + (ma20 > ma60))

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


class NearMa60(FilterRule):
    name = "near_ma60"

    def passes(self, ctx: StockContext) -> bool:
        ind = ctx.ind
        b = ind.get("bias_60") if ind is not None else None
        return b is not None and abs(b) < 15


# 會噴硬篩：站上上揚月線（趨勢成立）+ 距季線<15%（非過度延伸）；不要求量增。
WAVE_FILTERS: list[FilterRule] = [AboveRisingMa20(), NearMa60()]

# ─────────────── 遲滯（去抖動，scripts/pop_hysteresis_backtest.py 定版）───────────────
# 硬篩是二元開關、切在雜訊最大處（月線附近震盪股天天翻面）→ 榜單日換血 40%。
# 遲滯=進榜嚴、出榜鬆：3 年三段 walk-forward，寬限股命中率 42.4% > 清單均值 37.9%
# （三段皆高）、日存活率 58.5%→69.1%，穩定性等於免費。
HYST_ENTER_MARGIN = 0.01  # 進榜（新股）：硬篩全過 且 收盤 > 月線×(1+margin)
HYST_HARD_BREAK = 0.02    # 出榜（在榜）：單日收盤 < 月線×(1−break) 立即踢
# 軟出榜＝原始硬篩「連 2 天」不滿足（單日失守寬限）——由 ScoringEngine 拿昨日
# strict_filter 判定；此處只吐當日輸入。


def hyst_inputs(ctx: StockContext, strict: bool) -> dict:
    """遲滯狀態機的當日輸入：enter_ok（可新進榜）/ hard_break（大破線立即出榜）。

    缺料（無收盤或月線）視同 hard_break——資料斷了不硬留在榜上。
    """
    ind = ctx.ind
    ma20 = ind.get("ma20") if ind is not None else None
    c = ctx.close
    if c is None or ma20 is None or ma20 <= 0:
        return {"enter_ok": False, "hard_break": True}
    return {
        "enter_ok": bool(strict and c > ma20 * (1.0 + HYST_ENTER_MARGIN)),
        "hard_break": bool(c < ma20 * (1.0 - HYST_HARD_BREAK)),
    }

# ─────────────── 評分（僅供 evidence 參考，不計入會噴分數）───────────────


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
        if k is not None:
            # KD 中場甜蜜區(距 50 越近分越高)：k=50 得 15、k=0/100 為 0。
            # 原本 `20<k<80 → +15、否則 0` 是階梯：k=79 得 15、k=81 得 0 只差 2 卻懸崖。
            # 換連續 tent 函數後市場 regime 漂移(整體 KD 中樞 40 或 60)不用重調門檻。
            s += clamp(15 * (1 - abs(k - 50) / 50), 0, 15)
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
            # 越貼月線越好：|b|=0 得 25、|b|≥20% 得 0，連續衰減去階梯。
            # 原 `b<=4 → 25、b<=8 → 12、其餘 0` 換 tent：b=4 得 20、b=8 得 15。
            s += clamp((20 - abs(b)) / 20 * 25, 0, 25)
        k = ind.get("kd_k")
        if k is not None:
            # KD 越低分越高：k=0 得 15、k≥85 得 0，連續衰減去階梯。
            s += clamp((85 - k) / 85 * 15, 0, 15)
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


class VolatilityScore(ScoreRule):
    """波動度（會噴潛力）：atr_pct = atr14/close 越高，持有期間摸到停利點的機率越高。

    定版實證（docs/poppability-finding.md）：用「未來20日收盤報酬」選股沒有穩定 alpha，
    但改問「持有期間有沒有漲到 +10% 可賣點(MFE)」，波動度是最強且樣本外最穩的預測子
    （最高十分位摸+10%機率 57.8% vs 全市場 35.6%，1.62 倍）。注意這是**雙面刃**：會噴的
    也會崩，波動本身對「噴」與「崩」近乎對稱，方向性的上偏交給趨勢(均線多排)那一維。
    預設權重 0：只有 poppable(會噴) 風格才把它加重；不擾動 breakout/pullback。
    """

    category = "volatility"
    default_weight = 0.0
    _LO = 0.012   # atr_pct 約 p10 → 0 分
    _SPAN = 0.058  # 到 ~p90(0.07) → 100 分

    def score(self, ctx: StockContext) -> float | None:
        ind = ctx.ind
        atr = ind.get("atr14") if ind is not None else None
        if atr is None or ctx.close is None or ctx.close <= 0:
            return None
        return clamp((atr / ctx.close - self._LO) / self._SPAN * 100)

    def reason(self, ctx, value):
        return "高波動易噴" if value >= 65 else None

    def evidence(self, ctx, value):
        ind = ctx.ind
        atr = ind.get("atr14") if ind is not None else None
        if atr is None or ctx.close is None or ctx.close <= 0:
            return None
        ap = atr / ctx.close * 100
        tier = "高波動（易噴亦易崩）" if ap >= 5 else ("中波動" if ap >= 3 else "低波動（不易噴）")
        return f"{tier}，日均波幅約 {ap:.1f}%"


class ConsolidationScore(ScoreRule):
    """低檔盤整打底（會噴前的彈簧）：**區間低位** + 近10日振幅相對近40日收斂 + 橫向走平 + 量縮。

    使用者要的是『低位』盤整，故「打底」**硬性要求區間位階偏低**——中位/高位就算振幅收斂也不
    算打底（高位橫盤多是漲多後的換手，非低接點）。低位以近20日(收盤)區間位階 pir 判：pir<0.4
    完整給分、0.4~0.5 折半、≥0.5(中位以上)壓到 0.2x 不過門檻。盤整強度＝振幅收斂(≤70)＋橫向
    走平(≤15)＋量縮(15)，再乘低位係數。注意此分**獨立於** PositionScore(位階)——後者把『貼月線
    ＋KD未過熱』也算進去，會把中位股灌成「相對低」(如欣銓 pir0.51 卻得 62)，不能拿來判低位。
    預設權重 0：只落 sub_scores/evidence 供前端『低位盤整』軟篩與回測子集，不擾動會噴 rank。
    """

    category = "consolidation"
    default_weight = 0.0
    _SHORT = 10
    _LONG = 40

    def _pir(self, ctx: StockContext) -> float | None:
        """近 20 日(收盤)區間位階：0=區間低、1=區間高。缺料回 None。"""
        if ctx.n_bars < 20 or ctx.close is None:
            return None
        hi = float(ctx.prices["high"].iloc[-20:].max())
        lo = float(ctx.prices["low"].iloc[-20:].min())
        rng = hi - lo
        return (ctx.close - lo) / rng if rng > 0 else 0.5

    @staticmethod
    def _low_factor(pir: float) -> float:
        """區間位階越低係數越高：pir=0 → 1.0、pir≥0.6 → 0.2，之間線性衰減去階梯。

        原三段(<0.4=1.0/0.4-0.5=0.6/≥0.5=0.2)在 pir=0.39 vs 0.41 差 0.4x 是懸崖。
        改連續衰減後 pir=0.39/0.41 幾乎無差、pir=0.6 才確定壓到 0.2；同樣把「非低位」
        壓下不擾動會噴 rank，但沒有選定 0.4/0.5 這種 arbitrary 門檻的問題。
        """
        return clamp(1.0 - pir / 0.6 * 0.8, 0.2, 1.0)

    def _ratio(self, ctx: StockContext) -> float | None:
        """近 _SHORT 日平均(高低/收)振幅 ÷ 近 _LONG 日：<1 即收斂。缺料/長窗 0 回 None。"""
        if ctx.n_bars < self._LONG or ctx.close is None:
            return None
        close = ctx.prices["close"].replace(0, float("nan"))
        rng = (ctx.prices["high"] - ctx.prices["low"]) / close
        short = float(rng.iloc[-self._SHORT:].mean())
        long = float(rng.iloc[-self._LONG:].mean())
        if long != long or long <= 0 or short != short:  # NaN / 0
            return None
        return short / long

    def _drift(self, ctx: StockContext) -> float | None:
        """近 20 日淨漂移絕對值：越小＝越橫向（已過上揚月線硬篩，方向偏多免再判向）。"""
        if ctx.n_bars < 20:
            return None
        c0 = float(ctx.prices["close"].iloc[-20])
        c1 = float(ctx.prices["close"].iloc[-1])
        if c0 <= 0:
            return None
        return abs(c1 / c0 - 1)

    def score(self, ctx: StockContext) -> float | None:
        ratio = self._ratio(ctx)
        pir = self._pir(ctx)
        if ratio is None or pir is None:
            return None
        s = clamp((1.0 - ratio) / 0.5 * 100, 0, 70)  # 振幅越收斂分越高（上限 70）
        drift = self._drift(ctx)
        if drift is not None:
            # 越橫向越高：drift=0 得 15、drift≥10% 得 0，連續衰減去 4%/8% 三段階梯。
            s += clamp((0.10 - drift) / 0.10 * 15, 0, 15)
        ind = ctx.ind
        v5 = ind.get("vol_ma5") if ind is not None else None
        v20 = ind.get("vol_ma20") if ind is not None else None
        if v5 is not None and v20 is not None and v20 > 0:
            # 量縮越明顯越好：v5/v20=0.5(近5日均量僅一半)得 15、持平(=1)得 0，
            # 原「v5<v20 即 +15、否則 0」布林在 v5/v20=0.99/1.01 差 15 分是懸崖。
            s += clamp((1.0 - v5 / v20) / 0.5 * 15, 0, 15)
        return clamp(s * self._low_factor(pir))  # 乘低位係數：非低位即使收斂也不算打底

    def reason(self, ctx, value):
        return "低檔盤整打底" if value >= 50 else None

    def evidence(self, ctx, value):
        ratio = self._ratio(ctx)
        pir = self._pir(ctx)
        if ratio is None or pir is None:
            return None
        ptier = "區間低位" if pir <= 0.4 else ("區間中位" if pir <= 0.65 else "區間高位")
        ctier = "波動明顯收斂" if ratio <= 0.7 else ("波動略收斂" if ratio < 0.95 else "波動未收斂")
        parts = [f"{ptier}、{ctier}（近10日振幅約近月 {ratio * 100:.0f}%）"]
        drift = self._drift(ctx)
        if drift is not None and drift < 0.08:
            parts.append("股價橫向走平")
        ind = ctx.ind
        v5 = ind.get("vol_ma5") if ind is not None else None
        v20 = ind.get("vol_ma20") if ind is not None else None
        if v5 is not None and v20 is not None and v5 < v20:
            parts.append("量縮惜售")
        return "、".join(parts)


class EntryTimingScore(ScoreRule):
    """進場時機（擇時，非選股）：法人『剛進場』的時機分，只供清單內次要排序/徽章，不入會噴 rank。

    研究實證（chip_ic_research*.py，2021-2026 walk-forward 樣本外）：把『法人買超量級』拿來
    橫截面選股最弱（控制波動後僅 +2pp）；改看『法人剛轉買的時機』才有料——法人 20 日累計
    由賣轉正(翻買)當天進場 +5.3pp、外資投信同步買超、投信連續買超，這三者在**已選出的會噴
    清單內部**把摸+10%率再拉高 +2.6~2.9pp（兩段樣本外一致）。

    關鍵：混進會噴分數再重切前 N% 會把增益抵掉≈0，**唯有不動清單成員、只在成員間區分**才吃得
    到。故做成獨立『進場時機分數』(0~100)、default_weight=0：只落 sub_scores/evidence 供前端
    清單內排序與徽章，不改清單成員、不進會噴分數（與 VolatilityScore/ConsolidationScore 同模式）。
    合成＝法人翻買近期性(0.4)＋外資投信共識同向(0.3)＋投信連買天數(0.3)，等權自研究定版。
    """

    category = "entry_timing"
    default_weight = 0.0
    _STREAK_CAP = 8  # 投信連買天數封頂（正規化用）

    def _components(self, ctx: StockContext):
        """回 (recency, consensus, streak, streak_norm)；史料不足(無法人資料)回 None。"""
        recency = ctx.inst_cum_flip_recency()
        if recency is None:
            return None
        f20 = ctx.inst_sum("foreign_net", 20)
        t20 = ctx.inst_sum("trust_net", 20)
        consensus = 1.0 if (f20 > 0 and t20 > 0) else (0.5 if (f20 > 0 or t20 > 0) else 0.0)
        streak = ctx.inst_consecutive_buy("trust_net")
        return recency, consensus, streak, min(streak, self._STREAK_CAP) / self._STREAK_CAP

    def score(self, ctx: StockContext) -> float | None:
        comp = self._components(ctx)
        if comp is None:
            return None  # 無法人資料：缺料剔除，不灌 0（避免把『沒料』當『時機差』）
        recency, consensus, _, streak_norm = comp
        return clamp(100.0 * (0.4 * recency + 0.3 * consensus + 0.3 * streak_norm))

    def reason(self, ctx, value):
        comp = self._components(ctx)
        if comp is None:
            return None
        recency, consensus, streak, _ = comp
        if recency >= 0.6:
            return "法人剛翻買進場"
        if consensus >= 1.0 and value >= 50:
            return "外資投信同步進場"
        if streak >= 3 and value >= 50:
            return f"投信連{streak}日買超"
        return None

    def evidence(self, ctx, value):
        comp = self._components(ctx)
        if comp is None:
            return None
        recency, consensus, streak, _ = comp
        parts: list[str] = []
        if recency >= 0.6:
            parts.append("法人20日累計剛由賣轉買")
        elif recency > 0:
            parts.append("法人轉買中")
        if consensus >= 1.0:
            parts.append("外資投信同步買超")
        elif consensus >= 0.5:
            parts.append("法人單邊買超")
        if streak >= 2:
            parts.append(f"投信連{streak}日買超")
        return "、".join(parts) if parts else None  # 無時機訊號回 None：不污染行情白話/詳情


WAVE_SCORERS: list[ScoreRule] = [
    TrendScore(),
    MomentumScore(),
    VolumeScore(),
    ChipScore(),
    MarginScore(),
    PatternScore(),
    PositionScore(),
    VolatilityScore(),
    ConsolidationScore(),
    EntryTimingScore(),
]
