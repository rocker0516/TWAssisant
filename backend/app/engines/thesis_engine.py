"""波段論點狀態機（spec 2026-08-20-exit-philosophy-v2；停損出口於 2026-08-24 移除）。

純函式：不碰 DB。出口優先序＝反證(停損) → 兌現 → 過期，同日雙碰先判停損。
**stop_pct 為 None 時沒有反證出口**，只剩「兌現」與「到期重審」兩個——這是波段軌的
預設：目標函數是「10 日內摸 +10%」、標的是 ATR>9% 的高波動股，−8% 停損實測會把
命中率打掉 25pp（挖 76.6→51.6%、後 67.1→41.4%），且期間浮虧 >10% 的部位仍有 47%
最後照樣達標。波段軌的風控是**時間**（到期重審上限 2 次），不是價格。
使用者若在回測實驗室的策略上自行設了 stop_pct，該策略來源的持股仍照它走（明示優先）。
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
    stop_price: float | None   # None＝這筆論點沒有停損出口（波段軌預設）
    messages: list[str] = field(default_factory=list)


def evaluate_thesis(thesis: dict, *, avg_cost: float, hi_since_clock: float,
                    lo_today: float | None, days_elapsed: int,
                    reaudit_max: int = 2) -> ThesisEval:
    horizon = int(thesis["horizon_days"])
    target = round(avg_cost * (1 + thesis["target_pct"] / 100), 2)
    stop_pct = thesis.get("stop_pct")
    stop = round(avg_cost * (1 - stop_pct / 100), 2) if stop_pct is not None else None
    n = int(thesis.get("reaudit_count", 0))
    left = max(0, horizon - days_elapsed)

    def out(state, level, msgs):
        return ThesisEval(state, level, days_elapsed, left, n, target, stop, msgs)

    prior = thesis.get("state", "active")
    if prior in TERMINAL:
        msg = {"fulfilled": "論點已兌現", "expired": "論點已過期", "refuted": "論點已反證"}[prior]
        return out(prior, "red", [msg])
    if stop is not None and lo_today is not None and lo_today <= stop:
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
