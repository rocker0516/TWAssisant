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
    # 波段 stop_cap=None＝不設停損（2026-08-24；見 engines/stoploss.py docstring）。
    # 波段持股正常走 thesis 論點機，這組只在「還沒補到論點快照的舊倉」用得到。
    "wave": {"stop_cap": None, "trail_trigger": 0.10, "trail_pullback": 0.10, "break_ma_exit": True},
    "long": {"stop_cap": 0.15, "trail_trigger": 0.20, "trail_pullback": 0.20, "break_ma_exit": True,
              "score_slip_warn": 15.0},
}

# 可由設定頁覆寫（即時生效）。ExitEngine 每次評估前以 set_config 注入（百分比→比例）。
import copy as _copy

_ACTIVE: dict = _copy.deepcopy(DEFAULTS)


def set_config(percent_cfg: dict) -> None:
    """設定頁的出場參數（百分比，如 stop_cap=8）→ 比例（0.08）寫入 _ACTIVE。

    break_ma_exit 為布林（跌破均線是否視為建議出場），非百分比，原樣寫入。
    """
    for track in ("wave", "long"):
        tc = (percent_cfg or {}).get(track, {})
        for k in ("stop_cap", "trail_trigger", "trail_pullback"):
            if k in tc and tc[k] is not None:
                _ACTIVE.setdefault(track, {})[k] = tc[k] / 100.0
        if tc.get("break_ma_exit") is not None:
            _ACTIVE.setdefault(track, {})["break_ma_exit"] = bool(tc["break_ma_exit"])
        if track == "long" and tc.get("score_slip_warn") is not None:
            _ACTIVE.setdefault(track, {})["score_slip_warn"] = tc["score_slip_warn"]


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


def _cfg(holding: models.Holding, key: str) -> float | None:
    return _ACTIVE.get(holding.track, _ACTIVE["wave"])[key]


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
        # cap=None（波段軌）＝這條軌沒有停損線，只有手動覆寫才會有
        hard_stop = holding.stop_loss_override or (
            pos.avg_cost * (1 - cap) if cap is not None else None)
        if hard_stop is not None:
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
            # 跌破均線是否視為建議出場可由設定切換；關閉時降為早期警示（放寬出場、回測較佳）
            break_exit = _ACTIVE.get(holding.track, _ACTIVE["wave"]).get("break_ma_exit", True)
            sev = Sev.CRITICAL if break_exit else Sev.EARLY
            hits.append(Hit("break_ma", sev, f"跌破{label} {ma:.2f}"))
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


class ScoreSlipSignal(ExitSignal):
    """長線分數滑落（主基本面訊號）：近 5 日均值 vs 進場快照分數。"""

    tracks = ("long",)

    def check(self, holding, pos, ctx):
        snap = holding.entry_snapshot or {}
        baseline = snap.get("total_score")
        scores = getattr(ctx, "long_scores", None) or []
        if baseline is None or len(scores) < 5:
            return []
        cur = sum(scores[:5]) / 5
        slip = float(baseline) - cur
        warn_at = _ACTIVE["long"].get("score_slip_warn", 15.0)
        if slip < warn_at:
            return []
        passed = getattr(ctx, "long_passed_filter", None)
        if passed is False:
            return [Hit("score_slip", Sev.CRITICAL,
                        f"長線分數自 {baseline:.0f} 降至 {cur:.0f} 且跌破持有門檻")]
        return [Hit("score_slip", Sev.WARN, f"長線分數自 {baseline:.0f} 降至 {cur:.0f}")]


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
                hits.append(Hit("rev_drop", Sev.WARN, f"月營收年增大幅轉負 {yoy:.0f}%"))
            elif yoy < 0:
                hits.append(Hit("rev_neg", Sev.EARLY, f"月營收年增轉負 {yoy:.0f}%"))
        ind = ctx.ind
        vma_lots = (ind.get("vol_ma20") or 0) / 1000 if ind is not None else 0
        if vma_lots > 0:
            net20 = ctx.inst_sum("foreign_net", 20) + ctx.inst_sum("trust_net", 20)
            if net20 / (vma_lots * 20) < -0.05:
                hits.append(Hit("inst_sell", Sev.EARLY, "法人連續賣超"))
        return hits


class NewsRiskSignal(ExitSignal):
    """消息面利空（P4）：持股近期有利空事件 → 警戒。"""

    def check(self, holding, pos, ctx):
        if not ctx.events:
            return []
        risks = [e for e in ctx.events if e.is_risk]
        if not risks:
            return []
        latest = risks[0]
        # 處置警示視為較重（WARN），其餘利空為早期（EARLY）
        sev = Sev.WARN if (latest.category == "處置警示") else Sev.EARLY
        return [Hit("news_risk", sev, f"利空消息：{latest.title[:20]}")]


# 註冊順序即評估順序；加訊號只加這裡
ALL_SIGNALS: list[ExitSignal] = [
    StopLossSignal(),
    TrailingStopSignal(),
    TechWeakSignal(),
    ScoreSlipSignal(),
    FundamentalWeakSignal(),
    NewsRiskSignal(),
]
