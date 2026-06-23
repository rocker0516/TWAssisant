"""PoppabilityEfficacyEngine：會噴清單成效回測（波段軌 poppable 風格）。

問對的問題：會噴清單到底準不準？取最近數個「已有 ≥H 個未來交易日」的歷史進場日，
point-in-time 跑**真引擎**的 poppable 風格，對過門檻的清單量後來 H 日的**實際結果**
（有沒有摸到 +10%、最高漲多少、最深回撤），並與「全市場(過會噴硬篩宇宙)」基準對比。

進場價刻意用**進場日當天最高價**（保守，假設買在當天最差價位）——「就算買在最高，後面
還噴得到 +10% 嗎」。摸+10%/回撤/20日收盤皆相對此進場高價，且只看進場日**之後**的 H 根
（不含當天，避免用同日高點作弊）。

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
from .scoring import _DEFAULT_TOP_PCT, _pct_ranks
from .tracks import WaveTrack

_H = 20            # 未來交易日（與會噴定義一致）
_POP_TARGET = 0.10  # 「會噴」門檻：持有期間摸到 +10%
_N_DATES = 6        # 取最近幾個進場日
_SAMPLE = 5         # 每 5 個交易日取一個（約週頻）
_TOPN = 30          # 明細列出檔數
_WARMUP_DAYS = 160  # 載入往前多抓的日曆天（確保 n_bars≥60）


def _f(x) -> float | None:
    return None if x is None or pd.isna(x) else float(x)


def _is_coil(c: dict) -> bool:
    """低位盤整＝低檔盤整打底(consolidation≥50)。

    低位已內建在 consolidation 分數裡（區間中位/高位被低位係數壓到不過門檻），故此處只需
    一個門檻、不再另外判 position。與前端『低位盤整』軟篩同門檻(consolidationMeta ≥50)。
    """
    return (c.get("cons") or 0) >= 50


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

    def _cutoff(self, session: Session) -> float:
        """會噴分數門檻 = 100 − 前N%（與線上 finalize_wave_pop 同一語意）。"""
        row = session.get(models.Setting, "scoring")
        cfg = row.value if row and isinstance(row.value, dict) else {}
        try:
            top_pct = float((cfg.get("wave") or {}).get("top_pct", _DEFAULT_TOP_PCT))
        except (TypeError, ValueError):
            top_pct = _DEFAULT_TOP_PCT
        return 100.0 - top_pct

    def compute(self, session: Session, generated_at: date) -> dict:
        cutoff = self._cutoff(session)
        axis = session.execute(
            select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)
        ).scalars().all()
        # 只取「有 ≥H 個未來交易日」的進場日，週頻取樣、留最近 _N_DATES 個
        eligible = axis[: len(axis) - _H]
        targets = sorted(eligible[::_SAMPLE][-_N_DATES:]) if eligible else []
        if not targets:
            return self._empty(generated_at, cutoff)

        stock_map = {s.id: s for s in session.execute(select(models.Stock)).scalars().all()}
        stock_ids = sorted(stock_map)
        date_lo = min(targets) - timedelta(days=_WARMUP_DAYS)

        # per-date 累積候選（含 rank 原始值 + 實際結果）；排名在收齊全市場後逐日算。
        agg: dict[date, list] = {t: [] for t in targets}
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
                if p is None or p < _MIN_BARS - 1:
                    continue
                ctx = StockContext(
                    stock=stock, date=T, prices=pdf.iloc[: p + 1],
                    inds=ind_g[ind_g["date"] <= T],
                    inst=inst_g[inst_g["date"] <= T] if not inst_g.empty else inst_g,
                    margin=margin_g[margin_g["date"] <= T] if margin_g is not None else None,
                )
                res = self.track.evaluate(ctx, {})
                pin = res.get("pop_inputs") or {}
                atr_pct, ma_align = pin.get("atr_pct"), pin.get("ma_align")
                if atr_pct is None or ma_align is None:  # 不可排名 → 不進當日宇宙
                    continue
                sub = res.get("sub_scores") or {}  # 低位盤整擇時濾網（point-in-time，與線上同算）
                # 實際結果：保守進場 = 進場日當天最高價（最差買點），只看之後 H 根
                c0 = highs[p]
                mfe = dd = cret = None
                hit = has_out = False
                if c0 and c0 > 0 and p + _H < len(closes):
                    fhis = [h for h in highs[p + 1 : p + 1 + _H] if h is not None]
                    flos = [lo for lo in lows[p + 1 : p + 1 + _H] if lo is not None]
                    if fhis and flos:
                        c20 = closes[p + _H]
                        mfe = max(fhis) / c0 - 1
                        dd = min(flos) / c0 - 1
                        cret = (c20 / c0 - 1) if c20 else None
                        hit = mfe >= _POP_TARGET
                        has_out = True
                agg[T].append({
                    "stock_id": sid, "name": stock.name,
                    "atr_pct": atr_pct, "ma_align": ma_align,
                    "pos": sub.get("position"), "cons": sub.get("consolidation"),
                    "passed_filter": bool(res["passed_filter"]),
                    "has_out": has_out, "mfe": mfe, "dd": dd, "cret": cret, "hit": hit,
                })

        by_date = []
        tot_n = tot_hit = 0
        tot_coil_n = tot_coil_hit = 0
        list_by_t: dict[date, list] = {}
        for t in targets:
            cands = agg[t]
            ar = _pct_ranks([c["atr_pct"] for c in cands])
            lr = _pct_ranks([c["ma_align"] for c in cands])
            for c, ra, rl in zip(cands, ar, lr):  # 當天全市場橫截面 rank → 會噴分數
                c["pop"] = round((2.0 * ra + rl) / 3.0 * 100.0, 1) if (ra is not None and rl is not None) else None
            uni = [c for c in cands if c["passed_filter"] and c["has_out"]]  # 過會噴硬篩宇宙(基準)
            uni_n = len(uni)
            uni_hit = sum(1 for c in uni if c["hit"])
            lst = [c for c in uni if c["pop"] is not None and c["pop"] >= cutoff]  # 過硬篩 + 前N%
            n = len(lst)
            hit = sum(1 for c in lst if c["hit"])
            base_rate = round(uni_hit / uni_n, 3) if uni_n else None
            coil = [c for c in lst if _is_coil(c)]  # 清單中再過「低位盤整」擇時濾網
            coil_n = len(coil)
            coil_hit = sum(1 for c in coil if c["hit"])
            tot_n += n
            tot_hit += hit
            tot_coil_n += coil_n
            tot_coil_hit += coil_hit
            list_by_t[t] = lst
            by_date.append({
                "date": t.isoformat(), "n": n,
                "list_hit_rate": round(hit / n, 3) if n else None,
                "base_hit_rate": base_rate,
                "lift": round((hit / n) / base_rate, 2) if (n and base_rate) else None,
                "avg_mfe": round(sum(c["mfe"] for c in lst) / n * 100, 1) if n else None,
                "avg_dd": round(sum(c["dd"] for c in lst) / n * 100, 1) if n else None,
                "coil_n": coil_n,
                "coil_hit_rate": round(coil_hit / coil_n, 3) if coil_n else None,
            })

        latest = targets[-1]
        detail = [
            {
                "stock_id": c["stock_id"], "name": c["name"], "pop": c["pop"],
                "atr": round(c["atr_pct"] * 100, 1),
                "mfe": round(c["mfe"] * 100, 1), "dd": round(c["dd"] * 100, 1),
                "cret": round(c["cret"] * 100, 1) if c["cret"] is not None else None,
                "hit": c["hit"], "coil": _is_coil(c),
            }
            for c in sorted(list_by_t[latest], key=lambda r: -(r["pop"] or 0))[:_TOPN]
        ]
        return {
            "track": "wave", "style": "poppable",
            "generated_at": generated_at.isoformat(),
            "horizon": _H, "pop_target": _POP_TARGET, "threshold": cutoff,
            "window": {"from": targets[0].isoformat(), "to": latest.isoformat(), "entry_dates": len(targets)},
            "by_date": by_date,
            "overall_hit_rate": round(tot_hit / tot_n, 3) if tot_n else None,
            "total_list": tot_n,
            "coil_total": tot_coil_n,
            "coil_overall_hit_rate": round(tot_coil_hit / tot_coil_n, 3) if tot_coil_n else None,
            "detail_date": latest.isoformat(),
            "detail": detail,
            "note": (
                f"取最近 {len(targets)} 個已有 ≥{_H} 交易日未來的進場日，point-in-time 跑真引擎："
                f"當天全市場橫截面算會噴分數，取**前 {100 - cutoff:.0f}%**（分數≥{cutoff:.0f}）為清單。"
                "**進場價=進場日當天最高價**(保守，假設你買在當天最差價位)。「摸+10%」=之後持有期間最高價"
                "較進場再漲 +10%；基準=當日過會噴硬篩宇宙的摸+10%率。清單負責『給停利點』，賺不賺看出場"
                "——故另列最深回撤/20日收盤提醒噴完可能吐回。**低位盤整子集**＝清單中再過『低檔盤整"
                "打底(區間低位＋波動收斂)』的擇時濾網，比對其摸+10%率是否優於全清單，判這個進場濾網的生死。非投資建議。"
            ),
        }

    def _empty(self, generated_at: date, cutoff: float) -> dict:
        return {
            "track": "wave", "style": "poppable", "generated_at": generated_at.isoformat(),
            "horizon": _H, "pop_target": _POP_TARGET, "threshold": cutoff,
            "window": {"from": None, "to": None, "entry_dates": 0},
            "by_date": [], "overall_hit_rate": None, "total_list": 0,
            "coil_total": 0, "coil_overall_hit_rate": None,
            "detail_date": None, "detail": [], "note": "歷史資料不足以回測。",
        }
