"""共用硬篩（兩軌通用，架構③ COMMON_FILTERS）。

設計定案：排除 ETF / 近20日均量≥500張 / 掛牌滿60交易日。
全額交割·處置·警示·連續漲跌停·暴量異常：尚無資料源，P4 接重訊/狀態後補
（先以暴量過濾擋掉最離譜的異常）。
"""

from __future__ import annotations

from .base import FilterRule
from ..context import StockContext

_MIN_VOL_SHARES = 500 * 1000  # 500 張
_MIN_BARS = 60  # 掛牌滿 60 交易日


class NotEtfFilter(FilterRule):
    name = "not_etf"

    def passes(self, ctx: StockContext) -> bool:
        return not ctx.stock.is_etf


class MinLiquidityFilter(FilterRule):
    name = "min_liquidity"

    def passes(self, ctx: StockContext) -> bool:
        ind = ctx.ind
        v = ind.get("vol_ma20") if ind is not None else None
        return v is not None and v >= _MIN_VOL_SHARES


class ListedLongEnoughFilter(FilterRule):
    name = "listed_long_enough"

    def passes(self, ctx: StockContext) -> bool:
        return ctx.n_bars >= _MIN_BARS


class NotAbnormalVolumeFilter(FilterRule):
    """暴量異常：當日量 > 20日均量 ×6 視為異常排除。"""

    name = "not_abnormal_volume"

    def passes(self, ctx: StockContext) -> bool:
        ind = ctx.ind
        vma = ind.get("vol_ma20") if ind is not None else None
        vol = float(ctx.prices["volume"].iloc[-1]) if ctx.n_bars else None
        if not vma or vol is None:
            return True
        return vol <= vma * 6


COMMON_FILTERS: list[FilterRule] = [
    NotEtfFilter(),
    MinLiquidityFilter(),
    ListedLongEnoughFilter(),
    NotAbnormalVolumeFilter(),
]
