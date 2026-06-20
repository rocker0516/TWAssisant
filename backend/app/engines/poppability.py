"""PoppabilityEfficacyEngine：會噴清單成效回測（波段軌 poppable 風格）。

問對的問題：會噴清單到底準不準？取最近數個「已有 ≥H 個未來交易日」的歷史進場日，
point-in-time 跑**真引擎**的 poppable 風格，對過門檻的清單量後來 H 日的**實際結果**
（有沒有摸到 +10%、最高漲多少、最深回撤），並與「全市場(過會噴硬篩宇宙)」基準對比。

誠實定位：清單負責「給你一個停利點」(會噴)，**賺不賺看出場紀律**——故同時呈現
「摸+10%率」(清單的職責) 與「最深回撤 / 20日收盤」(提醒噴完可能吐回去)。
point-in-time 無類股修正(±5~8)，故 poppable 總分與線上推薦略有差異，僅供成效參考、非投資建議。
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from ..storage import models
from .base import BaseEngine
from .calibration import _INST_COLS, _MIN_BARS, _iter_stock_groups
from .context import StockContext
from .tracks import WaveTrack

_H = 20            # 未來交易日（與會噴定義一致）
_POP_TARGET = 0.10  # 「會噴」門檻：持有期間摸到 +10%
_N_DATES = 6        # 取最近幾個進場日
_SAMPLE = 5         # 每 5 個交易日取一個（約週頻）
_TOPN = 30          # 明細列出檔數
_WARMUP_DAYS = 160  # 載入往前多抓的日曆天（確保 n_bars≥60）


def _f(x) -> float | None:
    return None if x is None or pd.isna(x) else float(x)


class PoppabilityEfficacyEngine(BaseEngine):
    name = "poppability_efficacy"

    def __init__(self) -> None:
        self.track = WaveTrack()

    def run(self, session: Session, trading_date: date) -> dict:
        result = self.compute(session, generated_at=trading_date)
        row = session.get(models.Setting, "poppable_efficacy")
        if row is None:
            session.add(models.Setting(key="poppable_efficacy", value=result))
        else:
            row.value = result
        session.flush()
        return {"status": "ok", "entry_dates": len(result["by_date"]), "list_items": result["total_list"]}

    def _threshold(self, session: Session) -> float:
        row = session.get(models.Setting, "scoring")
        cfg = row.value if row and isinstance(row.value, dict) else {}
        try:
            return float((cfg.get("wave") or {}).get("threshold", 70))
        except (TypeError, ValueError):
            return 70.0

    def compute(self, session: Session, generated_at: date) -> dict:
        threshold = self._threshold(session)
        axis = session.execute(
            select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)
        ).scalars().all()
        # 只取「有 ≥H 個未來交易日」的進場日，週頻取樣、留最近 _N_DATES 個
        eligible = axis[: len(axis) - _H]
        targets = sorted(eligible[::_SAMPLE][-_N_DATES:]) if eligible else []
        if not targets:
            return self._empty(generated_at, threshold)

        stock_map = {s.id: s for s in session.execute(select(models.Stock)).scalars().all()}
        stock_ids = sorted(stock_map)
        date_lo = min(targets) - timedelta(days=_WARMUP_DAYS)

        # per-date 累積：list = 過門檻清單(含實際結果)，uni = 過會噴硬篩宇宙的基準命中
        agg: dict[date, dict] = {t: {"list": [], "uni_n": 0, "uni_hit": 0} for t in targets}
        tset = set(targets)
        for sid, pdf, ind_g, inst_g, margin_g in _iter_stock_groups(session, stock_ids, date_lo):
            stock = stock_map.get(sid)
            if stock is None or ind_g is None:
                continue
            if inst_g is None:
                inst_g = pd.DataFrame(columns=_INST_COLS)
            pos_of = {d: i for i, d in enumerate(pdf["date"])}
            highs = [_f(x) for x in pdf["high"]]
            lows = [_f(x) for x in pdf["low"]]
            closes = [_f(x) for x in pdf["close"]]
            for T in targets:
                p = pos_of.get(T)
                if p is None or p < _MIN_BARS - 1 or p + _H >= len(closes):
                    continue
                c0 = closes[p]
                if not c0 or c0 <= 0:
                    continue
                ctx = StockContext(
                    stock=stock, date=T, prices=pdf.iloc[: p + 1],
                    inds=ind_g[ind_g["date"] <= T],
                    inst=inst_g[inst_g["date"] <= T] if not inst_g.empty else inst_g,
                    margin=margin_g[margin_g["date"] <= T] if margin_g is not None else None,
                )
                res = self.track.evaluate(ctx, {})
                if "poppable" not in res["passed_styles"]:
                    continue
                fhis = [h for h in highs[p + 1 : p + 1 + _H] if h is not None]
                flos = [lo for lo in lows[p + 1 : p + 1 + _H] if lo is not None]
                c20 = closes[p + _H]
                if not fhis or not flos:
                    continue
                mfe = max(fhis) / c0 - 1
                dd = min(flos) / c0 - 1
                cret = (c20 / c0 - 1) if c20 else None
                hit = mfe >= _POP_TARGET
                agg[T]["uni_n"] += 1
                agg[T]["uni_hit"] += int(hit)
                pop = (res["style_totals"] or {}).get("poppable", 0.0)
                if pop >= threshold:
                    agg[T]["list"].append({
                        "stock_id": sid, "name": stock.name, "pop": round(pop, 1),
                        "vol": round((res["sub_scores"] or {}).get("volatility", 0)),
                        "mfe": round(mfe * 100, 1), "dd": round(dd * 100, 1),
                        "cret": round(cret * 100, 1) if cret is not None else None,
                        "hit": hit,
                    })

        by_date = []
        tot_n = tot_hit = 0
        for t in targets:
            lst = agg[t]["list"]
            n = len(lst)
            hit = sum(1 for r in lst if r["hit"])
            uni = agg[t]["uni_n"]
            base_rate = round(agg[t]["uni_hit"] / uni, 3) if uni else None
            tot_n += n
            tot_hit += hit
            by_date.append({
                "date": t.isoformat(), "n": n,
                "list_hit_rate": round(hit / n, 3) if n else None,
                "base_hit_rate": base_rate,
                "lift": round((hit / n) / base_rate, 2) if (n and base_rate) else None,
                "avg_mfe": round(sum(r["mfe"] for r in lst) / n, 1) if n else None,
                "avg_dd": round(sum(r["dd"] for r in lst) / n, 1) if n else None,
            })

        latest = targets[-1]
        detail = sorted(agg[latest]["list"], key=lambda r: -r["pop"])[:_TOPN]
        return {
            "track": "wave", "style": "poppable",
            "generated_at": generated_at.isoformat(),
            "horizon": _H, "pop_target": _POP_TARGET, "threshold": threshold,
            "window": {"from": targets[0].isoformat(), "to": latest.isoformat(), "entry_dates": len(targets)},
            "by_date": by_date,
            "overall_hit_rate": round(tot_hit / tot_n, 3) if tot_n else None,
            "total_list": tot_n,
            "detail_date": latest.isoformat(),
            "detail": detail,
            "note": (
                f"取最近 {len(targets)} 個已有 ≥{_H} 交易日未來的進場日，point-in-time 跑真引擎 "
                f"poppable 風格(門檻≥{threshold:.0f})。「摸+10%」=持有期間最高價達 +10%；基準=當日"
                "過會噴硬篩宇宙的摸+10%率。清單負責『給停利點』，賺不賺看出場——故另列最深回撤/"
                "20日收盤提醒噴完可能吐回。point-in-time 無類股修正，與線上推薦略差。非投資建議。"
            ),
        }

    def _empty(self, generated_at: date, threshold: float) -> dict:
        return {
            "track": "wave", "style": "poppable", "generated_at": generated_at.isoformat(),
            "horizon": _H, "pop_target": _POP_TARGET, "threshold": threshold,
            "window": {"from": None, "to": None, "entry_dates": 0},
            "by_date": [], "overall_hit_rate": None, "total_list": 0,
            "detail_date": None, "detail": [], "note": "歷史資料不足以回測。",
        }
