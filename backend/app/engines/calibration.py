"""CalibrationEngine（架構③ L4）：分數校準回測——分數高的真的比較會漲嗎？

只回測**波段（技術）軌**：技術指標有完整逐日歷史，可 point-in-time 乾淨回測。
長線軌的基本面只有「最新一筆」快照、無歷史，回測會用到未來資料（前視偏誤），
故刻意排除，等基本面存成歷史後再補。

做法：對每個有足夠回看(≥60 根)且有未來價(≥水平 h)的歷史日 T，逐檔 point-in-time
重算波段總分，量 T→T+h 的報酬，依分數分桶看「中位報酬 / 命中率(漲的比例)」是否
隨分數遞增。資料只有 ~5 個月、樣本薄，數字會雜——結果連同樣本數一起呈現，不誇大。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from ..storage import models
from .base import BaseEngine
from .context import StockContext
from .scoring import _STABILITY_LOOKBACK, _stability_factor
from .tracks import WaveTrack

_MIN_BARS = 60  # 與 ListedLongEnoughFilter 一致
_HORIZONS = [5, 10, 20]  # 未來交易日數
_BUCKETS = [(0, 50), (50, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
# 第二條：可信度分層（與前端燈號一致）。只在「可操作分數帶」內比較，問可信度能否再提升勝率
_ACTIONABLE_SCORE = 70.0
_CONF_TIERS = [("高", 75, 101), ("中", 50, 75), ("低", 0, 50)]

# 載入的欄位（與 ScoringEngine 對齊）；集中於此，calibration / expectancy 共用。
_PRICE_COLS = ["stock_id", "date", "open", "high", "low", "close", "volume"]
_IND_COLS = [
    "stock_id", "date", "ma5", "ma10", "ma20", "ma60", "vol_ma5", "vol_ma20",
    "kd_k", "kd_d", "macd", "macd_signal", "macd_hist", "atr14", "bias_20", "bias_60",
]
_INST_COLS = ["stock_id", "date", "foreign_net", "trust_net", "dealer_net", "total_net"]

# 回測窗（架構③ L4）：回補到 2020 後全表約 330 萬筆/六年。一次 select(model).all() 會把
# 三張表的 ORM（≈千萬列）同時載進記憶體 → OOM；逐日 evaluate 全歷史也會跑到天荒地老
# （實測 ~0.74ms/次，全市場每年的目標日約 8 分鐘）。對策兩條，缺一不可：
#   1) 只回測最近 _BACKTEST_DAYS 個交易日（多空都涵蓋、樣本足上萬）——回測本就需長歷史，
#      取 ~2 年是「夠長以涵蓋不同行情 / 短到能在數分鐘內跑完且不爆記憶體」的折衷；要更長
#      可調大此值（單調增加記憶體與耗時）。窗內結果與全載完全一致，只是目標日較少且較近。
#   2) 資料依股票分批、用欄位投影（Row tuple 不進 identity map）串流載入（_iter_stock_groups），
#      峰值記憶體 ~ 一批股票 × 窗長，而非全表。
_BACKTEST_DAYS = 480  # 目標/進場日數（交易日），約 2 年
_WARMUP_DAYS = 150  # 載入時於首個目標日往前多抓的日曆天，確保每檔 ≥_MIN_BARS 根回看
_STOCK_BATCH = 300  # 每批處理的股票數（與 IndicatorEngine 一致），限制峰值記憶體


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def _window(session: Session, reserve_tail: int) -> tuple[list[date], date | None]:
    """回測窗：取最近 _BACKTEST_DAYS 個交易日為目標日，並回傳載入用的日期下界 date_lo。

    reserve_tail = 末端要保留作「未來價／隔日進場」而不可當目標日的交易日數。
    date_lo 於首個目標日往前 _WARMUP_DAYS 日曆天，確保每檔在首個目標日已有 ≥_MIN_BARS 根回看。
    載入「不設上界」（抓到最新），讓視窗末端的目標日仍有未來價可用。
    """
    axis = session.execute(
        select(distinct(models.Indicator.date)).order_by(models.Indicator.date)
    ).scalars().all()
    end = len(axis) - reserve_tail
    if end <= _MIN_BARS:
        return [], None
    start = max(_MIN_BARS - 1, end - _BACKTEST_DAYS)
    target_dates = axis[start:end]
    return target_dates, target_dates[0] - timedelta(days=_WARMUP_DAYS)


def _group_ids(
    session: Session, model, cols: list[str], ids: list[str], date_lo: date
) -> dict[str, pd.DataFrame]:
    """載入指定股票、date≥date_lo 的某表，依 stock_id 分組（升冪）。

    用欄位投影（select 欄位 → Row tuple），不走 ORM entity，避免列物件累積進 Session
    identity map 而讓分批失去意義（與 IndicatorEngine 同手法）。
    """
    rows = session.execute(
        select(*[getattr(model, c) for c in cols])
        .where(model.stock_id.in_(ids), model.date >= date_lo)
        .order_by(model.stock_id, model.date)
    ).all()
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=cols)
    return {sid: g.reset_index(drop=True) for sid, g in df.groupby("stock_id", sort=False)}


def _iter_stock_groups(
    session: Session, stock_ids: list[str], date_lo: date, batch: int = _STOCK_BATCH
) -> Iterator[tuple[str, pd.DataFrame, pd.DataFrame | None, pd.DataFrame | None]]:
    """依股票分批串流載入 price/indicator/法人，逐檔 yield (sid, prices, inds, inst)。

    每批只把 batch 檔的窗內資料載進記憶體；calibration / expectancy 共用，避免一次把
    六年×全市場三張表全載而 OOM。無價量資料的股票直接略過（inds/inst 可能為 None）。
    """
    for i in range(0, len(stock_ids), batch):
        ids = stock_ids[i : i + batch]
        prices = _group_ids(session, models.DailyPrice, _PRICE_COLS, ids, date_lo)
        inds = _group_ids(session, models.Indicator, _IND_COLS, ids, date_lo)
        inst = _group_ids(session, models.Institutional, _INST_COLS, ids, date_lo)
        for sid in ids:
            pdf = prices.get(sid)
            if pdf is not None:
                yield sid, pdf, inds.get(sid), inst.get(sid)


class CalibrationEngine(BaseEngine):
    name = "calibration"

    def __init__(self) -> None:
        self.track = WaveTrack()

    def run(self, session: Session, trading_date: date) -> dict:
        """計算校準並存入 Setting['calibration']。trading_date 僅作 generated_at。"""
        result = self.compute(session, generated_at=trading_date)
        row = session.get(models.Setting, "calibration")
        if row is None:
            session.add(models.Setting(key="calibration", value=result))
        else:
            row.value = result
        session.flush()
        return {"status": "ok", "score_dates": result["window"]["score_dates"], "samples": result["samples"]}

    def compute(self, session: Session, generated_at: date) -> dict:
        # 目標日只取最近窗（多空涵蓋、樣本足）；資料依股票分批串流載入，避免全表 OOM。
        target_dates, date_lo = _window(session, reserve_tail=min(_HORIZONS))
        if not target_dates:
            return self._empty(generated_at, [])
        stock_map = {s.id: s for s in session.execute(select(models.Stock)).scalars().all()}
        stock_ids = sorted(stock_map)

        # samples[h] = list[(score, confidence, forward_return)]，僅收過硬篩（真實可交易宇宙）
        samples: dict[int, list[tuple[float, float, float]]] = {h: [] for h in _HORIZONS}
        used_dates: set[date] = set()
        for sid, pdf, ind_g, inst_g in _iter_stock_groups(session, stock_ids, date_lo):
            stock = stock_map.get(sid)
            if stock is None or ind_g is None:
                continue
            if inst_g is None:
                inst_g = pd.DataFrame(columns=_INST_COLS)
            pos_of = {d: i for i, d in enumerate(pdf["date"])}
            # NaN → None：pandas 把 NULL 收成 float64 NaN，而 NaN 不等於 None、且 NaN<=0 為
            # False，會穿過下方守門把未來報酬污染成 nan（停牌/下市股有整段零價）。與
            # ExpectancyEngine 同手法。
            closes = [None if pd.isna(x) else float(x) for x in pdf["close"]]
            recent_totals: list[float] = []  # 該檔近期總分（連續評分日）→ 算穩定度，與 production 對齊
            for T in target_dates:
                p = pos_of.get(T)
                if p is None or p < _MIN_BARS - 1:
                    continue
                ctx = StockContext(
                    stock=stock, date=T,
                    prices=pdf.iloc[: p + 1],
                    inds=ind_g[ind_g["date"] <= T],
                    inst=inst_g[inst_g["date"] <= T] if not inst_g.empty else inst_g,
                )
                res = self.track.evaluate(ctx, {})
                score = res["total_score"]
                # 可信度 = evaluate 的完整度×共識度 再折進穩定度（與 ScoringEngine 同公式）
                stab = _stability_factor(recent_totals[-_STABILITY_LOOKBACK:], score)
                confidence = round((res.get("confidence") or 0.0) * stab, 1)
                recent_totals.append(score)

                base_close = closes[p]
                if not res["passed_filter"] or base_close is None or base_close <= 0:
                    continue
                fwd = {h: closes[p + h] / base_close - 1 for h in _HORIZONS
                       if p + h < len(closes) and closes[p + h] is not None and closes[p + h] > 0}
                if not fwd:
                    continue
                used_dates.add(T)
                for h, r in fwd.items():
                    samples[h].append((score, confidence, r))

        buckets = {str(h): self._bucketize(samples[h]) for h in _HORIZONS}
        baseline = {str(h): self._round(_median([r for _, _, r in samples[h]])) for h in _HORIZONS}
        by_confidence = {str(h): self._confidence_breakdown(samples[h]) for h in _HORIZONS}
        total_samples = sum(len(samples[h]) for h in _HORIZONS)
        return {
            "generated_at": generated_at.isoformat(),
            "track": "wave",
            "window": {
                "from": target_dates[0].isoformat() if target_dates else None,
                "to": target_dates[-1].isoformat() if target_dates else None,
                "score_dates": len(used_dates),
            },
            "horizons": _HORIZONS,
            "buckets": buckets,
            "baseline": baseline,
            "by_confidence": by_confidence,
            "actionable_score": _ACTIONABLE_SCORE,
            "samples": total_samples,
            "note": (
                "僅波段(技術)軌、point-in-time。報酬為未來 N 交易日漲跌幅中位數，"
                "baseline 為同宇宙整體中位數。固定水平中位報酬對「動能+停損」策略偏粗"
                "（真正的勝負在停損後的賺賠不對稱，此指標看不到），僅供方向參考、非投資建議。"
                "長線軌因基本面無歷史暫不校準。"
            ),
        }

    def _bucketize(self, samples: list[tuple[float, float, float]]) -> list[dict]:
        out = []
        for lo, hi in _BUCKETS:
            rets = [r for s, _c, r in samples if lo <= s < hi]
            n = len(rets)
            out.append({
                "lo": lo, "hi": min(hi, 100), "n": n,
                "hit_rate": round(sum(1 for r in rets if r > 0) / n, 3) if n else None,
                "median_ret": self._round(_median(rets)),
            })
        return out

    def _confidence_breakdown(self, samples: list[tuple[float, float, float]]) -> list[dict]:
        """第二條：在可操作分數帶(≥70)內，依可信度分層比勝率——可信度能否再提升命中率？"""
        pool = [(c, r) for s, c, r in samples if s >= _ACTIONABLE_SCORE]
        out = []
        for label, lo, hi in _CONF_TIERS:
            rets = [r for c, r in pool if lo <= c < hi]
            n = len(rets)
            out.append({
                "tier": label, "lo": lo, "hi": min(hi, 100), "n": n,
                "hit_rate": round(sum(1 for r in rets if r > 0) / n, 3) if n else None,
                "median_ret": self._round(_median(rets)),
            })
        return out

    @staticmethod
    def _round(x: float | None) -> float | None:
        return round(x * 100, 2) if x is not None else None  # 回傳百分比

    def _empty(self, generated_at: date, all_dates: list[date]) -> dict:
        return {
            "generated_at": generated_at.isoformat(), "track": "wave",
            "window": {"from": None, "to": None, "score_dates": 0},
            "horizons": _HORIZONS, "buckets": {}, "baseline": {}, "by_confidence": {},
            "actionable_score": _ACTIONABLE_SCORE, "samples": 0,
            "note": "歷史資料不足以回測（需 ≥60 根 + 未來價）。",
        }
