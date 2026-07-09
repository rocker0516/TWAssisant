"""長線軌規則 —「釣大魚」＝不斷持續成長＋展望好的公司（2026-07 重定錨）。

定義用「公司長相」而非事後漲幅；價格報酬留給回測驗證。路徑無關、不考慮止損。
原則：**產業差異修正「量尺」、不修「定義」**——持續成長保持絕對（慢池塘的相對
冠軍不是大魚），量尺被產業結構扭曲處（估值常態、成長基準率、認列節奏）才相對化。

硬篩三條：
  ①連續成長（定義本體）：近6月中≥5月營收YoY>0且最新月>0；lumpy認列產業（建材營造）
    走替代量尺=當年累計YoY>0且近3月均YoY>0。
  ②已經會賺錢：近4季EPS合計>0（虧損成長股不收——無現金流資料判斷存活力；回測後重審）。
  ③估值理智線（相對）：PE>類股中位×3才擋；無類股中位退 PE<100。

評分七類（2026-07 walk-forward 驗證後的「軟性版」修正，使用者拍板）：
  persistence 30 / outlook 25 / freshness 15 / accel 15 / valuation_sane 10 /
  quality_confirm 10 / strength 5。
回測發現（scripts/long_bigfish_validate.py，2021-06~2025-07）：
  - 魚齡毒性單調：成長 streak 5-9 月相對宇宙 +1pp → 19月+ −5.5pp → persistence 改
    12 月見頂後遞減（老魚不絕對排除、分數自然掉）。
  - 已漲幅毒性單調：前12月 <30% +3~4pp → 200%+ −20.9pp（2021 航運頂＝此桶）→ 新增
    freshness 因子重罰已暴漲（「釣還沒被釣走的魚」）。加此條款後年輕魚×未暴漲×高分
    組五年逐年相對宇宙全非負（2021 災難年保平、2025 +21pp）。
  - strength（類股內熱門成長）IC 持續為負（擁擠交易）→ 權重 15→5；
    valuation_sane IC +0.035 t=3.1 → 5→10。
廢除舊版：利潤率水準45分（產業屬性非魚訊）、PE<30硬篩（砍真成長股）、趨勢輔助
（站上季線是波段軌的事）。循環股：展望桶確認啟動、加速度+新鮮度雙剎車
（2021 實測加速度單獨煞不住——營收 YoY 落後股價見頂）。

資料：ctx.revenue / ctx.financials 為「歷史」DataFrame（單季化、依公布時點切片）；
ctx.fund_rel 為 engine 算好的類股相對量尺；ctx.events_60d 為近 60 日展望/利空事件。
"""

from __future__ import annotations

import math

import pandas as pd

from .base import FilterRule, ScoreRule, clamp
from ..context import StockContext

# 完工認列制產業：月營收忽高忽低是認列節奏非成長訊號 → 持續性硬篩走累計量尺
_LUMPY_SECTORS = {"建材營造"}

_MIN_MONTHS = 6  # 「不斷持續」至少要能看 6 個月


def _sector_name(ctx: StockContext) -> str | None:
    sec = getattr(ctx.stock, "sector", None)
    return sec.name if sec is not None else None


def _pe(ctx: StockContext) -> float | None:
    if ctx.valuation is None or "pe" not in ctx.valuation:
        return None
    v = ctx.valuation["pe"]
    return None if pd.isna(v) else float(v)


def _yoy3m(ctx: StockContext) -> float | None:
    tail = ctx.rev_yoy_tail(3)
    return sum(tail) / len(tail) if len(tail) == 3 else None


# ─────────────── 硬篩 ───────────────


class SustainedGrowthFilter(FilterRule):
    """①連續成長（大魚定義本體）。"""

    name = "sustained_growth"

    def passes(self, ctx: StockContext) -> bool:
        if _sector_name(ctx) in _LUMPY_SECTORS:
            cum = ctx.rev_cum_yoy()
            y3 = _yoy3m(ctx)
            return cum is not None and cum > 0 and y3 is not None and y3 > 0
        tail = ctx.rev_yoy_tail(_MIN_MONTHS)
        if len(tail) < _MIN_MONTHS:
            return False  # 史料不足以證明「持續」——不收
        return sum(1 for v in tail if v > 0) >= 5 and tail[-1] > 0


class ProfitableFilter(FilterRule):
    """②已經會賺錢：近4季EPS合計>0。虧損成長股不收（標記：回測後重審）。"""

    name = "profitable_ttm"

    def passes(self, ctx: StockContext) -> bool:
        ttm = ctx.eps_ttm()
        return ttm is not None and ttm > 0


class PeSanityFilter(FilterRule):
    """③估值理智線（相對）：只擋本夢比，不再用絕對 PE 上限砍成長股。"""

    name = "pe_sanity"

    def passes(self, ctx: StockContext) -> bool:
        pe = _pe(ctx)
        if pe is None or pe <= 0:
            return True  # 無估值資料不擋（獲利底線由②顧）；EPS>0 但 PE 缺屬資料缺口
        median = (ctx.fund_rel or {}).get("pe_sector_median")
        if median and median > 0:
            return pe <= median * 3
        return pe < 100


LONG_FILTERS: list[FilterRule] = [SustainedGrowthFilter(), ProfitableFilter(), PeSanityFilter()]

# ─────────────── 評分 ───────────────


class GrowthPersistenceScore(ScoreRule):
    """成長持續性（核心 30）：連續正成長月數 + EPS 連季遞增 + 營收創 12 月新高。"""

    category = "persistence"
    default_weight = 30.0

    def score(self, ctx: StockContext) -> float | None:
        n = ctx.rev_consec_growth_months()
        if n is None or len(ctx.rev_yoy_tail(_MIN_MONTHS)) < _MIN_MONTHS:
            return None
        # 魚齡曲線：12 月見頂、之後遞減（回測：streak 19月+ 相對宇宙 −5.5pp，老魚是毒）
        if n <= 12:
            s = n / 12 * 70
        else:
            s = max(15.0, 70 - (n - 12) * 6)
        eps = ctx.fin_tail("eps", 3)
        if len(eps) == 3 and eps[0] < eps[1] < eps[2]:
            s += 15
        elif len(eps) >= 2 and eps[-2] < eps[-1]:
            s += 8
        if ctx.rev_new_high_months(12):
            s += 15
        return clamp(s)

    def reason(self, ctx, value):
        n = ctx.rev_consec_growth_months() or 0
        return f"連 {n} 月營收正成長" if 6 <= n <= 18 else None

    def evidence(self, ctx, value):
        n = ctx.rev_consec_growth_months()
        if n is None:
            return None
        parts = [f"營收連 {n} 月年增為正" + ("（成長已進入中後段）" if n > 18 else "")]
        eps = ctx.fin_tail("eps", 3)
        if len(eps) == 3 and eps[0] < eps[1] < eps[2]:
            parts.append(f"EPS 三季遞增（{eps[0]:.2f}→{eps[1]:.2f}→{eps[2]:.2f} 元）")
        if ctx.rev_new_high_months(12):
            parts.append("月營收創近一年新高")
        return "、".join(parts)


class OutlookScore(ScoreRule):
    """展望（25）：展望桶消息（目標價/評等/法說/後市）+ 投信真金白銀。

    「沒消息」是中性資訊非缺料 → 恆有分（基準 40）。已知坑：法人評等在循環頂點
    最樂觀（落後指標）——過熱剎車交給加速度因子，此處不重複處理。
    """

    category = "outlook"
    default_weight = 25.0

    def _counts(self, ctx: StockContext) -> tuple[int, int]:
        outlook = risk = 0
        for ev in ctx.events_60d or []:
            if ev.category == "展望":
                outlook += 1
            elif ev.category == "利空" or ev.is_risk:
                risk += 1
        return outlook, risk

    def score(self, ctx: StockContext) -> float | None:
        outlook, risk = self._counts(ctx)
        s = 40.0
        s += min(outlook * 15, 40)
        s -= min(risk * 15, 40)
        trust20 = ctx.inst_sum("trust_net", 20)
        if trust20 > 0:
            s += 10
            if ctx.inst_consecutive_buy("trust_net") >= 5:
                s += 10
        return clamp(s)

    def reason(self, ctx, value):
        outlook, _ = self._counts(ctx)
        return "展望消息偏多" if outlook >= 2 and value >= 70 else None

    def evidence(self, ctx, value):
        outlook, risk = self._counts(ctx)
        trust20 = ctx.inst_sum("trust_net", 20)
        parts = []
        if outlook or risk:
            parts.append(f"近 60 日展望消息 {outlook} 則、利空 {risk} 則")
        if trust20 != 0:
            parts.append(f"投信 20 日累計{'買超' if trust20 > 0 else '賣超'} {abs(trust20):,.0f} 張")
        return "、".join(parts) if parts else None


class FreshnessScore(ScoreRule):
    """新鮮度（15）：魚還沒被釣走——前 12 月漲幅越大分越低。

    回測單調毒性：<30% 相對宇宙 +3~4pp、80~200% −8.2pp、200%+ −20.9pp（2021 航運
    循環頂＝此桶）。這是「大魚定義含價格位階」的軟性版：老魚/已暴漲不絕對排除，
    靠此分自然壓下門檻。掛牌不滿半年缺基期 → None。
    """

    category = "freshness"
    default_weight = 15.0

    def _mom12(self, ctx: StockContext) -> float | None:
        s = ctx.prices["close"].dropna() if ctx.prices is not None else pd.Series(dtype=float)
        if len(s) < 120:
            return None
        past = s.iloc[-min(len(s), 246)]
        if not past or pd.isna(past):
            return None
        return float(s.iloc[-1] / past - 1)

    def score(self, ctx: StockContext) -> float | None:
        mom = self._mom12(ctx)
        if mom is None:
            return None
        if mom <= 0.30:
            return 100.0
        if mom <= 0.80:
            return clamp(100 - (mom - 0.30) / 0.50 * 50)  # 30%→100 線性降至 80%→50
        if mom <= 2.00:
            return clamp(50 - (mom - 0.80) / 1.20 * 40)  # 80%→50 降至 200%→10
        return 0.0

    def reason(self, ctx, value):
        mom = self._mom12(ctx)
        return "低基期未起漲" if mom is not None and mom <= 0.30 else None

    def evidence(self, ctx, value):
        mom = self._mom12(ctx)
        if mom is None:
            return None
        tag = "，低基期" if mom <= 0.30 else ("，已大漲高位" if mom >= 0.80 else "")
        return f"近 12 月漲幅 {mom * 100:+.0f}%{tag}"


class GrowthStrengthScore(ScoreRule):
    """成長強度（5）：近 3 月均 YoY，**類股內百分位**（量尺相對化；小類股退全市場）。

    回測 IC 持續為負（t=−6，類股內最熱門的成長＝擁擠交易）→ 權重 15→5 降到
    敘事價值為主；證據行保留「類股內贏過 N% 同業」給人看。
    """

    category = "strength"
    default_weight = 5.0

    def score(self, ctx: StockContext) -> float | None:
        y3 = _yoy3m(ctx)
        if y3 is None:
            return None
        rank = (ctx.fund_rel or {}).get("yoy3m_rank")
        if rank is not None:
            return clamp(rank * 100)
        # 無類股相對量尺 → 絕對 log 壓縮（同舊版 GrowthScore 尺度）
        if y3 >= 0:
            return clamp(50 + 30 * math.log10(1 + y3 / 15))
        return clamp(50 + y3)

    def reason(self, ctx, value):
        y3 = _yoy3m(ctx)
        return f"營收動能強（近3月均 {y3:+.0f}%）" if y3 is not None and y3 >= 20 and value >= 70 else None

    def evidence(self, ctx, value):
        y3 = _yoy3m(ctx)
        if y3 is None:
            return None
        rank = (ctx.fund_rel or {}).get("yoy3m_rank")
        rel = f"，類股內贏過 {rank * 100:.0f}% 同業" if rank is not None else ""
        return f"近 3 月營收年增平均 {y3:+.1f}%{rel}"


class GrowthAccelScore(ScoreRule):
    """成長加速度（15）：近3月均YoY − 前3月均YoY。方向普世 → 絕對量尺。

    循環股的天然剎車：過熱段基期墊高、YoY 減速 → 此分先轉弱（即使消息還在喊多）。
    """

    category = "accel"
    default_weight = 15.0

    def score(self, ctx: StockContext) -> float | None:
        tail = ctx.rev_yoy_tail(6)
        if len(tail) < 6:
            return None
        recent, prior = sum(tail[3:]) / 3, sum(tail[:3]) / 3
        delta = recent - prior
        return clamp(50 + delta * 2.5)  # +20pp → 100；−20pp → 0

    def reason(self, ctx, value):
        return "成長加速中" if value >= 75 else None

    def evidence(self, ctx, value):
        tail = ctx.rev_yoy_tail(6)
        if len(tail) < 6:
            return None
        recent, prior = sum(tail[3:]) / 3, sum(tail[:3]) / 3
        return f"營收年增近 3 月均 {recent:+.1f}% vs 前 3 月均 {prior:+.1f}%（{recent - prior:+.1f}pp）"


class QualityConfirmScore(ScoreRule):
    """品質確認（10）：賺的是真錢——毛利/營益率趨勢向上 + 獲利跟得上營收。

    只看「趨勢」不看水準（趨勢自帶產業中性）；金融股三率 None → 部分/全部缺料，
    交給 None 剔除機制重分配權重，不特殊處理。
    """

    category = "quality_confirm"
    default_weight = 10.0

    def score(self, ctx: StockContext) -> float | None:
        parts: list[float] = []
        for col in ("gross_margin", "op_margin"):
            vals = ctx.fin_tail(col, 4)
            if len(vals) == 4:
                trend = (vals[2] + vals[3]) / 2 - (vals[0] + vals[1]) / 2
                parts.append(100.0 if trend >= 0 else 0.0)
        ttm, ttm_prev = ctx.eps_ttm(), ctx.eps_ttm(quarters_ago=4)
        if ttm is not None and ttm_prev is not None and ttm_prev > 0:
            parts.append(100.0 if ttm >= ttm_prev else 0.0)
        return clamp(sum(parts) / len(parts)) if parts else None

    def reason(self, ctx, value):
        return "獲利品質同步向上" if value >= 100 else None

    def evidence(self, ctx, value):
        bits = []
        gm = ctx.fin_tail("gross_margin", 4)
        if len(gm) == 4:
            bits.append(f"毛利率近兩季均 {(gm[2] + gm[3]) / 2:.1f}%（前兩季 {(gm[0] + gm[1]) / 2:.1f}%）")
        ttm, prev = ctx.eps_ttm(), ctx.eps_ttm(quarters_ago=4)
        if ttm is not None and prev is not None and prev > 0:
            bits.append(f"近 4 季 EPS {ttm:.2f} 元 vs 前一年 {prev:.2f} 元")
        return "、".join(bits) if bits else None


class ValuationSanityScore(ScoreRule):
    """估值合理度（守門 5）：PE 相對類股中位。便宜不加分邏輯已廢——大魚很少是最便宜的；
    此分只懲罰「成長對不起價格」的極端。循環股頂點低 PE 陷阱靠低配分+類股相對化緩解。"""

    category = "valuation_sane"
    default_weight = 10.0  # 回測 IC +0.035（t=3.1）→ 5 提到 10

    def score(self, ctx: StockContext) -> float | None:
        pe = _pe(ctx)
        if pe is None or pe <= 0:
            return None
        median = (ctx.fund_rel or {}).get("pe_sector_median")
        if median and median > 0:
            ratio = pe / median
            if ratio <= 1.2:
                return 100.0
            return clamp((3.0 - ratio) / 1.8 * 100)  # 1.2→100 線性降至 3.0→0
        if pe <= 40:
            return 100.0
        return clamp((100 - pe) / 60 * 100)

    def evidence(self, ctx, value):
        pe = _pe(ctx)
        if pe is None:
            return None
        median = (ctx.fund_rel or {}).get("pe_sector_median")
        if median and median > 0:
            return f"本益比 {pe:.1f} 倍 vs 類股中位 {median:.1f} 倍（{pe / median:.2f}×）"
        return f"本益比 {pe:.1f} 倍"


LONG_SCORERS: list[ScoreRule] = [
    GrowthPersistenceScore(),
    OutlookScore(),
    FreshnessScore(),
    GrowthStrengthScore(),
    GrowthAccelScore(),
    QualityConfirmScore(),
    ValuationSanityScore(),
]
