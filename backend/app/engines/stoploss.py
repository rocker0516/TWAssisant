"""StopLossCalculator（架構③，進出場共用）。

買進區間：上緣=現價（不追高）、下緣=支撐
  - 波段：max(近20日前低, 月線ma20)
  - 長線：季線ma60
停損：**只有長線軌有**。max(ATR停損, 結構停損) 再夾上限（-15%），回 loss_pct。

波段軌不設停損（2026-08-24 定版）：這條軌的目標函數是「10 日內摸到 +10%」，選的又是
ATR>9% 的高波動標的——舊的 -8% 上限剛好切在訊號自己的呼吸幅度上。實測（定案 crash
規則 574 筆、逐根走路徑、同日雙碰保守記停損）：無停損 挖 76.6%/後 67.1%，
加 -8% 停損掉到 51.6%/41.4%（−25pp），整個改版的增益被吃光。而期間曾浮虧 >10% 的
部位裡仍有 47% 最後照樣摸到 +10% ⇒ 停損把「路還沒走完」誤判成「論點錯了」。
**波段軌的風控是時間（10 日到期重審），不是價格。** 見 docs/wave-hit-challenge.md。
"""

from __future__ import annotations

from dataclasses import dataclass

from .context import StockContext

_ATR_MULT = 2.0
_CAP = {"long": 0.15}  # 停損最大虧損上限；波段軌不設停損（見模組 docstring）


@dataclass
class TradePlan:
    buy_low: float | None
    buy_high: float | None
    stop_loss: float | None
    loss_pct: float | None


class StopLossCalculator:
    def support(self, ctx: StockContext, track: str) -> float | None:
        ind = ctx.ind
        if ind is None:
            return None
        if track == "wave":
            prev_low = ctx.recent_low(20)
            ma20 = ind.get("ma20")
            cands = [x for x in (prev_low, ma20) if x is not None]
            return max(cands) if cands else None
        # long
        return ind.get("ma60")

    def compute(self, ctx: StockContext, track: str) -> TradePlan:
        close = ctx.close
        ind = ctx.ind
        if close is None or ind is None:
            return TradePlan(None, None, None, None)

        support = self.support(ctx, track)
        buy_high = close  # 不追高
        buy_low = support if support is not None and support < close else close

        if track not in _CAP:
            # 波段軌：只給買進區間，不給停損價（見模組 docstring）。給了使用者就會照著設，
            # 而那條線會把這條軌的命中率打掉 25pp。
            return TradePlan(buy_low=float(round(buy_low, 2)),
                             buy_high=float(round(buy_high, 2)),
                             stop_loss=None, loss_pct=None)

        atr = ind.get("atr14")
        atr_stop = close - _ATR_MULT * atr if atr is not None else None
        # 支撐在現價上方（跌破均線）就不能當停損，否則停損＞現價、loss_pct 變正值
        struct_stop = support if support is not None and support < close else None
        cands = [x for x in (atr_stop, struct_stop) if x is not None]
        # 取較高者（較貼近現價 = 較嚴謹）
        stop = max(cands) if cands else None

        cap = _CAP[track]
        floor = close * (1 - cap)  # 停損不可低於此（夾上限）
        if stop is None:
            stop = floor
        else:
            stop = max(stop, floor)

        loss_pct = (stop / close - 1) * 100
        return TradePlan(
            buy_low=float(round(buy_low, 2)),
            buy_high=float(round(buy_high, 2)),
            stop_loss=float(round(stop, 2)),
            loss_pct=float(round(loss_pct, 2)),
        )
