"""SectorEngine（架構③）：由成分股聚合算類股強弱 / 方向 / 輪動 → sector_daily。

三維度（與 Track 共用 WeightedScorer 做正規化加權）：
  動能 = 近5/20日漲跌幅；資金 = 法人淨買超佔量比 + 成交佔比；技術 = 站上均線/多頭排列家數比。
雙時間框架方向：短波段（ret5+站上月線+法人）、中長期（ret20+站上季線）。
輪動階段：破底→轉弱→剛起漲→主升段→高檔鈍化→整理（由 above_ma / 動能啟發式判定）。
資料沿用個股 daily_prices/indicators/institutional（不新增來源）。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..storage import models
from ..storage.repositories import BaseRepository
from .base import BaseEngine
from .rules.base import WeightedScorer, clamp

_DEFAULT_WEIGHTS = {"momentum": 35.0, "fund": 30.0, "tech": 35.0}
_MIN_CONSTITUENTS = 3


def _classify_short(ret5: float, above_ma20: float) -> str:
    if ret5 > 1.5 and above_ma20 >= 0.55:
        return "偏多"
    if ret5 < -1.5 and above_ma20 <= 0.45:
        return "偏空"
    return "中性"


def _classify_long(ret20: float, above_ma60: float) -> str:
    if ret20 > 3 and above_ma60 >= 0.55:
        return "偏多"
    if ret20 < -3 and above_ma60 <= 0.45:
        return "偏空"
    return "中性"


def _rotation(above_ma20: float, above_ma60: float, ret5: float, ret20: float) -> str:
    if above_ma60 < 0.3 and ret20 < -3:
        return "破底"
    if above_ma20 < 0.4 and ret5 < 0:
        return "轉弱"
    if above_ma60 < 0.5 and ret20 < 0 and ret5 > 1 and above_ma20 > 0.5:
        return "剛起漲"
    if above_ma20 > 0.6 and above_ma60 > 0.6 and ret20 > 2:
        if ret5 < ret20 / 4:  # 短動能鈍化但仍高檔
            return "高檔鈍化"
        return "主升段"
    return "整理"


class SectorEngine(BaseEngine):
    name = "sector"

    def _weights(self, session: Session) -> dict[str, float]:
        row = session.get(models.Setting, "sector")
        cfg = row.value if row and isinstance(row.value, dict) else {}
        overrides = cfg.get("weights", {})
        return {k: overrides.get(k, v) for k, v in _DEFAULT_WEIGHTS.items()}

    def _trading_dates(self, session: Session, td: date) -> list[date]:
        return list(
            session.execute(
                select(models.DailyPrice.date).where(models.DailyPrice.date <= td)
                .distinct().order_by(models.DailyPrice.date.desc()).limit(21)
            ).scalars().all()
        )

    def run(self, session: Session, trading_date: date) -> dict:
        td = trading_date
        dates = self._trading_dates(session, td)
        if not dates:
            return {"status": "empty"}
        d5 = dates[min(5, len(dates) - 1)]
        d20 = dates[min(20, len(dates) - 1)]
        last5 = dates[: min(5, len(dates))]

        def closes_on(d: date) -> dict[str, float]:
            return dict(
                session.execute(
                    select(models.DailyPrice.stock_id, models.DailyPrice.close)
                    .where(models.DailyPrice.date == d)
                ).all()
            )

        close_td, close_5, close_20 = closes_on(td), closes_on(d5), closes_on(d20)
        turnover = dict(
            session.execute(
                select(models.DailyPrice.stock_id, models.DailyPrice.turnover)
                .where(models.DailyPrice.date == td)
            ).all()
        )
        inds = {
            r.stock_id: r
            for r in session.execute(
                select(models.Indicator).where(models.Indicator.date == td)
            ).scalars().all()
        }
        # 近5日法人（外資+投信）淨買超（張）
        inst_rows = session.execute(
            select(
                models.Institutional.stock_id,
                models.Institutional.foreign_net,
                models.Institutional.trust_net,
            ).where(models.Institutional.date.in_(last5))
        ).all()
        inst_net: dict[str, float] = {}
        for sid, f, t in inst_rows:
            inst_net[sid] = inst_net.get(sid, 0) + (f or 0) + (t or 0)

        sectors = session.execute(
            select(models.Stock.id, models.Stock.sector_id).where(models.Stock.sector_id.isnot(None))
        ).all()
        by_sector: dict[int, list[str]] = {}
        for sid, sec in sectors:
            by_sector.setdefault(sec, []).append(sid)

        total_turnover = sum(v for v in turnover.values() if v) or 1.0
        weights = self._weights(session)
        rows: list[dict] = []

        for sec_id, members in by_sector.items():
            valid = [s for s in members if s in close_td and s in inds]
            if len(valid) < _MIN_CONSTITUENTS:
                continue
            n = len(valid)
            rets5, rets20, above20, above60, bull = [], [], 0, 0, 0
            net5_lots = 0.0
            sec_turnover = 0.0
            avg_lots5 = 0.0
            for s in valid:
                c = close_td[s]
                if s in close_5 and close_5[s]:
                    rets5.append((c / close_5[s] - 1) * 100)
                if s in close_20 and close_20[s]:
                    rets20.append((c / close_20[s] - 1) * 100)
                ind = inds[s]
                if ind.ma20 and c > ind.ma20:
                    above20 += 1
                if ind.ma60 and c > ind.ma60:
                    above60 += 1
                if ind.ma5 and ind.ma20 and ind.ma60 and ind.ma5 > ind.ma20 > ind.ma60:
                    bull += 1
                net5_lots += inst_net.get(s, 0)
                sec_turnover += turnover.get(s) or 0
                avg_lots5 += (ind.vol_ma20 or 0) / 1000 * 5

            ret5 = sum(rets5) / len(rets5) if rets5 else 0.0
            ret20 = sum(rets20) / len(rets20) if rets20 else 0.0
            frac20, frac60, frac_bull = above20 / n, above60 / n, bull / n

            dim_momentum = clamp(50 + (0.6 * ret5 + 0.4 * ret20) * 4)
            ratio = net5_lots / avg_lots5 if avg_lots5 > 0 else 0
            dim_fund = clamp(50 + ratio * 400)
            dim_tech = clamp(frac20 * 40 + frac60 * 30 + frac_bull * 30)
            strength = WeightedScorer.weighted_total(
                {"momentum": dim_momentum, "fund": dim_fund, "tech": dim_tech}, weights
            )

            rows.append({
                "sector_id": sec_id, "date": td,
                "strength_score": strength,
                "trend_short": _classify_short(ret5, frac20),
                "trend_long": _classify_long(ret20, frac60),
                "rotation_stage": _rotation(frac20, frac60, ret5, ret20),
                "momentum_5": round(ret5, 2), "momentum_20": round(ret20, 2),
                "foreign_net": round(net5_lots),
                "dim_momentum": round(dim_momentum, 1), "dim_fund": round(dim_fund, 1),
                "dim_tech": round(dim_tech, 1),
                "turnover_share": round(sec_turnover / total_turnover * 100, 2),
                "above_ma20": round(frac20, 3), "constituents": n,
            })

        BaseRepository(models.SectorDaily).upsert_many(session, rows)
        session.flush()
        return {"status": "ok", "sectors": len(rows)}
