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


def _fmt_band(x: float) -> str:
    return f"{x:g}"


def _drift_detail(fh: dict) -> str:
    names = list(fh.get("drifted", []))
    n = int(fh.get("n_drifted", len(names)))
    psi = fh.get("drifted_psi", {}) or {}
    parts = []
    for name in names[:3]:
        p = psi.get(name)
        parts.append(f"{name} PSI {p['psi']:.2f}>{p['thr']:.2f}" if p and p.get("psi") is not None else name)
    more = "…" if n > 3 else ""
    return f"特徵漂移：{n} 個特徵超過門檻（{'、'.join(parts)}{more}）。系統依 fail-closed 不出單。"


def build_verdict(status: str, reason: str | None, reason_text: str | None, universe: int, qualified: int,
                  recommended: int, health: dict, candidate_band: tuple[float, float] | None) -> dict:
    """健康條一句話判讀。tone：ok＝正常出單、quiet＝市場面不出單（非故障）、fail＝系統 fail-closed。"""
    if status == "SYSTEM_NO_TRADE":
        if reason == "FEATURE_DRIFT":
            detail = _drift_detail(health.get("feature_health", {}) or {})
        else:
            detail = reason_text or reason or ""
        return {"headline": "系統暫停出單（fail-closed）", "detail": detail, "tone": "fail"}
    if status != "OK" or recommended == 0:
        return {"headline": "今日不出單：市場無機會", "detail": reason_text or reason or "", "tone": "quiet"}
    detail = f"Universe {universe} → 通過 Gate {qualified} → Top-K {recommended}"
    if candidate_band is not None:
        lo, hi = candidate_band
        if lo <= qualified <= hi:
            detail += " ｜ 候選數在 OOF 常態範圍內"
        else:
            detail += f" ｜ 候選數超出 OOF 常態範圍（p5–p95 {_fmt_band(lo)}–{_fmt_band(hi)}）"
    return {"headline": f"正常出單 {recommended} 檔", "detail": detail, "tone": "ok"}
