"""波段論點狀態機（spec 2026-08-20-exit-philosophy-v2）。

純函式：不碰 DB。三出口優先序＝反證(停損) → 兌現 → 過期，
同日雙碰先判停損（與 strategy_engine._judge 回測口徑一致）。
狀態持久化由 ExitEngine.run() 負責，本模組只算。
"""

from __future__ import annotations

from dataclasses import dataclass, field

TERMINAL = {"fulfilled", "expired", "refuted"}


@dataclass
class ThesisEval:
    state: str
    level: str
    days_elapsed: int
    days_left: int
    reaudit_count: int
    target_price: float
    stop_price: float
    messages: list[str] = field(default_factory=list)


def evaluate_thesis(thesis: dict, *, avg_cost: float, hi_since_clock: float,
                    lo_today: float | None, days_elapsed: int,
                    reaudit_max: int = 2) -> ThesisEval:
    horizon = int(thesis["horizon_days"])
    target = round(avg_cost * (1 + thesis["target_pct"] / 100), 2)
    stop = round(avg_cost * (1 - thesis["stop_pct"] / 100), 2)
    n = int(thesis.get("reaudit_count", 0))
    left = max(0, horizon - days_elapsed)

    def out(state, level, msgs):
        return ThesisEval(state, level, days_elapsed, left, n, target, stop, msgs)

    prior = thesis.get("state", "active")
    if prior in TERMINAL:
        msg = {"fulfilled": "論點已兌現", "expired": "論點已過期", "refuted": "論點已反證"}[prior]
        return out(prior, "red", [msg])
    if lo_today is not None and lo_today <= stop:
        return out("refuted", "red", [f"論點反證：觸及停損價 {stop:.2f}，建議出場"])
    if hi_since_clock >= target:
        return out("fulfilled", "red", [f"論點兌現：觸及目標價 {target:.2f}，建議獲利了結"])
    if days_elapsed >= horizon:
        if n >= reaudit_max:
            return out("expired", "red", [f"論點過期：已重審 {n}/{reaudit_max} 次，建議出場"])
        return out("awaiting_reaudit", "orange", [f"第 {horizon} 天未兌現，待重審進場條件"])
    if left <= 2:
        return out("expiring", "yellow", [f"論點倒數 {left} 天（第 {days_elapsed}/{horizon} 天）"])
    return out("active", "green", [])
