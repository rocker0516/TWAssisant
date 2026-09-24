"""StopLossCalculator（架構③，進出場共用）。

買進區間：上緣=現價（不追高）、下緣=支撐 max(近20日前低, 月線ma20)。
停損：不給（長線軌已移除 2026-08-28；既有長線「持倉」的出場照顧在 exit_signals.py，
與這裡的「進場計畫」無關）。

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

@dataclass
class TradePlan:
    buy_low: float | None
    buy_high: float | None
    stop_loss: float | None
    loss_pct: float | None


class StopLossCalculator:
    def support(self, ctx: StockContext, track: str) -> float | None:
        # track 參數保留簽名相容；長線軌移除後只剩波段：max(近20日前低, 月線ma20)
        ind = ctx.ind
        if ind is None:
            return None
        prev_low = ctx.recent_low(20)
        ma20 = ind.get("ma20")
        cands = [x for x in (prev_low, ma20) if x is not None]
        return max(cands) if cands else None

    def compute(self, ctx: StockContext, track: str) -> TradePlan:
        close = ctx.close
        ind = ctx.ind
        if close is None or ind is None:
            return TradePlan(None, None, None, None)

        support = self.support(ctx, track)
        buy_high = close  # 不追高
        buy_low = support if support is not None and support < close else close
        # 只給買進區間，不給停損價（見模組 docstring）。給了使用者就會照著設，
        # 而那條線會把這條軌的命中率打掉 25pp。
        return TradePlan(buy_low=float(round(buy_low, 2)),
                         buy_high=float(round(buy_high, 2)),
                         stop_loss=None, loss_pct=None)
