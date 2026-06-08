"""出場訊號（架構③ ExitEngine）。可插拔 ExitSignal 子類。

子類 .check() 回 0+ 個 Hit（含嚴重度）。ExitEngine 聚合成狀態燈。
嚴重度：EARLY🟡 單一早期訊號 / WARN🟠 警戒 / CRITICAL🔴 建議出場。

預設參數（設計定案）：
  波段：停損 -8% 或跌破月線；移動停利 獲利>10% 啟動、回落 10%；技術轉弱(空頭/KD死叉/爆量長黑)。
  長線：停損 -15% 或跌破季線；移動停利 回落 20%；基本面轉弱(月營收年增轉負/法人連賣)。
逐檔可覆寫 stop_loss_override(絕對價) / trail_trigger / trail_pullback。
SectorWeak(P3) / NewsRisk(P4) 之後加，加訊號不動聚合骨架。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import IntEnum

from .context import StockContext
from ..storage import models

DEFAULTS = {
    "wave": {"stop_cap": 0.08, "trail_trigger": 0.10, "trail_pullback": 0.10},
    "long": {"stop_cap": 0.15, "trail_trigger": 0.20, "trail_pullback": 0.20},
}


class Sev(IntEnum):
    EARLY = 1
    WARN = 2
    CRITICAL = 3


@dataclass
class Hit:
    code: str
    sev: Sev
    message: str


@dataclass
class Position:
    shares: int
    avg_cost: float
    highest: float  # 持有期間最高價
    close: float

    @property
    def return_pct(self) -> float:
        return (self.close / self.avg_cost - 1) * 100

    @property
    def peak_return(self) -> float:
        return (self.highest / self.avg_cost - 1) if self.avg_cost else 0.0

    @property
    def drawdown(self) -> float:
        return (self.highest - self.close) / self.highest if self.highest else 0.0


def _cfg(holding: models.Holding, key: str) -> float:
    return DEFAULTS.get(holding.track, DEFAULTS["wave"])[key]


class ExitSignal(ABC):
    tracks: tuple[str, ...] = ("wave", "long")

    def applies(self, track: str) -> bool:
        return track in self.tracks

    @abstractmethod
    def check(self, holding: models.Holding, pos: Position, ctx: StockContext) -> list[Hit]: ...


class StopLossSignal(ExitSignal):
    """停損：跌破停損價（-cap% 或覆寫）或跌破均線（波段月線/長線季線）。"""

    def check(self, holding, pos, ctx):
        hits: list[Hit] = []
        cap = _cfg(holding, "stop_cap")
        hard_stop = holding.stop_loss_override or pos.avg_cost * (1 - cap)
        if pos.close <= hard_stop:
            hits.append(Hit("stop_loss", Sev.CRITICAL, f"跌破停損價 {hard_stop:.2f}"))
        elif pos.close <= hard_stop * 1.02:
            dist = (pos.close / hard_stop - 1) * 100
            hits.append(Hit("near_stop", Sev.WARN, f"接近停損價（距 {dist:.1f}%）"))

        ind = ctx.ind
        ma_key = "ma20" if holding.track == "wave" else "ma60"
        ma = ind.get(ma_key) if ind is not None else None
        if ma is not None and pos.close < ma:
            label = "月線" if holding.track == "wave" else "季線"
            hits.append(Hit("break_ma", Sev.CRITICAL, f"跌破{label} {ma:.2f}"))
        return hits


class TrailingStopSignal(ExitSignal):
    """移動停利：獲利達啟動門檻後，自持有最高點回落超過設定 %。"""

    def check(self, holding, pos, ctx):
        trigger = holding.trail_trigger_override or _cfg(holding, "trail_trigger")
        pullback = holding.trail_pullback_override or _cfg(holding, "trail_pullback")
        if pos.peak_return < trigger:
            return []
        dd = pos.drawdown
        if dd >= pullback:
            return [Hit("trailing_stop", Sev.CRITICAL, f"自高點回落 {dd * 100:.0f}%，觸發移動停利")]
        if dd >= pullback * 0.7:
            return [Hit("near_trailing", Sev.WARN, f"接近移動停利（回落 {dd * 100:.0f}%）")]
        return []


class TechWeakSignal(ExitSignal):
    """技術轉弱（波段）：均線空頭排列 / KD 死叉 / 爆量長黑。"""

    tracks = ("wave",)

    def check(self, holding, pos, ctx):
        ind, prev = ctx.ind, ctx.ind_ago(1)
        if ind is None:
            return []
        hits: list[Hit] = []
        ma5, ma10, ma20 = ind.get("ma5"), ind.get("ma10"), ind.get("ma20")
        if ma5 and ma10 and ma20 and ma5 < ma10 < ma20:
            hits.append(Hit("ma_bear", Sev.EARLY, "均線空頭排列"))
        k, d = ind.get("kd_k"), ind.get("kd_d")
        if prev is not None and k is not None and d is not None:
            pk, pd_ = prev.get("kd_k"), prev.get("kd_d")
            if pk is not None and pd_ is not None and k < d and pk >= pd_:
                hits.append(Hit("kd_dead", Sev.EARLY, "KD 死叉"))
        if ctx.n_bars >= 1:
            o = float(ctx.prices["open"].iloc[-1])
            vol = float(ctx.prices["volume"].iloc[-1])
            vma = ind.get("vol_ma20")
            if o and pos.close < o and (o - pos.close) / o > 0.03 and vma and vol > vma * 1.8:
                hits.append(Hit("vol_black", Sev.EARLY, "爆量長黑"))
        return hits


class FundamentalWeakSignal(ExitSignal):
    """基本面轉弱（長線）：月營收年增轉負 / 法人連續賣超。"""

    tracks = ("long",)

    def check(self, holding, pos, ctx):
        hits: list[Hit] = []
        yoy = None
        if ctx.revenue is not None and "yoy" in ctx.revenue:
            v = ctx.revenue["yoy"]
            yoy = None if v is None else float(v)
        if yoy is not None:
            if yoy < -10:
                hits.append(Hit("rev_drop", Sev.CRITICAL, f"月營收年增大幅轉負 {yoy:.0f}%"))
            elif yoy < 0:
                hits.append(Hit("rev_neg", Sev.EARLY, f"月營收年增轉負 {yoy:.0f}%"))
        ind = ctx.ind
        vma_lots = (ind.get("vol_ma20") or 0) / 1000 if ind is not None else 0
        if vma_lots > 0:
            net10 = ctx.inst_sum("foreign_net", 10) + ctx.inst_sum("trust_net", 10)
            if net10 / (vma_lots * 10) < -0.05:
                hits.append(Hit("inst_sell", Sev.EARLY, "法人連續賣超"))
        return hits


# 註冊順序即評估順序；加訊號只加這裡
ALL_SIGNALS: list[ExitSignal] = [
    StopLossSignal(),
    TrailingStopSignal(),
    TechWeakSignal(),
    FundamentalWeakSignal(),
]
