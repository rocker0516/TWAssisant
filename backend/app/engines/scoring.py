"""ScoringEngine（架構③）：批次載入 → 每檔建 StockContext → 跑雙軌 → 落 scores。

規則不各自查 DB：此處一次把 price/indicator/法人 載進記憶體、依股號切片建 context。
每檔每軌產一列（passed 標記是否進推薦）。配分/門檻由 settings 'scoring' 覆寫。
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from ..storage import models
from ..storage.repositories import BaseRepository
from .base import BaseEngine
from .context import StockContext
from .rules.base import clamp
from .tracks import LongTrack, WaveTrack

_STABILITY_LOOKBACK = 5  # 取近 5 個評分日算分數穩定度
_STABILITY_MIN_POINTS = 3  # 含今日至少 3 點才談穩定度，否則中性不扣
_DEFAULT_TOP_PCT = 20.0  # 會噴推薦預設前 N%（門檻 = 100 − N；設定/推薦頁橫桿可調）


def _pct_ranks(values: list[float | None]) -> list[float | None]:
    """橫截面百分位 rank（0~1，越大越高）。None 不參與排名、回 None。

    平手取平均序位 / 有效樣本數。空樣本回全 None。
    """
    valid = sorted(v for v in values if v is not None)
    n = len(valid)
    if n == 0:
        return [None] * len(values)
    # 每個值的平均序位（1-based 中點），平手共享 → 轉 0~1
    import bisect

    out: list[float | None] = []
    for v in values:
        if v is None:
            out.append(None)
            continue
        lo = bisect.bisect_left(valid, v)
        hi = bisect.bisect_right(valid, v)
        avg_rank = (lo + 1 + hi) / 2.0  # 平手取中點序位（1-based）
        out.append(avg_rank / n)
    return out


def finalize_wave_pop(rows: list[dict], top_pct: float = _DEFAULT_TOP_PCT) -> None:
    """會噴分數＝當天全市場橫截面 rank：(2×rank(atr_pct)+rank(ma_align))/3×100。

    就地改寫 wave 列：填 total_score / passed / coverage / confidence，並移除 transient
    的 pop_inputs（非 Score 欄位）。cutoff = 100 − top_pct（前 N% 進推薦）。
    rank 範圍 = 傳入 rows 中的全部 wave 列（一個交易日的全市場），自動隨大盤波動正規化。
    """
    wave = [r for r in rows if r.get("track") == "wave"]
    if not wave:
        return
    atr_rank = _pct_ranks([(r.get("pop_inputs") or {}).get("atr_pct") for r in wave])
    align_rank = _pct_ranks([(r.get("pop_inputs") or {}).get("ma_align") for r in wave])
    # 2026-07 答案反推定版：+pos_52w/pb（清單內 IC 挖掘窗+holdout 雙活），缺值中性 0.5。
    # 混合 (2atr+align+pos+pb)/5 挖掘窗 46.9% vs 舊二因子 43.8%，三段全贏、MAE 不變。
    pos_rank = _pct_ranks([(r.get("pop_inputs") or {}).get("pos_52w") for r in wave])
    pb_rank = _pct_ranks([(r.get("pop_inputs") or {}).get("pb") for r in wave])
    cutoff = 100.0 - float(top_pct)
    # 合成 → 再做一次全市場百分位重排名：平均式合成會向中間集中（分數≥80 實切僅前
    # ~6%，橫桿「前N%」名不符實=漏標的）；重排名後 分數≥100−N ⟺ 真·前N%，
    # 且回測嚴格度單調（前20% 43.1% → 前3% 47.6%，三段皆穩）。
    comps: list[float | None] = []
    presents: list[int] = []
    for ra, rl, rp, rb in zip(atr_rank, align_rank, pos_rank, pb_rank):
        if ra is None or rl is None:
            comps.append(None)
            presents.append(0)
            continue
        presents.append(2 + int(rp is not None) + int(rb is not None))
        rp = 0.5 if rp is None else rp
        rb = 0.5 if rb is None else rb
        comps.append((2.0 * ra + rl + rp + rb) / 5.0)
    total_rank = _pct_ranks(comps)
    for r, tr, n_present in zip(wave, total_rank, presents):
        r.pop("pop_inputs", None)
        if tr is None:
            r["total_score"] = None
            r["passed"] = False
            r["coverage"] = 0.0
            r["confidence"] = 0.0
            continue
        total = round(tr * 100.0, 2)
        r["total_score"] = total
        # 完整度：4 個 rank 因子缺幾個扣幾個（pos/pb 缺值中性補但誠實降 confidence）
        r["coverage"] = round(n_present / 4.0, 2)
        r["confidence"] = round(100.0 * n_present / 4.0, 1)  # 完整度；穩定度於 _apply_stability 再折入
        r["passed"] = bool(r.get("passed_filter")) and total >= cutoff


def _stability_factor(prior_totals: list[float | None], today: float | None) -> float:
    """分數穩定度係數 0.8~1.0（L3）：近期總分波動越大越不可信。

    刻意做成「輕推」——技術分天生隨行情起伏，過重會把整條軌壓平、失去鑑別度
    （鑑別交給共識度）。標準差以 60 分正規化、下限 0.8（最多扣 20%）；史料不足回
    1.0 中性。穩定度另存欄位、tooltip 透明顯示。
    """
    vals = [v for v in [*prior_totals, today] if v is not None]
    if len(vals) < _STABILITY_MIN_POINTS:
        return 1.0
    mean = sum(vals) / len(vals)
    std = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
    return clamp(1.0 - std / 60.0, 0.8, 1.0)


# 評分只需近窗（規則最長用到 ma60 + 前低 + 斜率）；MA240 等長均線已在 indicators
# 表預先算好、只讀最新列。下界避免回補長歷史後把全市場×多年 ORM 全載進記憶體（OOM）。
_SCORING_LOOKBACK_DAYS = 400


def _load_groups(
    session: Session, model, cols: list[str], td: date,
    lookback_days: int = _SCORING_LOOKBACK_DAYS,
) -> dict[str, pd.DataFrame]:
    """載入某表 [td-lookback, td] 的資料，依 stock_id 分組（升冪）。"""
    stmt = (
        select(model)
        .where(model.date <= td, model.date >= td - timedelta(days=lookback_days))
        .order_by(model.stock_id, model.date)
    )
    rows = session.execute(stmt).scalars().all()
    if not rows:
        return {}
    df = pd.DataFrame([{c: getattr(r, c) for c in cols} for r in rows])
    return {sid: g.reset_index(drop=True) for sid, g in df.groupby("stock_id", sort=False)}


def _load_latest(session: Session, model, cols: list[str], order_cols: list) -> dict[str, pd.Series]:
    """每檔最新一筆（估值），回 stock_id→Series。"""
    rows = session.execute(select(model).order_by(*order_cols)).scalars().all()
    out: dict[str, pd.Series] = {}
    for r in rows:  # 升冪 → 後者覆寫，最終留最新
        out[r.stock_id] = pd.Series({c: getattr(r, c) for c in cols})
    return out


def _load_revenue_history(session: Session, td: date) -> dict[str, pd.DataFrame]:
    """月營收全歷史，依「公布時點」切片（point-in-time）：(y,m) 於次月 10 日可得。

    長線軌（釣大魚）的持續性/加速度因子需要多期歷史；切片讓評分只用當日已公布
    的資料，未來回測可直接沿用。
    """
    rows = session.execute(
        select(
            models.RevenueMonthly.stock_id, models.RevenueMonthly.year,
            models.RevenueMonthly.month, models.RevenueMonthly.revenue,
            models.RevenueMonthly.yoy, models.RevenueMonthly.mom,
        ).order_by(models.RevenueMonthly.stock_id, models.RevenueMonthly.year, models.RevenueMonthly.month)
    ).all()
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=["stock_id", "year", "month", "revenue", "yoy", "mom"])
    next_m = df["month"] % 12 + 1
    next_y = df["year"] + (df["month"] == 12).astype(int)
    avail = pd.to_datetime(dict(year=next_y, month=next_m, day=10))
    df = df[avail <= pd.Timestamp(td)]
    return {sid: g.reset_index(drop=True) for sid, g in df.groupby("stock_id", sort=False)}


# 季報法定申報期限：Q1→5/15、Q2→8/14、Q3→11/14、Q4(年報)→次年3/31
_FIN_DEADLINES = {1: (0, 5, 15), 2: (0, 8, 14), 3: (0, 11, 14), 4: (1, 3, 31)}


def _load_financial_history(session: Session, td: date) -> dict[str, pd.DataFrame]:
    """季財報（單季化）全歷史，依申報期限切片（保守：期限日起才視為可得）。"""
    rows = session.execute(
        select(
            models.FinancialQuarter.stock_id, models.FinancialQuarter.year,
            models.FinancialQuarter.quarter, models.FinancialQuarter.eps,
            models.FinancialQuarter.gross_margin, models.FinancialQuarter.op_margin,
            models.FinancialQuarter.net_margin,
        ).order_by(models.FinancialQuarter.stock_id, models.FinancialQuarter.year, models.FinancialQuarter.quarter)
    ).all()
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=["stock_id", "year", "quarter", "eps", "gross_margin", "op_margin", "net_margin"])
    dl = df["quarter"].map(_FIN_DEADLINES)
    avail = pd.to_datetime(dict(
        year=df["year"] + dl.str[0], month=dl.str[1], day=dl.str[2],
    ))
    df = df[avail <= pd.Timestamp(td)]
    return {sid: g.reset_index(drop=True) for sid, g in df.groupby("stock_id", sort=False)}


def _fund_relatives(
    rev_hist: dict[str, pd.DataFrame],
    valuation: dict[str, pd.Series],
    stock_map: dict,
    min_sector_n: int = 15,
) -> tuple[dict[str, float], dict[int, float]]:
    """類股相對量尺（原則：量尺相對化、定義保持絕對）。

    回 (yoy3m_rank per stock（類股內百分位；小類股退全市場）, pe 中位 per sector_id)。
    """
    yoy3m: dict[str, float] = {}
    for sid, g in rev_hist.items():
        tail = [float(v) for v in g["yoy"].iloc[-3:] if pd.notna(v)]
        if len(tail) == 3:
            yoy3m[sid] = sum(tail) / 3
    # 依類股分組排名；樣本太小的類股集中到全市場池
    by_sector: dict[int | None, list[str]] = {}
    for sid in yoy3m:
        st = stock_map.get(sid)
        by_sector.setdefault(st.sector_id if st else None, []).append(sid)
    global_pool: list[str] = []
    rank_map: dict[str, float] = {}
    for sec_id, sids in by_sector.items():
        if sec_id is None or len(sids) < min_sector_n:
            global_pool.extend(sids)
            continue
        ranks = _pct_ranks([yoy3m[s] for s in sids])
        rank_map.update({s: r for s, r in zip(sids, ranks) if r is not None})
    if global_pool:
        ranks = _pct_ranks([yoy3m[s] for s in global_pool])
        rank_map.update({s: r for s, r in zip(global_pool, ranks) if r is not None})

    pe_by_sector: dict[int, list[float]] = {}
    for sid, v in valuation.items():
        st = stock_map.get(sid)
        pe = v.get("pe")
        if st and st.sector_id is not None and pe is not None and not pd.isna(pe) and pe > 0:
            pe_by_sector.setdefault(st.sector_id, []).append(float(pe))
    pe_median = {
        sec: float(pd.Series(vals).median()) for sec, vals in pe_by_sector.items() if len(vals) >= 5
    }
    return rank_map, pe_median


class ScoringEngine(BaseEngine):
    name = "scoring"

    def __init__(self) -> None:
        self.tracks = [WaveTrack(), LongTrack()]

    def _config(self, session: Session) -> dict:
        row = session.get(models.Setting, "scoring")
        return row.value if row and isinstance(row.value, dict) else {}

    def run(self, session: Session, trading_date: date) -> dict:
        td = trading_date
        config = self._config(session)

        price_cols = ["stock_id", "date", "open", "high", "low", "close", "volume"]
        ind_cols = [
            "stock_id", "date", "ma5", "ma10", "ma20", "ma60", "vol_ma5", "vol_ma20",
            "kd_k", "kd_d", "macd", "macd_signal", "macd_hist", "atr14", "bias_20", "bias_60",
        ]
        inst_cols = ["stock_id", "date", "foreign_net", "trust_net", "dealer_net", "total_net"]
        margin_cols = ["stock_id", "date", "margin_balance", "margin_change", "short_balance", "short_change"]
        hold_cols = ["stock_id", "date", "big_pct", "over1000_pct", "small_pct", "holders", "avg_lots"]

        prices = _load_groups(session, models.DailyPrice, price_cols, td)
        inds = _load_groups(session, models.Indicator, ind_cols, td)
        inst = _load_groups(session, models.Institutional, inst_cols, td)
        margin = _load_groups(session, models.Margin, margin_cols, td)
        holding = _load_groups(session, models.ShareholdingDistribution, hold_cols, td)
        stock_map = {s.id: s for s in session.execute(select(models.Stock)).scalars().all()}

        # 基本面（長線軌）：估值最新一筆；月營收/季財報載「歷史」並依公布時點切片
        valuation = _load_latest(
            session, models.Valuation, ["pe", "pb", "dividend_yield"],
            [models.Valuation.stock_id, models.Valuation.date],
        )
        revenue = _load_revenue_history(session, td)
        financials = _load_financial_history(session, td)
        fund_rank, pe_median = _fund_relatives(revenue, valuation, stock_map)
        # 類股方向（P3）→ Track 算 sector_adjust
        sector_daily = {
            sd.sector_id: sd
            for sd in session.execute(
                select(models.SectorDaily).where(models.SectorDaily.date == td)
            ).scalars().all()
        }
        # 處置警示（近15日）→ 共用硬篩排除（不推薦處置股）。一次撈，規則不各自查 DB。
        from datetime import timedelta

        disposed: dict[str, list] = {}
        for ev in session.execute(
            select(models.Event).where(
                models.Event.category == "處置警示", models.Event.date >= td - timedelta(days=15)
            )
        ).scalars().all():
            disposed.setdefault(ev.stock_id, []).append(ev)

        # 展望/利空事件（近 60 日）→ 長線軌 OutlookScore
        events60: dict[str, list] = {}
        for ev in session.execute(
            select(models.Event).where(
                models.Event.category.in_(["展望", "利空"]),
                models.Event.date >= td - timedelta(days=60),
                models.Event.date <= td,
                models.Event.stock_id.is_not(None),
            )
        ).scalars().all():
            events60.setdefault(ev.stock_id, []).append(ev)

        rows: list[dict] = []
        scored = 0
        for sid, ind_g in inds.items():
            if ind_g["date"].iloc[-1] != td:  # 當日無指標 = 當日未交易，跳過
                continue
            stock = stock_map.get(sid)
            price_g = prices.get(sid)
            if stock is None or price_g is None or price_g["date"].iloc[-1] != td:
                continue
            ctx = StockContext(
                stock=stock,
                date=td,
                prices=price_g,
                inds=ind_g,
                inst=inst.get(sid, pd.DataFrame(columns=inst_cols)),
                margin=margin.get(sid),
                holding=holding.get(sid),
                valuation=valuation.get(sid),
                revenue=revenue.get(sid),
                financials=financials.get(sid),
                sector=sector_daily.get(stock.sector_id),
                events=disposed.get(sid),
                fund_rel={
                    "yoy3m_rank": fund_rank.get(sid),
                    "pe_sector_median": pe_median.get(stock.sector_id),
                },
                events_60d=events60.get(sid),
            )
            for track in self.tracks:
                rows.append(track.evaluate(ctx, config.get(track.track_key, {})))
            scored += 1

        # 會噴：先套遲滯（用昨日狀態去抖動硬篩），再當天全市場橫截面 rank 合成總分
        self._apply_wave_hysteresis(session, td, rows)
        top_pct = float((config.get("wave") or {}).get("top_pct", _DEFAULT_TOP_PCT))
        finalize_wave_pop(rows, top_pct)
        self._apply_crash_style(session, td, rows)

        self._apply_stability(session, td, rows)

        n = BaseRepository(models.Score).upsert_many(session, rows)
        session.flush()
        passed = {t.track_key: sum(1 for r in rows if r["track"] == t.track_key and r["passed"]) for t in self.tracks}
        return {"status": "ok", "scored_stocks": scored, "rows": n, "passed": passed}

    def _apply_wave_hysteresis(self, session: Session, td: date, rows: list[dict]) -> None:
        """波段硬篩遲滯（去抖動，scripts/pop_hysteresis_backtest.py 定版）。

        改寫 wave 列的 passed_filter：
        - 昨日不在榜 → 進榜要嚴：原始硬篩全過 且 收盤 > 月線×(1+margin)（enter_ok）。
        - 昨日在榜   → 出榜要鬆：只有「大破線」（hard_break）或「原始硬篩連 2 天
          不滿足」（今日 strict=False 且 昨日 strict_filter=False）才踢，單日失守寬限。
        昨日＝td 之前最近的評分日；無史料（冷啟動/舊列 strict_filter=NULL）時，舊列
        以 passed_filter 代 strict（歷史上兩者同義）。重跑當日冪等（只讀 date<td）。
        """
        wave = [r for r in rows if r.get("track") == "wave"]
        if not wave:
            return
        prev_date = session.execute(
            select(func.max(models.Score.date)).where(
                models.Score.date < td, models.Score.track == "wave")
        ).scalar()
        prev: dict[str, tuple[bool, bool]] = {}
        if prev_date is not None:
            for sid, pf, strict in session.execute(
                select(
                    models.Score.stock_id, models.Score.passed_filter, models.Score.strict_filter,
                ).where(models.Score.date == prev_date, models.Score.track == "wave")
            ):
                prev[sid] = (bool(pf), bool(pf if strict is None else strict))
        for r in wave:
            h = r.pop("hyst_inputs", None) or {"enter_ok": False, "hard_break": True}
            strict_t = bool(r["passed_filter"])
            state_y, strict_y = prev.get(r["stock_id"], (False, False))
            if not state_y:
                r["passed_filter"] = h["enter_ok"]
            else:
                soft_break = not strict_t and not strict_y
                r["passed_filter"] = not (h["hard_break"] or soft_break)

    def _apply_crash_style(self, session: Session, td: date, rows: list[dict]) -> None:
        """深跌反攻風格的市場端閘（wave.CRASH_MKT_BIAS60）：

        大盤收盤距 MA60 ≤ −2.3% 時，crash_cand（個股端已過）轉正式 crash 風格；
        其餘日子拔掉 crash_cand（不落 DB、清單為空=誠實）。
        """
        from .rules.wave import CRASH_MKT_BIAS60
        closes = session.execute(
            select(models.MarketIndex.close)
            .where(models.MarketIndex.date <= td)
            .order_by(models.MarketIndex.date.desc())
            .limit(60)
        ).scalars().all()
        deep = False
        if len(closes) == 60:
            ma60 = sum(float(c) for c in closes) / 60.0
            deep = ma60 > 0 and (float(closes[0]) / ma60 - 1.0) * 100.0 <= CRASH_MKT_BIAS60
        for r in rows:
            if r.get("track") != "wave":
                continue
            styles = r.get("passed_styles") or []
            if "crash_cand" in styles:
                styles = [st for st in styles if st != "crash_cand"]
                if deep:
                    styles.append("crash")
                r["passed_styles"] = styles

    def _apply_stability(self, session: Session, td: date, rows: list[dict]) -> None:
        """L3：用近期歷史總分算穩定度，折進 confidence（confidence = 完整度×共識度×穩定度）。

        史料不足時穩定度=1.0，confidence 不變。重跑當日冪等（只看 date<td 的歷史）。
        會噴/長線軌皆用各自 total_score 的歷史穩定度。
        """
        recent_dates = session.execute(
            select(distinct(models.Score.date))
            .where(models.Score.date < td)
            .order_by(models.Score.date.desc())
            .limit(_STABILITY_LOOKBACK)
        ).scalars().all()
        prior: dict[tuple[str, str], list[float | None]] = {}
        if recent_dates:
            for sid, track, total in session.execute(
                select(
                    models.Score.stock_id, models.Score.track, models.Score.total_score,
                ).where(models.Score.date.in_(recent_dates))
            ):
                prior.setdefault((sid, track), []).append(total)
        for r in rows:
            key = (r["stock_id"], r["track"])
            st = _stability_factor(prior.get(key, []), r["total_score"])
            r["stability"] = round(st, 3)
            r["confidence"] = round((r.get("confidence") or 0.0) * st, 1)
