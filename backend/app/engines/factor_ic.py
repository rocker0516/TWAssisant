"""FactorICEngine（架構③ L4 / Phase 2）：單因子 IC → 資料驅動的因子權重。

回應「配分是手設魔法數值」的疑慮：用歷史算每個因子的 **rank-IC**（該因子分數與
未來報酬的橫截面 Spearman 相關），由資料決定哪個因子有預測力、該給多少權重，
取代手設配分。**只決定『因子間權重』**；每條規則內部的門檻/給分仍是 heuristic
（那層要資料化得做更細的單因子分桶，另議）。

做法（與 CalibrationEngine 同一套 point-in-time 回測骨架）：
  對回測窗每個目標日 T、每檔有 ≥_MIN_BARS 回看的股票，point-in-time 算各因子原始分數
  與 T→T+h 報酬；每個 T 在橫截面上算各因子的 rank-IC；跨日平均得 IC 均值與 IR。
  建議權重 ∝ max(0, IC 均值)（無/負預測力的因子降到 0），正規化呈現。

只波段(技術)軌：技術指標有逐日歷史可乾淨回測；長線基本面只有快照、有前視偏誤，不做。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..storage import models
from .base import BaseEngine
from .calibration import _MIN_BARS, _iter_stock_groups, _window
from .context import StockContext
from .tracks import WaveTrack

_IC_HORIZON = 20          # 報酬水平（交易日）：波段持有約一個月
_MIN_STOCKS_PER_DATE = 30  # 該日橫截面至少這麼多檔才算當日 IC（太少不穩）


def _spearman(scores: list[float], rets: list[float]) -> float | None:
    """rank-IC = 排序後的 Pearson 相關（即 Spearman）。自行 rank+pearson，免 scipy 依賴。"""
    if len(scores) < _MIN_STOCKS_PER_DATE:
        return None
    rs = pd.Series(scores).rank()
    rr = pd.Series(rets).rank()
    ic = rs.corr(rr)  # 預設 pearson；對 rank 即 Spearman
    return float(ic) if ic == ic else None  # 過濾 NaN（全同分→無排序）


class FactorICEngine(BaseEngine):
    name = "factor_ic"

    def __init__(self) -> None:
        self.track = WaveTrack()

    def run(self, session: Session, trading_date: date) -> dict:
        result = self.compute(session, generated_at=trading_date)
        row = session.get(models.Setting, "factor_ic")
        if row is None:
            session.add(models.Setting(key="factor_ic", value=result))
        else:
            row.value = result
        session.flush()
        return {"status": "ok", "factors": len(result["factors"]), "score_dates": result["score_dates"]}

    def compute(self, session: Session, generated_at: date) -> dict:
        target_dates, date_lo = _window(session, reserve_tail=_IC_HORIZON)
        if not target_dates:
            return self._empty(generated_at)
        target_set = set(target_dates)
        stock_map = {s.id: s for s in session.execute(select(models.Stock)).scalars().all()}
        stock_ids = sorted(stock_map)
        scorers = self.track.scorers

        # by_date[T][factor] = ([factor_score...], [forward_return...])（橫截面累積）
        by_date: dict[date, dict[str, tuple[list[float], list[float]]]] = defaultdict(
            lambda: defaultdict(lambda: ([], []))
        )

        for sid, pdf, ind_g, inst_g, margin_g in _iter_stock_groups(session, stock_ids, date_lo):
            stock = stock_map.get(sid)
            if stock is None or ind_g is None:
                continue
            if inst_g is None:
                inst_g = pd.DataFrame(columns=["stock_id", "date"])
            pos_of = {d: i for i, d in enumerate(pdf["date"])}
            closes = [None if pd.isna(x) else float(x) for x in pdf["close"]]
            for T in pdf["date"]:
                if T not in target_set:
                    continue
                p = pos_of[T]
                if p < _MIN_BARS - 1 or p + _IC_HORIZON >= len(closes):
                    continue
                base_c, fwd_c = closes[p], closes[p + _IC_HORIZON]
                if not base_c or base_c <= 0 or not fwd_c or fwd_c <= 0:
                    continue
                ret = fwd_c / base_c - 1
                ctx = StockContext(
                    stock=stock, date=T, prices=pdf.iloc[: p + 1],
                    inds=ind_g[ind_g["date"] <= T],
                    inst=inst_g[inst_g["date"] <= T] if not inst_g.empty else inst_g,
                    margin=margin_g[margin_g["date"] <= T] if margin_g is not None else None,
                )
                for sc in scorers:
                    val = sc.score(ctx)
                    if val is None:
                        continue
                    s, r = by_date[T][sc.category]
                    s.append(float(val))
                    r.append(ret)

        # 每因子：逐日 rank-IC → 跨日平均（IC 均值）+ IR（均值/標準差）
        factors: dict[str, dict] = {}
        for sc in scorers:
            f = sc.category
            ics = [ic for T in by_date if (ic := _spearman(*by_date[T].get(f, ([], [])))) is not None]
            if ics:
                mean = sum(ics) / len(ics)
                std = (sum((x - mean) ** 2 for x in ics) / len(ics)) ** 0.5
                factors[f] = {
                    "ic_mean": round(mean, 4),
                    "ic_ir": round(mean / std, 3) if std else None,
                    "n_dates": len(ics),
                }
            else:
                factors[f] = {"ic_mean": None, "ic_ir": None, "n_dates": 0}

        current = {sc.category: sc.default_weight for sc in scorers}
        suggested = self._suggest_weights(factors, current)
        return {
            "generated_at": generated_at.isoformat(),
            "track": "wave",
            "horizon": _IC_HORIZON,
            "window": {
                "from": target_dates[0].isoformat(),
                "to": target_dates[-1].isoformat(),
            },
            "score_dates": len(by_date),
            "factors": factors,
            "current_weights": current,
            "suggested_weights": suggested,
            "note": (
                f"rank-IC＝因子分數與未來 {_IC_HORIZON} 交易日報酬的橫截面 Spearman 相關，"
                "跨日平均；IR＝IC 均值/標準差（穩定度）。建議權重 ∝ max(0, IC 均值) 正規化，"
                "無/負預測力因子降到 0。只決定因子間權重，規則內部門檻仍為 heuristic。"
                "樣本為近 ~2 年、非投資建議。"
            ),
        }

    @staticmethod
    def _suggest_weights(factors: dict[str, dict], current: dict[str, float]) -> dict[str, float]:
        """建議權重 ∝ max(0, IC 均值)，正規化到與現行權重總和相同（量級可比）。"""
        pos = {f: max(0.0, (factors[f].get("ic_mean") or 0.0)) for f in current}
        tot = sum(pos.values())
        scale = sum(current.values())
        if tot <= 0:
            return {f: None for f in current}
        return {f: round(scale * pos[f] / tot, 1) for f in current}

    def _empty(self, generated_at: date) -> dict:
        return {
            "generated_at": generated_at.isoformat(), "track": "wave", "horizon": _IC_HORIZON,
            "window": {"from": None, "to": None}, "score_dates": 0,
            "factors": {}, "current_weights": {}, "suggested_weights": {},
            "note": "資料不足，無法計算因子 IC。",
        }
