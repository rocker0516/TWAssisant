"""/app/level1 呈現層純函式：估算價、健康條判讀句。

只做讀取與措辭，不含任何判定門檻（門檻一律來自 config／Frozen 統計）。
"""

from __future__ import annotations

import math

from app.research.level2.costs import round_down_tick, round_up_tick


def est_barrier_prices(close: float | None, target_pct: float = 0.10, stop_pct: float = 0.05
                       ) -> tuple[float | None, float | None]:
    """以收盤估算 +10%／−5% 價位；目標向下、停損向上取整（兩者皆取保守側）。
    實際 barrier 從明日開盤起算，這裡只是跟單參考。"""
    if close is None or not math.isfinite(close) or close <= 0:
        return None, None
    return round_down_tick(close * (1 + target_pct)), round_up_tick(close * (1 - stop_pct))
