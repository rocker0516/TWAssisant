"""FlowEngine：法人 + 大戶散戶資金動向分析（市場 / 類股 / 個股，分外資/投信/自營）。

純查詢 + 計算，不落引擎結果表（盤後算太重的 inst_price_relation 才快取進 Setting）。
三個視角，皆以「整個週期 / 多日累積」為核心，而非看當天：

  market_flow      全市場三大法人累積淨買超曲線（疊加權指數）+ phase 週期判斷
                   + 法人 vs 指數漲跌量化關係。各 actor（合計/外資/投信/自營）分開算。
  sector_flow      近 20 日各類股法人淨買超累計（哪些類股法人錢流入/流出），分 actor。
  stock_flow_ranking  個股榜：各 actor 近 20/60 日累計 + 連買天數 + 大戶/散戶/股東動向。
  inst_price_relation 個股「法人累積 → 未來報酬」rank-IC / 勝率（point-in-time，較重，快取）。

actor 維度：法人三者方向常分歧（外資買、投信賣意義不同），故全程支援個別檢視。
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from ..storage import models
from .calibration import _iter_stock_groups

# actor → 對應欄位（個股 institutional 與市場 institutional_market_total 同名）
_ACTORS = {
    "total": "total_net",
    "foreign": "foreign_net",
    "trust": "trust_net",
    "dealer": "dealer_net",
}
_REL_H = 20  # 未來交易日（看法人累積對之後報酬的關係）
_REL_N_DATES = 6  # 取樣的歷史進場日數
_REL_SAMPLE = 5  # 每 5 個交易日取一個


def _f(x) -> float | None:
    return None if x is None or pd.isna(x) else float(x)


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx ** 0.5 * vy ** 0.5)


def _ranks(xs: list[float]) -> list[float]:
    """平均序位（spearman 用）。"""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def _consec(daily: list[float | None]) -> int:
    """尾端連買/連賣天數：正＝連買、負＝連賣（同號連續、0 或 None 中斷）。"""
    run = 0
    sign = 0
    for v in reversed(daily):
        if v is None or v == 0:
            break
        s = 1 if v > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            break
        run += 1
    return run * sign


class FlowEngine:
    name = "flow"

    # ───────────────────────── 市場層 ─────────────────────────

    def market_flow(self, session: Session, days: int = 250) -> dict:
        """全市場三大法人累積曲線 + 多尺度累計 + phase + 疊指數 + 量化關係（各 actor）。"""
        rows = session.execute(
            select(
                models.InstitutionalMarketTotal.date,
                models.InstitutionalMarketTotal.foreign_net,
                models.InstitutionalMarketTotal.trust_net,
                models.InstitutionalMarketTotal.dealer_net,
                models.InstitutionalMarketTotal.total_net,
            ).order_by(models.InstitutionalMarketTotal.date)
        ).all()
        if not rows:
            return {"from_date": None, "to_date": None, "dates": [], "index": [], "actors": {}}
        rows = rows[-days:]
        dates = [r[0] for r in rows]
        # 對齊加權指數
        idx_map = dict(
            session.execute(
                select(models.MarketIndex.date, models.MarketIndex.close)
                .where(models.MarketIndex.date >= dates[0])
            ).all()
        )
        index = [_f(idx_map.get(d)) for d in dates]

        col_pos = {"foreign_net": 1, "trust_net": 2, "dealer_net": 3, "total_net": 4}
        actors: dict[str, dict] = {}
        for actor, col in _ACTORS.items():
            daily = [_f(r[col_pos[col]]) for r in rows]
            cum, run = [], 0.0
            for v in daily:
                run += v or 0.0
                cum.append(round(run, 1))
            actors[actor] = {
                "daily": daily,
                "cum": cum,
                "cum20": self._tail_sum(daily, 20),
                "cum60": self._tail_sum(daily, 60),
                "cum120": self._tail_sum(daily, 120),
                "consec_days": _consec(daily),
                "phase": self._phase(daily),
                "relation": self._market_relation(daily, index, _REL_H),
            }
        return {
            "from_date": dates[0].isoformat(),
            "to_date": dates[-1].isoformat(),
            "dates": [d.isoformat() for d in dates],
            "index": index,
            "actors": actors,
        }

    @staticmethod
    def _tail_sum(daily: list[float | None], n: int) -> float | None:
        tail = [v for v in daily[-n:] if v is not None]
        return round(sum(tail), 1) if tail else None

    def _phase(self, daily: list[float | None]) -> str:
        """以多尺度累計判週期段：持續買/賣超循環、由賣轉買/由買轉賣、區間整理。"""
        c20 = self._tail_sum(daily, 20) or 0.0
        c60 = self._tail_sum(daily, 60) or 0.0
        c120 = self._tail_sum(daily, 120) or 0.0
        if c20 > 0 and c60 > 0 and c120 >= 0:
            return "持續買超循環"
        if c20 < 0 and c60 < 0 and c120 <= 0:
            return "持續賣超循環"
        if c20 > 0 and c60 <= 0:
            return "由賣轉買"
        if c20 < 0 and c60 >= 0:
            return "由買轉賣"
        return "區間整理"

    def _market_relation(
        self, daily: list[float | None], index: list[float | None], h: int
    ) -> dict | None:
        """法人 20 日累計 vs 指數未來 h 日報酬：正/負累計時的平均報酬、勝率、相關係數。"""
        sig: list[float] = []
        ret: list[float] = []
        n = len(daily)
        for i in range(20, n - h):
            window = [v for v in daily[i - 20 : i] if v is not None]
            if not window:
                continue
            c20 = sum(window)
            i0, ih = index[i], index[i + h]
            if i0 is None or ih is None or i0 <= 0:
                continue
            sig.append(c20)
            ret.append(ih / i0 - 1.0)
        if len(sig) < 5:
            return None
        pos = [r for s, r in zip(sig, ret) if s > 0]
        neg = [r for s, r in zip(sig, ret) if s < 0]
        ap, an = _mean(pos), _mean(neg)
        return {
            "h": h,
            "samples": len(sig),
            "avg_ret_pos": round(ap * 100, 2) if ap is not None else None,
            "avg_ret_neg": round(an * 100, 2) if an is not None else None,
            "winrate_pos": round(sum(1 for r in pos if r > 0) / len(pos), 3) if pos else None,
            "corr": round(_pearson(sig, ret), 3) if _pearson(sig, ret) is not None else None,
        }

    # ───────────────────────── 類股層 ─────────────────────────

    def sector_flow(self, session: Session, lookback: int = 20) -> dict:
        """近 lookback 交易日各類股法人淨買超累計（張，分 actor）。前端依選定 actor 排序。"""
        axis = self._inst_axis(session, lookback)
        if not axis:
            return {"date": None, "lookback": lookback, "items": []}
        d_lo = axis[0]
        rows = session.execute(
            select(
                models.Stock.sector_id,
                models.Sector.name,
                func.sum(models.Institutional.foreign_net),
                func.sum(models.Institutional.trust_net),
                func.sum(models.Institutional.dealer_net),
                func.sum(models.Institutional.total_net),
                func.count(distinct(models.Institutional.stock_id)),
            )
            .join(models.Stock, models.Institutional.stock_id == models.Stock.id)
            .join(models.Sector, models.Stock.sector_id == models.Sector.id)
            .where(models.Institutional.date >= d_lo, models.Stock.is_etf.is_(False))
            .group_by(models.Stock.sector_id)
        ).all()
        items = [
            {
                "id": sid,
                "name": name,
                "foreign_cum": int(fn) if fn is not None else None,
                "trust_cum": int(tn) if tn is not None else None,
                "dealer_cum": int(dn) if dn is not None else None,
                "total_cum": int(ttl) if ttl is not None else None,
                "constituents": int(cnt),
            }
            for sid, name, fn, tn, dn, ttl, cnt in rows
        ]
        items.sort(key=lambda r: (r["total_cum"] if r["total_cum"] is not None else 0), reverse=True)
        return {"date": axis[-1].isoformat(), "lookback": lookback, "items": items}

    def sector_rotation(
        self, session: Session, *, actor: str = "total", weeks: int = 6
    ) -> dict:
        """類股資金輪動象限圖資料：每類股近 weeks 週軌跡。

        每個週錨點回傳原始量(net20/net5/turnover20/turnover5)，前端依「強度/絕對」模式
        各自算 X(強度=淨買超佔成交比 或 絕對淨買超)、Y(加速度=近5日流速−近20日流速)、
        size(資金量=20日成交張)。排除 ETF。
        """
        col = _ACTORS.get(actor, "total_net")
        axis = self._inst_axis(session, 5 * weeks + 25)
        if len(axis) < 25:
            return {"actor": actor, "weeks": weeks, "date": None, "sectors": []}
        # 週錨點（每 5 個交易日一個，最近 weeks 個，需 index≥19 才有 20 日窗）
        idxs = [len(axis) - 1 - 5 * k for k in range(weeks)]
        idxs = sorted(i for i in idxs if i >= 19)
        if not idxs:
            return {"actor": actor, "weeks": weeks, "date": None, "sectors": []}
        lo = axis[idxs[0] - 19]

        sid_to_sector = dict(
            session.execute(
                select(models.Stock.id, models.Stock.sector_id)
                .where(models.Stock.is_etf.is_(False), models.Stock.sector_id.is_not(None))
            ).all()
        )
        sector_names = dict(session.execute(select(models.Sector.id, models.Sector.name)).all())
        # 只留真產業類股（成分股 ≥5），濾掉 ETN/指數型等少數成分的偽類股、避免象限圖雜訊
        sec_count: dict[int, int] = {}
        for sec in sid_to_sector.values():
            sec_count[sec] = sec_count.get(sec, 0) + 1
        real_sectors = {sec for sec, n in sec_count.items() if n >= 5}

        inst_rows = session.execute(
            select(models.Institutional.stock_id, models.Institutional.date, getattr(models.Institutional, col))
            .where(models.Institutional.date >= lo)
        ).all()
        vol_rows = session.execute(
            select(models.DailyPrice.stock_id, models.DailyPrice.date, models.DailyPrice.volume)
            .where(models.DailyPrice.date >= lo)
        ).all()
        if not inst_rows:
            return {"actor": actor, "weeks": weeks, "date": None, "sectors": []}

        ndf = pd.DataFrame(inst_rows, columns=["stock_id", "date", "net"])
        ndf["sector"] = ndf["stock_id"].map(sid_to_sector)
        ndf = ndf.dropna(subset=["sector"])
        net_piv = ndf.pivot_table(index="date", columns="sector", values="net", aggfunc="sum").fillna(0).sort_index()

        vdf = pd.DataFrame(vol_rows, columns=["stock_id", "date", "vol"])
        vdf["sector"] = vdf["stock_id"].map(sid_to_sector)
        vdf = vdf.dropna(subset=["sector"])
        vdf["vol"] = vdf["vol"].fillna(0) / 1000.0  # 股→張
        vol_piv = vdf.pivot_table(index="date", columns="sector", values="vol", aggfunc="sum").fillna(0).sort_index()

        def wsum(piv: pd.DataFrame, dates: list) -> pd.Series:
            return piv.reindex(dates).fillna(0).sum()

        # 每錨點各 sector 的四個量
        per_anchor: list[tuple] = []  # (date, net20, net5, vol20, vol5) Series
        for i in idxs:
            w20 = list(axis[i - 19 : i + 1])
            w5 = list(axis[i - 4 : i + 1])
            per_anchor.append((
                axis[i],
                wsum(net_piv, w20), wsum(net_piv, w5),
                wsum(vol_piv, w20), wsum(vol_piv, w5),
            ))

        all_sectors = [s for s in (set(net_piv.columns) | set(vol_piv.columns)) if s in real_sectors]
        sectors_out: list[dict] = []
        for sec in all_sectors:
            name = sector_names.get(int(sec))
            if not name:
                continue
            pts = []
            for d, n20, n5, v20, v5 in per_anchor:
                pts.append({
                    "date": d.isoformat(),
                    "net20": round(float(n20.get(sec, 0.0))),
                    "net5": round(float(n5.get(sec, 0.0))),
                    "turnover20": round(float(v20.get(sec, 0.0))),
                    "turnover5": round(float(v5.get(sec, 0.0))),
                })
            sectors_out.append({"id": int(sec), "name": name, "points": pts})
        sectors_out.sort(key=lambda s: s["points"][-1]["turnover20"], reverse=True)
        return {
            "actor": actor,
            "weeks": len(idxs),
            "date": axis[idxs[-1]].isoformat(),
            "sectors": sectors_out,
        }

    # ───────────────────────── 個股層 ─────────────────────────

    def stock_flow_ranking(
        self, session: Session, *, sort: str = "total_cum20", limit: int = 50
    ) -> dict:
        """個股籌碼榜：各 actor 近 20/60 日累計 + 連買天數 + 大戶/散戶/股東動向。"""
        axis = self._inst_axis(session, 60)
        if not axis:
            return {"date": None, "sort": sort, "items": []}
        d60 = axis[0]
        d20 = axis[-20] if len(axis) >= 20 else axis[0]
        latest = axis[-1]

        inst = pd.DataFrame(
            session.execute(
                select(
                    models.Institutional.stock_id,
                    models.Institutional.date,
                    models.Institutional.foreign_net,
                    models.Institutional.trust_net,
                    models.Institutional.dealer_net,
                    models.Institutional.total_net,
                )
                .where(models.Institutional.date >= d60)
                .order_by(models.Institutional.stock_id, models.Institutional.date)
            ).all(),
            columns=["stock_id", "date", "foreign_net", "trust_net", "dealer_net", "total_net"],
        )
        if inst.empty:
            return {"date": latest.isoformat(), "sort": sort, "items": []}

        names = dict(session.execute(select(models.Stock.id, models.Stock.name)).all())
        sectors = dict(
            session.execute(
                select(models.Stock.id, models.Sector.name)
                .join(models.Sector, models.Stock.sector_id == models.Sector.id)
            ).all()
        )
        # 個股榜聚焦真個股，排除 ETF（ETF 法人張數量級大、會洗版且非選股標的）
        etf_ids = set(
            session.execute(select(models.Stock.id).where(models.Stock.is_etf.is_(True))).scalars().all()
        )
        hold = self._holding_trends(session)
        closes = self._latest_changes(session, latest)

        items: list[dict] = []
        for sid, g in inst.groupby("stock_id", sort=False):
            if sid in etf_ids:
                continue
            g20 = g[g["date"] >= d20]
            row = {"stock_id": sid, "name": names.get(sid, sid), "sector_name": sectors.get(sid)}
            for actor, col in _ACTORS.items():
                key = actor if actor != "total" else "total"
                row[f"{key}_cum20"] = int(g20[col].fillna(0).sum())
                row[f"{key}_cum60"] = int(g[col].fillna(0).sum())
            row["consec_days"] = _consec([_f(v) for v in g["total_net"].tolist()])
            h = hold.get(sid, {})
            row["big_trend"] = h.get("big_trend")
            row["small_trend"] = h.get("small_trend")
            row["holders_change"] = h.get("holders_change")
            row["big_pct"] = h.get("big_pct")
            c = closes.get(sid, {})
            row["close"] = c.get("close")
            row["change_pct"] = c.get("change_pct")
            items.append(row)

        items.sort(key=lambda r: (r.get(sort) if r.get(sort) is not None else -1e18), reverse=True)
        return {"date": latest.isoformat(), "sort": sort, "items": items[:limit]}

    def _holding_trends(self, session: Session) -> dict[str, dict]:
        """每檔集保大戶/散戶/股東近 ~8 週變化（最新 − 約 8 週前）。"""
        rows = session.execute(
            select(
                models.ShareholdingDistribution.stock_id,
                models.ShareholdingDistribution.date,
                models.ShareholdingDistribution.big_pct,
                models.ShareholdingDistribution.small_pct,
                models.ShareholdingDistribution.holders,
            ).order_by(
                models.ShareholdingDistribution.stock_id,
                models.ShareholdingDistribution.date,
            )
        ).all()
        if not rows:
            return {}
        df = pd.DataFrame(rows, columns=["stock_id", "date", "big_pct", "small_pct", "holders"])
        out: dict[str, dict] = {}
        for sid, g in df.groupby("stock_id", sort=False):
            g = g.dropna(subset=["big_pct"])
            if g.empty:
                continue
            last = g.iloc[-1]
            ref = g.iloc[-9] if len(g) > 8 else g.iloc[0]
            big_t = _f(last["big_pct"]) - _f(ref["big_pct"]) if _f(ref["big_pct"]) is not None else None
            sm_l, sm_r = _f(last["small_pct"]), _f(ref["small_pct"])
            small_t = sm_l - sm_r if sm_l is not None and sm_r is not None else None
            h_l, h_r = _f(last["holders"]), _f(ref["holders"])
            hold_c = round((h_l - h_r) / h_r * 100, 2) if h_l is not None and h_r else None
            out[sid] = {
                "big_pct": round(_f(last["big_pct"]), 2) if _f(last["big_pct"]) is not None else None,
                "big_trend": round(big_t, 2) if big_t is not None else None,
                "small_trend": round(small_t, 2) if small_t is not None else None,
                "holders_change": hold_c,
            }
        return out

    def _latest_changes(self, session: Session, latest: date) -> dict[str, dict]:
        """每檔最新收盤 + 當日漲跌%（取 ≤latest 最後兩筆）。"""
        rows = session.execute(
            select(models.DailyPrice.stock_id, models.DailyPrice.date, models.DailyPrice.close)
            .where(models.DailyPrice.date <= latest, models.DailyPrice.date >= latest - timedelta(days=10))
            .order_by(models.DailyPrice.stock_id, models.DailyPrice.date)
        ).all()
        df = pd.DataFrame(rows, columns=["stock_id", "date", "close"])
        out: dict[str, dict] = {}
        for sid, g in df.groupby("stock_id", sort=False):
            cl = g["close"].dropna()
            if cl.empty:
                continue
            close = _f(cl.iloc[-1])
            prev = _f(cl.iloc[-2]) if len(cl) > 1 else None
            chg = round((close - prev) / prev * 100, 2) if close is not None and prev else None
            out[sid] = {"close": close, "change_pct": chg}
        return out

    def _inst_axis(self, session: Session, n: int) -> list[date]:
        """個股法人資料的近 n 個交易日（升冪）。"""
        axis = session.execute(
            select(distinct(models.Institutional.date)).order_by(models.Institutional.date)
        ).scalars().all()
        return list(axis[-n:])

    # ─────────────── 個股「法人累積→未來報酬」量化（IC/勝率，較重）───────────────

    def inst_price_relation(self, session: Session, generated_at: date | None = None) -> dict:
        """各 actor 近 20 日累計（法人因子）對未來 20 日報酬的 rank-IC / 勝率。

        point-in-time：取最近數個「已有 ≥H 未來交易日」的日子，跨股池橫截面算 spearman
        rank-IC，多日平均；勝率＝法人累積為正的標的之後上漲比例。重用串流載入防 OOM。
        """
        axis = session.execute(
            select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)
        ).scalars().all()
        eligible = axis[: len(axis) - _REL_H]
        targets = sorted(eligible[::_REL_SAMPLE][-_REL_N_DATES:]) if eligible else []
        if not targets:
            return self._empty_relation(generated_at)

        stock_ids = sorted(session.execute(select(models.Stock.id)).scalars().all())
        date_lo = min(targets) - timedelta(days=80)

        # per (actor, date) 累積候選：[(factor, fwd_ret)]
        agg: dict[str, dict[date, list]] = {a: {t: [] for t in targets} for a in _ACTORS}
        for sid, pdf, ind_g, inst_g, margin_g in _iter_stock_groups(session, stock_ids, date_lo):
            if inst_g is None or inst_g.empty:
                continue
            pos_of = {d: i for i, d in enumerate(pdf["date"])}
            closes = [_f(x) for x in pdf["close"]]
            for T in targets:
                p = pos_of.get(T)
                if p is None or p + _REL_H >= len(closes):
                    continue
                c0, ch = closes[p], closes[p + _REL_H]
                if c0 is None or ch is None or c0 <= 0:
                    continue
                fwd = ch / c0 - 1.0
                win = inst_g[inst_g["date"] <= T].tail(20)
                if win.empty:
                    continue
                for actor, col in _ACTORS.items():
                    if col not in win:
                        continue
                    agg[actor][T].append((float(win[col].fillna(0).sum()), fwd))

        actors_out: dict[str, dict] = {}
        for actor in _ACTORS:
            ics: list[float] = []
            pos_rets: list[float] = []
            n_total = 0
            for t in targets:
                cands = agg[actor][t]
                if len(cands) < 10:
                    continue
                n_total += len(cands)
                fx = [c[0] for c in cands]
                fy = [c[1] for c in cands]
                ic = _pearson(_ranks(fx), _ranks(fy))
                if ic is not None:
                    ics.append(ic)
                pos_rets += [r for s, r in cands if s > 0]
            ic_avg = _mean(ics)
            actors_out[actor] = {
                "ic": round(ic_avg, 3) if ic_avg is not None else None,
                "winrate_pos": round(sum(1 for r in pos_rets if r > 0) / len(pos_rets), 3) if pos_rets else None,
                "avg_ret_pos": round(_mean(pos_rets) * 100, 2) if pos_rets else None,
                "samples": n_total,
            }
        return {
            "generated_at": (generated_at or targets[-1]).isoformat(),
            "horizon": _REL_H,
            "entry_dates": len(targets),
            "actors": actors_out,
            "note": (
                f"取最近 {len(targets)} 個已有 ≥{_REL_H} 交易日未來的日子，跨全市場橫截面算"
                "「法人近 20 日累計淨買超」對未來 20 日報酬的 spearman rank-IC（多日平均）。"
                "勝率＝法人累積買超(>0)的標的之後上漲比例。IC>0 代表法人累積越多後續越會漲；"
                "可比較外資/投信/自營誰最有領先性。純歷史統計、非投資建議。"
            ),
        }

    def _empty_relation(self, generated_at: date | None) -> dict:
        return {
            "generated_at": generated_at.isoformat() if generated_at else None,
            "horizon": _REL_H, "entry_dates": 0,
            "actors": {a: {"ic": None, "winrate_pos": None, "avg_ret_pos": None, "samples": 0} for a in _ACTORS},
            "note": "歷史資料不足以回測。",
        }
