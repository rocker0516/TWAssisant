"""ExpectancyEngine（L4+）：逐筆交易期望值回測（僅波段軌，point-in-time）。

問對的問題：照系統「進場(分數≥門檻) + 真實出場規則(停損/跌破月線/移動停利)」逐筆模擬，
每筆交易的期望值＝勝率×平均賺 − 敗率×平均賠 是否為正？分數/可信度越高，期望值越高嗎？

進場：歷史日 T 波段 passed → 隔日 T+1 開盤買。出場：逐日跑真實 ExitSignal，CRITICAL
（停損−cap%／跌破月線／移動停利）成立 → 隔日開盤賣。一檔同時只持一倉，最長持有 120 日。
含來回成本。對照組＝只過硬篩不看分數。誠實但書：存活者偏誤、日線近似、樣本內、僅波段。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..storage import models
from .base import BaseEngine
from .calibration import (
    _BUCKETS,
    _CONF_TIERS,
    _INST_COLS,
    _MIN_BARS,
    _iter_stock_groups,
    _median,
    _window,
)
from .context import StockContext
from .exit_signals import DEFAULTS, Position, Sev, StopLossSignal, TrailingStopSignal, set_config
from .scoring import _STABILITY_LOOKBACK, _stability_factor
from .tracks import WaveTrack

_COST = 0.00585  # 來回成本：手續費 0.1425%×2 + 賣出證交稅 0.3% ≈ 0.585%
_MAX_HOLD = 120  # 最長持有交易日，到期強制平倉
_ACTIONABLE = 70.0


@dataclass
class _Shim:
    """出場訊號需要的 holding 介面（track + 無逐檔覆寫）。"""

    track: str = "wave"
    stop_loss_override = None
    trail_trigger_override = None
    trail_pullback_override = None


class _CtxShim:
    """出場訊號只讀 ctx.ind（StopLoss 用 ma20）。輕量、免建整個 StockContext。"""

    def __init__(self, ind: pd.Series | None) -> None:
        self.ind = ind


@dataclass
class StockData:
    """單檔的 point-in-time 預算結果：進場最貴的 evaluate 只算一次，給多組出場參數重用。"""

    stock: object
    dates: list
    opens: list
    highs: list
    lows: list
    closes: list
    ind_idx: pd.DataFrame  # set_index('date')，出場查 ma20 用
    positions: list  # 有 cache 的 price 位置（升冪）
    cache: dict  # pos -> (passed, passed_filter, score, conf)


def _entry_passed(passed: bool, pf: bool) -> bool:
    return passed  # 主策略：分數達門檻


def _entry_filter(passed: bool, pf: bool) -> bool:
    return pf  # 對照組：只過硬篩、不看分數


def _pctile(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def _stats(trades: list[dict]) -> dict:
    n = len(trades)
    if not n:
        return {"n": 0, "win_rate": None, "avg_win": None, "avg_loss": None,
                "expectancy": None, "payoff": None, "avg_hold": None, "forced_pct": None,
                "avg_mae": None, "p10_ret": None, "worst": None}
    rets = [t["ret"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    maes = [t["mae"] for t in trades if t.get("mae") is not None]
    pct = lambda x: round(x * 100, 2) if x is not None else None  # noqa: E731
    return {
        "n": n,
        "win_rate": round(len(wins) / n, 3),
        "avg_win": pct(avg_win),
        "avg_loss": pct(avg_loss),
        "expectancy": pct(sum(rets) / n),  # 每筆期望值 = mean(ret)
        "payoff": round(avg_win / abs(avg_loss), 2) if avg_loss < 0 else None,
        "avg_hold": round(sum(t["days"] for t in trades) / n, 1),
        "forced_pct": round(sum(1 for t in trades if t["forced"]) / n, 3),
        "avg_mae": pct(sum(maes) / len(maes)) if maes else None,  # 平均最大不利偏移（持有期間最深水下）
        "p10_ret": pct(_pctile(rets, 0.10)),  # 第 10 百分位報酬（左尾痛點）
        "worst": pct(min(rets)),
    }


class ExpectancyEngine(BaseEngine):
    name = "expectancy"

    def __init__(self) -> None:
        self.track = WaveTrack()
        self.stop_sig = StopLossSignal()
        self.trail_sig = TrailingStopSignal()

    def run(self, session: Session, trading_date: date) -> dict:
        result = self.compute(session, generated_at=trading_date)
        row = session.get(models.Setting, "expectancy")
        if row is None:
            session.add(models.Setting(key="expectancy", value=result))
        else:
            row.value = result
        session.flush()
        return {"status": "ok", "trades": result["overall"]["n"],
                "expectancy": result["overall"]["expectancy"]}

    def compute(self, session: Session, generated_at: date) -> dict:
        # 出場參數依設定頁；評分門檻/配分依 scoring 設定（與 production 推薦一致）
        ex_cfg = session.get(models.Setting, "exit")
        set_config(ex_cfg.value if ex_cfg and isinstance(ex_cfg.value, dict) else {})
        sc_cfg = session.get(models.Setting, "scoring")
        wave_cfg = (sc_cfg.value or {}).get("wave", {}) if sc_cfg and isinstance(sc_cfg.value, dict) else {}

        sds, target_dates = self._precompute(session, wave_cfg)
        if not sds:
            return self._empty(generated_at)

        main: list[dict] = []
        main_stoponly: list[dict] = []
        control: list[dict] = []
        for sd in sds:
            main += self._simulate(sd, _entry_passed, faithful=True)
            main_stoponly += self._simulate(sd, _entry_passed, faithful=False)
            control += self._simulate(sd, _entry_filter, faithful=True)
        used_dates = {t["entry_date"] for t in main}

        by_score = []
        for lo, hi in _BUCKETS:
            by_score.append({"lo": lo, "hi": min(hi, 100), **_stats([t for t in main if lo <= t["score"] < hi])})
        by_conf = []
        for label, lo, hi in _CONF_TIERS:
            sub = [t for t in main if t["score"] >= _ACTIONABLE and lo <= t["conf"] < hi]
            by_conf.append({"tier": label, "lo": lo, "hi": min(hi, 100), **_stats(sub)})

        return {
            "generated_at": generated_at.isoformat(),
            "track": "wave",
            "window": {"from": target_dates[0].isoformat(), "to": target_dates[-1].isoformat(),
                       "entry_dates": len(used_dates)},
            "cost_pct": round(_COST * 100, 3),
            "max_hold": _MAX_HOLD,
            "actionable_score": _ACTIONABLE,
            "overall": _stats(main),
            "overall_stop_only": _stats(main_stoponly),
            "control": _stats(control),
            "by_score": by_score,
            "by_confidence": by_conf,
            "note": (
                "僅波段軌、point-in-time。進場=分數達門檻、隔日開盤買；出場=真實 ExitSignal "
                "的 CRITICAL（停損/跌破月線/移動停利），隔日開盤賣。含來回成本 "
                f"{round(_COST * 100, 2)}%。對照組=只過硬篩不看分數。"
                "存活者偏誤(已下市股缺席→偏樂觀)、日線近似(跳空風險低估)、樣本內(窗約 2 年、"
                "見 window)非 walk-forward、長線軌不測。為歷史統計，非投資建議。"
            ),
        }

    def _precompute(self, session: Session, wave_cfg: dict) -> tuple[list[StockData], list]:
        """最貴的一步（每檔每日 point-in-time evaluate）只算一次，供多組出場參數重用。

        目標日只取最近窗、資料依股票分批串流載入（見 calibration._window/_iter_stock_groups），
        避免回補到 2020 後一次把六年×全市場三張表全載而 OOM。
        """
        target_dates, date_lo = _window(session, reserve_tail=1)  # 末日無隔日開盤可進場
        if not target_dates:
            return [], []
        stock_map = {s.id: s for s in session.execute(select(models.Stock)).scalars().all()}
        stock_ids = sorted(stock_map)

        sds: list[StockData] = []
        for sid, pdf, ind_g, inst_g in _iter_stock_groups(session, stock_ids, date_lo):
            stock = stock_map.get(sid)
            if stock is None or ind_g is None:
                continue
            if inst_g is None:
                inst_g = pd.DataFrame(columns=_INST_COLS)
            dates = list(pdf["date"])
            # NaN → None，否則 NaN 不等於 None、會穿過守門污染報酬為 nan
            opens = [None if pd.isna(x) else float(x) for x in pdf["open"]]
            highs = [None if pd.isna(x) else float(x) for x in pdf["high"]]
            lows = [None if pd.isna(x) else float(x) for x in pdf["low"]]
            closes = [None if pd.isna(x) else float(x) for x in pdf["close"]]
            pos_of = {d: i for i, d in enumerate(dates)}
            ind_idx = ind_g.set_index("date")

            recent: list[float] = []
            cache: dict[int, tuple[bool, bool, float, float]] = {}
            for T in target_dates:
                p = pos_of.get(T)
                if p is None or p < _MIN_BARS - 1:
                    continue
                ctx = StockContext(
                    stock=stock, date=T, prices=pdf.iloc[: p + 1],
                    inds=ind_g[ind_g["date"] <= T],
                    inst=inst_g[inst_g["date"] <= T] if not inst_g.empty else inst_g,
                )
                res = self.track.evaluate(ctx, wave_cfg)
                score = res["total_score"]
                stab = _stability_factor(recent[-_STABILITY_LOOKBACK:], score)
                conf = round((res.get("confidence") or 0.0) * stab, 1)
                recent.append(score)
                cache[p] = (res["passed"], res["passed_filter"], score, conf)
            if cache:
                sds.append(StockData(stock, dates, opens, highs, lows, closes, ind_idx, sorted(cache), cache))
        return sds, target_dates

    def _simulate(self, sd: StockData, entry_pred, faithful: bool) -> list[dict]:
        """單檔逐筆模擬。出場參數讀全域 _ACTIVE（呼叫前用 set_config 設定）。"""
        shim = _Shim()
        opens, highs, lows, closes, dates = sd.opens, sd.highs, sd.lows, sd.closes, sd.dates
        ind_index = sd.ind_idx.index
        out: list[dict] = []
        hold_until = -1
        for p in sd.positions:
            if p <= hold_until:
                continue
            passed, pf, score, conf = sd.cache[p]
            if not entry_pred(passed, pf):
                continue
            e = p + 1  # 隔日開盤進場
            if e >= len(opens):
                break
            entry = opens[e]
            if entry is None or entry <= 0:
                continue
            highest = highs[e] if highs[e] is not None else entry
            lowest = lows[e] if lows[e] is not None else entry  # 持有期間最低 → MAE
            exit_price = exit_pos = None
            forced = False
            last = min(len(closes) - 1, e + _MAX_HOLD)
            for q in range(e, last + 1):
                if highs[q] is not None:
                    highest = max(highest, highs[q])
                if lows[q] is not None:
                    lowest = min(lowest, lows[q])
                close = closes[q]
                if close is None:
                    continue
                pos = Position(shares=1, avg_cost=entry, highest=highest, close=close)
                dq = dates[q]
                ctxq = _CtxShim(sd.ind_idx.loc[dq] if dq in ind_index else None)
                hits = self.stop_sig.check(shim, pos, ctxq) + self.trail_sig.check(shim, pos, ctxq)
                crit = [h for h in hits if h.sev == Sev.CRITICAL and (faithful or h.code != "break_ma")]
                if crit:
                    if q + 1 < len(opens) and opens[q + 1]:
                        exit_price, exit_pos = opens[q + 1], q + 1  # 隔日開盤出
                    else:
                        exit_price, exit_pos = close, q
                    break
            if exit_price is None:  # 到期/資料末 → 強制平倉
                exit_pos = last
                exit_price = closes[last] if closes[last] else entry
                forced = True
            ret = exit_price / entry - 1 - _COST
            mae = lowest / entry - 1  # 最大不利偏移（持有期間最深水下，負值）
            out.append({"score": score, "conf": conf, "ret": ret, "days": exit_pos - e,
                        "forced": forced, "entry_date": dates[p], "mae": mae})
            hold_until = exit_pos
        return out

    def _empty(self, generated_at: date) -> dict:
        z = _stats([])
        return {
            "generated_at": generated_at.isoformat(), "track": "wave",
            "window": {"from": None, "to": None, "entry_dates": 0},
            "cost_pct": round(_COST * 100, 3), "max_hold": _MAX_HOLD, "actionable_score": _ACTIONABLE,
            "overall": z, "overall_stop_only": z, "control": z, "by_score": [], "by_confidence": [],
            "note": "歷史資料不足以回測。",
        }
