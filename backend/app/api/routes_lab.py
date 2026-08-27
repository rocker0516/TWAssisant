"""策略室端點（方向 1+2）：模擬倉重播 / 勝率分組統計 / 參數敏感度。

三者共用同一套「歷史推薦成員」選取邏輯（與回看月曆同口徑：pop=過硬篩或有標籤
＋PIT 機率門檻；其他風格=passed_styles 純門檻篩），以及同一個進場錨（隔一交易
日最高價，保守的追高最壞情境）。

模擬倉＝確定性重播：Score 表本身就是逐日落庫的 PIT 推薦快照，虛擬交易直接用
歷史 Score＋歷史股價按固定規則重跑即可，毋須落庫；參數（停損/持有天數/機率
門檻）隨調隨比。
"""

from __future__ import annotations

import json
import os
from bisect import bisect_right
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, cast, or_, select, text
from sqlalchemy.orm import Session

from ..storage import models
from .deps import get_session
from .routes import _POP_TARGET, _latest_score_date, _prob_lookup
from .schemas import (
    CalibrationBin,
    ComboSample,
    ComboSamplesResponse,
    CooccurrenceResponse,
    LookbackGroupStat,
    SignalDecayPoint,
    SignalDecayResponse,
    SignalDecaySeries,
    TagComboStat,
    LookbackStatsResponse,
    PaperEquityPoint,
    PaperPositionDTO,
    PaperSimResponse,
    PaperSimStats,
    SensitivityPoint,
    SensitivityResponse,
    WaveChallengeResponse,
)

router = APIRouter(tags=["lab"])

_STYLE_PATTERN = "^(pop|explosive|strong|story|crash|punish|notice)$"


# ─────────────────────────── 共用備料 ───────────────────────────


def _candidates(
    session: Session, since: date | None, style: str, today_d: date, prob_min: float
) -> list[tuple[date, str, float | None, float | None]]:
    """歷史推薦成員 (推薦日, 股號, 分數, PIT機率)。口徑與回看月曆一致。

    punish/notice＝注意/處置事件策略（判官驗證：處置後10日控波動+16pp、holdout命中71%）：
    訊號日=公告日，隔日高進場與其他策略同錨；「分數」欄放累計次數供 top_n 排序。
    """
    if style in ("punish", "notice"):
        cond = models.AttentionListing.date >= since if since is not None else True
        rows = session.execute(
            select(models.AttentionListing.date, models.AttentionListing.stock_id,
                   models.AttentionListing.times)
            .where(models.AttentionListing.kind == style,
                   models.AttentionListing.date < today_d, cond)
        ).all()
        return [(d, sid, float(t) if t is not None else 0.0, None) for d, sid, t in rows]

    cond_since = models.Score.date >= since if since is not None else True
    if style != "pop":
        rows = session.execute(
            select(models.Score.date, models.Score.stock_id, models.Score.total_score)
            .where(
                models.Score.track == "wave", models.Score.date < today_d, cond_since,
                cast(models.Score.passed_styles, String).like(f'%"{style}"%'),
            )
        ).all()
        return [(d, sid, sc, None) for d, sid, sc in rows]

    rows = session.execute(
        select(models.Score.date, models.Score.stock_id, models.Score.total_score,
               models.Indicator.atr14, models.DailyPrice.close, models.Score.passed_styles)
        .join(models.Indicator,
              (models.Indicator.stock_id == models.Score.stock_id)
              & (models.Indicator.date == models.Score.date), isouter=True)
        .join(models.DailyPrice,
              (models.DailyPrice.stock_id == models.Score.stock_id)
              & (models.DailyPrice.date == models.Score.date), isouter=True)
        .where(models.Score.track == "wave", models.Score.date < today_d, cond_since,
               or_(models.Score.passed_filter == True,  # noqa: E712
                   cast(models.Score.passed_styles, String).like('%"%')))
    ).all()
    mkt = session.execute(
        select(models.MarketIndex.date, models.MarketIndex.close)
        .order_by(models.MarketIndex.date)
    ).all()
    mkt_closes = [r[1] for r in mkt]
    bias_map: dict[date, float] = {}
    for i in range(59, len(mkt)):
        ma = sum(mkt_closes[i - 59:i + 1]) / 60
        bias_map[mkt[i][0]] = (mkt_closes[i] / ma - 1.0) * 100
    out: list[tuple[date, str, float | None, float | None]] = []
    for d_, sid, score_, atr14, close_, styles_ in rows:
        atrp = (atr14 / close_) if atr14 is not None and close_ else None
        # 帶風格：卡片與實驗室必須用同一個機率，否則同一檔在兩邊被當成不同東西
        hitp, *_ = _prob_lookup(score_, atrp, bias_map.get(d_), styles_ or [])
        if hitp is not None and hitp >= prob_min:
            out.append((d_, sid, score_, hitp))
    return out


def _load_prices(
    session: Session, sids: set[str], since: date | None
) -> dict[str, tuple[list[date], list[tuple[float | None, float | None, float | None]]]]:
    """一次撈齊涉及個股的 (dates, [(high, low, close, open)])，升冪。

    open 擺在最後而非依 OHLC 慣例排首位：既有呼叫端多半以位置索引取 high/low/close
    (b[0]/b[1]/b[2])，附加在尾端才不必連動改四個端點。唯一的 tuple 解包在
    paper_simulate，已改成 bars[j][:3]。
    """
    stmt = select(
        models.DailyPrice.stock_id, models.DailyPrice.date,
        models.DailyPrice.high, models.DailyPrice.low, models.DailyPrice.close,
        models.DailyPrice.open,
    ).where(models.DailyPrice.stock_id.in_(sids)).order_by(models.DailyPrice.date)
    if since is not None:
        stmt = stmt.where(models.DailyPrice.date >= since)
    px: dict[str, tuple[list[date], list[tuple]]] = {}
    for sid, d_, hi, lo, cl, op in session.execute(stmt).all():
        dates, bars = px.setdefault(sid, ([], []))
        dates.append(d_)
        bars.append((hi, lo, cl, op))
    return px


# ─────────────────────────── 模擬倉 ───────────────────────────


@router.get("/paper/simulate", response_model=PaperSimResponse)
def paper_simulate(
    since: date | None = Query(None, description="模擬起始日；不傳=全部 Score 歷史"),
    style: str = Query("pop", pattern=_STYLE_PATTERN),
    prob_min: float = Query(50.0, ge=0.0, le=95.0, description="PIT 達標機率門檻%（pop 風格）"),
    top_n: int = Query(3, ge=1, le=20, description="每日最多開倉檔數（依分數取前 N）"),
    hold_days: int = Query(20, ge=1, le=120, description="最長持有交易日數，逾期收盤出場"),
    stop_pct: float | None = Query(None, ge=1.0, le=30.0,
                                   description="停損%；**預設不設停損**（波段軌定版口徑）"),
    target_pct: float | None = Query(None, ge=1.0, le=100.0, description="停利%；預設=會噴目標 +10%"),
    session: Session = Depends(get_session),
) -> PaperSimResponse:
    """模擬倉：把歷史推薦當訊號重播虛擬交易，得出「如果每天照單操作」的真實績效。

    規則：訊號日隔一交易日以最高價進場（保守）→ 逐日檢查 觸停損（低點先看，保守）
    → 觸停利 → 逾期收盤出。每筆等權 1 單位；權益曲線＝已實現報酬按出場日累加。

    stop_pct 預設 None＝**不設停損**，與波段軌 2026-08-24 定版一致：這條軌選的是
    ATR>9% 的高波動標的，−8% 停損實測把命中率打掉 25pp，而期間浮虧 >10% 的部位仍有
    47% 最後照樣達標。想看停損版就明確傳 stop_pct（實驗室本來就是拿來比的）。
    """
    tgt = (target_pct / 100.0) if target_pct is not None else _POP_TARGET
    today_d = _latest_score_date(session)
    if today_d is None:
        return PaperSimResponse(
            since=since, today_date=None, style=style, prob_min=prob_min, top_n=top_n,
            hold_days=hold_days, stop_pct=stop_pct, target_pct=round(tgt * 100, 1),
            stats=PaperSimStats(trades=0, closed=0, open=0, wins=0, win_rate=None,
                                avg_return_pct=None, total_return_pct=None,
                                open_unrealized_pct=None, avg_days_held=None,
                                max_drawdown_pct=None),
            equity=[], positions=[],
        )

    cands = _candidates(session, since, style, today_d, prob_min)
    # 每日依分數取前 N（沒分數排最後）
    by_day: dict[date, list[tuple[date, str, float | None, float | None]]] = {}
    for c in cands:
        by_day.setdefault(c[0], []).append(c)
    picked: list[tuple[date, str, float | None, float | None]] = []
    for d_, lst in by_day.items():
        lst.sort(key=lambda x: -(x[2] or 0))
        picked.extend(lst[:top_n])

    sids = {c[1] for c in picked}
    px = _load_prices(session, sids, since)
    # 停損價優先用當日 Score 落庫的 stop_loss（PIT）；沒要停損就整段不查
    stop_map: dict[tuple[date, str], float] = {}
    if picked and stop_pct is not None:
        srows = session.execute(
            select(models.Score.date, models.Score.stock_id, models.Score.stop_loss)
            .where(models.Score.track == "wave",
                   models.Score.stock_id.in_(sids),
                   models.Score.stop_loss.isnot(None))
        ).all()
        stop_map = {(d_, sid): sl for d_, sid, sl in srows}
    names = dict(session.execute(
        select(models.Stock.id, models.Stock.name).where(models.Stock.id.in_(sids))
    ).all()) if sids else {}

    positions: list[PaperPositionDTO] = []
    for sig_d, sid, score_, prob in sorted(picked):
        if sid not in px:
            continue
        dates, bars = px[sid]
        i0 = bisect_right(dates, sig_d)
        if i0 >= len(dates) or bars[i0][0] is None or bars[i0][0] <= 0:
            continue  # 尚無隔日資料（最近的訊號）
        entry = float(bars[i0][0])
        if stop_pct is None:
            stop = None
        else:
            sl = stop_map.get((sig_d, sid))
            stop = (float(sl) if sl is not None and sl < entry
                    else round(entry * (1 - stop_pct / 100), 2))
        target = round(entry * (1 + tgt), 2)

        status, exit_d, exit_px, reason = "open", None, None, None
        held = 0
        for j in range(i0 + 1, len(dates)):
            hi, lo, cl = bars[j][:3]
            held = j - i0
            if stop is not None and lo is not None and lo <= stop:
                status, exit_d, exit_px, reason = "closed", dates[j], stop, "stop"
                break
            if hi is not None and hi >= target:
                status, exit_d, exit_px, reason = "closed", dates[j], target, "target"
                break
            if held >= hold_days and cl is not None:
                status, exit_d, exit_px, reason = "closed", dates[j], float(cl), "timeout"
                break
        if status == "open":
            last_cl = next((b[2] for b in reversed(bars) if b[2] is not None), None)
            ret = round((last_cl / entry - 1) * 100, 2) if last_cl else None
            held = len(dates) - 1 - i0
        else:
            ret = round((exit_px / entry - 1) * 100, 2)
        positions.append(PaperPositionDTO(
            stock_id=sid, name=names.get(sid, sid), signal_date=sig_d,
            entry_date=dates[i0], entry_price=round(entry, 2),
            stop_price=stop, target_price=target, status=status,
            exit_date=exit_d, exit_price=exit_px, exit_reason=reason,
            return_pct=ret, days_held=held, score=score_, prob_hit=prob,
        ))

    closed = [p for p in positions if p.status == "closed"]
    open_ = [p for p in positions if p.status == "open"]
    wins = sum(1 for p in closed if (p.return_pct or 0) > 0)
    equity: list[PaperEquityPoint] = []
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in sorted(closed, key=lambda x: x.exit_date):
        cum += p.return_pct or 0
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
        equity.append(PaperEquityPoint(date=p.exit_date, cum_return_pct=round(cum, 2)))
    rets = [p.return_pct for p in closed if p.return_pct is not None]
    open_unrl = [p.return_pct for p in open_ if p.return_pct is not None]
    stats = PaperSimStats(
        trades=len(positions), closed=len(closed), open=len(open_), wins=wins,
        win_rate=round(wins / len(closed), 3) if closed else None,
        avg_return_pct=round(sum(rets) / len(rets), 2) if rets else None,
        total_return_pct=round(cum, 2) if closed else None,
        open_unrealized_pct=round(sum(open_unrl), 2) if open_unrl else None,
        avg_days_held=round(sum(p.days_held for p in closed) / len(closed), 1) if closed else None,
        max_drawdown_pct=round(max_dd, 2) if closed else None,
    )
    # open 在前（進行中最重要），再依日期新→舊
    positions.sort(key=lambda p: (p.status == "open", p.exit_date or p.entry_date), reverse=True)
    return PaperSimResponse(
        since=since, today_date=today_d, style=style, prob_min=prob_min, top_n=top_n,
        hold_days=hold_days, stop_pct=stop_pct, target_pct=round(tgt * 100, 1),
        stats=stats, equity=equity, positions=positions,
    )


# ─────────────────────────── 勝率分組統計 ───────────────────────────


_LAB_WINDOW = 10  # 成效觀察窗（2026-08 定版：與推薦目標一致，10 日內碰到 +10%）


def _sample_metrics(
    px: dict[str, tuple[list[date], list[tuple]]],
    sig_d: date, sid: str, min_age: int,
) -> tuple[bool, float | None, float | None, float | None,
           bool | None, float | None] | None:
    """單樣本 (hit, return, mfe, mae, hit_open, return_open)，皆以 10 日窗計。

    主錨=隔日**最高價**（最壞情況追高，保守）；尾端兩欄=隔日**開盤價**錨（較貼近實務，
    盤後看到推薦、隔天開盤掛進）。兩個錨並存是刻意的：只給高錨會讓「平均報酬」看起來
    永遠是負的——那是追高懲罰、不是策略績效（見 poppability.py 的 hit_close 同款作法）。
    樣本齡不足回 None。
    """
    if sid not in px:
        return None
    dates, bars = px[sid]
    i0 = bisect_right(dates, sig_d)
    if i0 >= len(dates) or bars[i0][0] is None or bars[i0][0] <= 0:
        return None
    if len(dates) - 1 - i0 < min_age:
        return None  # 進場後還沒走滿 min_age 個交易日 → 不入統計（避免拉低命中）
    entry = float(bars[i0][0])
    op = bars[i0][3] if len(bars[i0]) > 3 else None
    entry_op = float(op) if op else None
    win = bars[i0 + 1: i0 + 1 + _LAB_WINDOW]
    highs = [b[0] for b in win if b[0] is not None]
    lows = [b[1] for b in win if b[1] is not None]
    last_cl = next((b[2] for b in reversed(win) if b[2] is not None), None)
    hit = bool(highs) and max(highs) >= entry * (1.0 + _POP_TARGET)
    ret = round((last_cl / entry - 1) * 100, 2) if last_cl else None  # 第10日（或窗內最後）收盤
    mfe = round((max(highs) / entry - 1) * 100, 2) if highs else None
    mae = round((min(lows) / entry - 1) * 100, 2) if lows else None
    hit_op = (bool(highs) and max(highs) >= entry_op * (1.0 + _POP_TARGET)) if entry_op else None
    ret_op = round((last_cl / entry_op - 1) * 100, 2) if (last_cl and entry_op) else None
    return hit, ret, mfe, mae, hit_op, ret_op


def _group_stat(key: str, samples: list[tuple[bool, float | None, float | None, float | None]]) -> LookbackGroupStat:
    n = len(samples)
    hits = sum(1 for s in samples if s[0])
    rets = [s[1] for s in samples if s[1] is not None]
    mfes = [s[2] for s in samples if s[2] is not None]
    maes = [s[3] for s in samples if s[3] is not None]
    return LookbackGroupStat(
        key=key, n=n, hit_count=hits,
        hit_rate=round(hits / n, 3) if n else None,
        avg_return_pct=round(sum(rets) / len(rets), 2) if rets else None,
        avg_mfe_pct=round(sum(mfes) / len(mfes), 2) if mfes else None,
        avg_mae_pct=round(sum(maes) / len(maes), 2) if maes else None,
    )


_SCORE_BINS = [(0, 85, "<85"), (85, 90, "85–90"), (90, 95, "90–95"), (95, 999, "≥95")]


@router.get("/recommendations/lookback/stats", response_model=LookbackStatsResponse)
def lookback_stats(
    since: date | None = Query(None, description="統計起始日；不傳=全部歷史"),
    min_age_days: int = Query(10, ge=0, le=60, description="樣本至少距今 N 個交易日"),
    session: Session = Depends(get_session),
) -> LookbackStatsResponse:
    """勝率分組統計：哪類推薦其實準/不準，直接回饋門檻與選股偏好。

    分組軸：①風格標籤（pop=過硬篩、explosive/strong/story/crash=標籤）②分數帶。
    同一樣本可同時落在多個風格組（標籤可複掛）。
    """
    today_d = _latest_score_date(session)
    if today_d is None:
        return LookbackStatsResponse(since=since, today_date=None,
                                     min_age_days=min_age_days, by_style=[], by_score_bin=[])
    cond_since = models.Score.date >= since if since is not None else True
    rows = session.execute(
        select(models.Score.date, models.Score.stock_id, models.Score.total_score,
               models.Score.passed_filter, models.Score.passed_styles)
        .where(models.Score.track == "wave", models.Score.date < today_d, cond_since,
               or_(models.Score.passed_filter == True,  # noqa: E712
                   cast(models.Score.passed_styles, String).like('%"%')))
    ).all()
    px = _load_prices(session, {r[1] for r in rows}, since)

    by_style: dict[str, list] = {}
    by_bin: dict[str, list] = {}
    for d_, sid, score_, passed, styles in rows:
        m = _sample_metrics(px, d_, sid, min_age_days)
        if m is None:
            continue
        if passed:
            by_style.setdefault("pop", []).append(m)
        for st in (styles or []):
            by_style.setdefault(st, []).append(m)
        if score_ is not None:
            for lo, hi, label in _SCORE_BINS:
                if lo <= score_ < hi:
                    by_bin.setdefault(label, []).append(m)
                    break

    # 注意/處置事件組（獨立事件池，非 Score 推薦；與其他組同錨同窗可直接比較）
    att_cond = models.AttentionListing.date >= since if since is not None else True
    att_rows = session.execute(
        select(models.AttentionListing.date, models.AttentionListing.stock_id,
               models.AttentionListing.kind)
        .where(models.AttentionListing.date < today_d, att_cond)
    ).all()
    att_px = _load_prices(session, {r[1] for r in att_rows}, since)
    for d_, sid, kind in att_rows:
        m = _sample_metrics(att_px, d_, sid, min_age_days)
        if m is not None:
            by_style.setdefault(kind, []).append(m)

    style_order = ["pop", "explosive", "strong", "story", "crash", "punish", "notice"]
    return LookbackStatsResponse(
        since=since, today_date=today_d, min_age_days=min_age_days,
        by_style=[_group_stat(k, by_style[k]) for k in style_order if k in by_style],
        by_score_bin=[_group_stat(label, by_bin[label])
                      for _, _, label in _SCORE_BINS if label in by_bin],
    )


# ─────────────────────────── 標籤共存機率 ───────────────────────────


_COOC_TAGS = ["pop", "explosive", "strong", "story", "crash", "punish", "notice"]


@router.get("/recommendations/lookback/cooccurrence", response_model=CooccurrenceResponse)
def lookback_cooccurrence(
    since: date | None = Query(None, description="統計起始日；不傳=全部歷史"),
    min_age_days: int = Query(10, ge=0, le=60, description="成效樣本至少距今 N 個交易日"),
    session: Session = Depends(get_session),
) -> CooccurrenceResponse:
    """標籤共存結構：1對1 條件機率矩陣 + 精確組合全枚舉（每組附命中率/MFE/MAE）。"""
    from datetime import timedelta as td_

    today_d = _latest_score_date(session)
    if today_d is None:
        return CooccurrenceResponse(since=since, today_date=None, tags=_COOC_TAGS)

    cond_since = models.Score.date >= since if since is not None else True
    rows = session.execute(
        select(models.Score.date, models.Score.stock_id,
               models.Score.passed_filter, models.Score.passed_styles)
        .where(models.Score.track == "wave", models.Score.date < today_d, cond_since)
    ).all()

    # 注意/處置窗（與推薦卡徽章同判定）：stock → [(kind, 起, 迄)]
    att_cond = (models.AttentionListing.date >= since - td_(days=21)) if since is not None else True
    windows: dict[str, list[tuple[str, date, date]]] = {}
    for r in session.execute(
        select(models.AttentionListing).where(att_cond)
    ).scalars().all():
        if r.kind == "punish":
            end = r.end_date if r.end_date and r.end_date > r.date + td_(days=14) else r.date + td_(days=14)
            windows.setdefault(r.stock_id, []).append(("punish", r.date, end))
        else:
            windows.setdefault(r.stock_id, []).append(("notice", r.date, r.date + td_(days=7)))

    idx = {t: i for i, t in enumerate(_COOC_TAGS)}
    n_tag = [0] * len(_COOC_TAGS)
    n_both = [[0] * len(_COOC_TAGS) for _ in _COOC_TAGS]
    combo_samples: dict[tuple[int, ...], list[tuple[date, str]]] = {}
    total_tagged = 0
    for d_, sid, passed, styles in rows:
        tags: list[int] = []
        if passed:
            tags.append(idx["pop"])
        for st in (styles or []):
            if st in idx:
                tags.append(idx[st])
        for kind, b, e in windows.get(sid, ()):
            if b <= d_ <= e:
                tags.append(idx[kind])
        if not tags:
            continue
        key = tuple(sorted(set(tags)))
        total_tagged += 1
        combo_samples.setdefault(key, []).append((d_, sid))
        for a in key:
            n_tag[a] += 1
            for b2 in key:
                n_both[a][b2] += 1

    matrix: list[list[float | None]] = []
    for a in range(len(_COOC_TAGS)):
        if n_tag[a] < 30:
            matrix.append([None] * len(_COOC_TAGS))
        else:
            matrix.append([round(n_both[a][b2] / n_tag[a] * 100, 1) for b2 in range(len(_COOC_TAGS))])

    # 精確組合全枚舉（n≥30）＋ 成效（與分組統計同口徑：隔日高錨、10日窗，見 _sample_metrics）
    big = {k: v for k, v in combo_samples.items() if len(v) >= 30}
    sids = {sid for v in big.values() for _, sid in v}
    px = _load_prices(session, sids, since)
    combos: list[TagComboStat] = []
    for key, samples in big.items():
        ms = [m for d_, sid in samples if (m := _sample_metrics(px, d_, sid, min_age_days)) is not None]
        hits = sum(1 for m in ms if m[0])
        rets = [m[1] for m in ms if m[1] is not None]
        mfes = [m[2] for m in ms if m[2] is not None]
        maes = [m[3] for m in ms if m[3] is not None]
        combos.append(TagComboStat(
            key="+".join(_COOC_TAGS[i] for i in key),
            n=len(samples),
            share_pct=round(len(samples) / total_tagged * 100, 1) if total_tagged else 0.0,
            hit_rate=round(hits / len(ms), 3) if ms else None,
            avg_ret_pct=round(sum(rets) / len(rets), 2) if rets else None,
            avg_mfe_pct=round(sum(mfes) / len(mfes), 2) if mfes else None,
            avg_mae_pct=round(sum(maes) / len(maes), 2) if maes else None,
        ))
    combos.sort(key=lambda c: -c.n)

    return CooccurrenceResponse(
        since=since, today_date=today_d, tags=_COOC_TAGS,
        counts={t: n_tag[idx[t]] for t in _COOC_TAGS}, matrix=matrix, combos=combos,
    )


@router.get("/recommendations/lookback/cooccurrence/samples", response_model=ComboSamplesResponse)
def cooccurrence_samples(
    combo: str = Query(..., description="精確組合鍵，如 punish+notice"),
    since: date | None = Query(None),
    min_age_days: int = Query(10, ge=0, le=60),
    session: Session = Depends(get_session),
) -> ComboSamplesResponse:
    """某精確組合的樣本明細（檔×日，新→舊）＋各樣本 10 日窗成效。"""
    from datetime import timedelta as td_

    target = set(combo.split("+"))
    if not target or not target.issubset(set(_COOC_TAGS)):
        return ComboSamplesResponse(combo=combo, since=since)

    today_d = _latest_score_date(session)
    if today_d is None:
        return ComboSamplesResponse(combo=combo, since=since)

    cond_since = models.Score.date >= since if since is not None else True
    rows = session.execute(
        select(models.Score.date, models.Score.stock_id,
               models.Score.passed_filter, models.Score.passed_styles)
        .where(models.Score.track == "wave", models.Score.date < today_d, cond_since)
    ).all()

    att_cond = (models.AttentionListing.date >= since - td_(days=21)) if since is not None else True
    windows: dict[str, list[tuple[str, date, date]]] = {}
    for r in session.execute(select(models.AttentionListing).where(att_cond)).scalars().all():
        if r.kind == "punish":
            end = r.end_date if r.end_date and r.end_date > r.date + td_(days=14) else r.date + td_(days=14)
            windows.setdefault(r.stock_id, []).append(("punish", r.date, end))
        else:
            windows.setdefault(r.stock_id, []).append(("notice", r.date, r.date + td_(days=7)))

    picked: list[tuple[date, str]] = []
    for d_, sid, passed, styles in rows:
        tags = set(styles or [])
        if passed:
            tags.add("pop")
        for kind, b, e in windows.get(sid, ()):
            if b <= d_ <= e:
                tags.add(kind)
        if tags == target:
            picked.append((d_, sid))

    sids = {sid for _, sid in picked}
    px = _load_prices(session, sids, since)
    names = dict(session.execute(
        select(models.Stock.id, models.Stock.name).where(models.Stock.id.in_(sids))
    ).all()) if sids else {}

    samples: list[ComboSample] = []
    for d_, sid in sorted(picked, reverse=True):
        m = _sample_metrics(px, d_, sid, min_age_days)
        samples.append(ComboSample(
            date=d_, stock_id=sid, name=names.get(sid, sid),
            hit=m[0] if m else None,
            ret_pct=m[1] if m else None,
            mfe_pct=m[2] if m else None,
            mae_pct=m[3] if m else None,
        ))
    return ComboSamplesResponse(combo=combo, since=since, samples=samples)


# ─────────────────────────── 訊號時變效力 ───────────────────────────

_decay_cache: dict = {}


@router.get("/recommendations/signal-decay", response_model=SignalDecayResponse)
def signal_decay(session: Session = Depends(get_session)) -> SignalDecayResponse:
    """各訊號逐月 10 日命中率 vs 全市場基率：訊號影響度不是常數，隨市況時變。

    樣本：forward_labels（mfe10 有值的已定案列）× 當日訊號旗標
    （Score.passed_filter/passed_styles ＋ 注意/處置公告窗）。行程內以最新標籤日快取。
    """
    from datetime import timedelta as td_

    latest = session.execute(text("SELECT MAX(date) FROM forward_labels WHERE mfe10 IS NOT NULL")).scalar()
    if latest is None:
        return SignalDecayResponse()
    if _decay_cache.get("key") == latest:
        return _decay_cache["value"]

    # 全市場基率逐月
    base_rows = session.execute(text("""
        SELECT substr(date,1,7) ym, COUNT(*) n, AVG(mfe10 >= 10.0)*100 hit
        FROM forward_labels WHERE mfe10 IS NOT NULL GROUP BY ym ORDER BY ym""")).all()
    base_map = {ym: hit for ym, _, hit in base_rows}
    base = [SignalDecayPoint(ym=ym, n=n, hit=round(hit, 1)) for ym, n, hit in base_rows]

    series: list[SignalDecaySeries] = []

    def _series(key: str, rows: list[tuple[str, int, float]]) -> None:
        pts = [SignalDecayPoint(ym=ym, n=n, hit=round(hit, 1),
                                lift=round(hit - base_map.get(ym, 0.0), 1))
               for ym, n, hit in rows if ym in base_map and n >= 20]
        if len(pts) < 2:  # Score 標籤史較短（2026-03 起）；注意/處置有 2020 起長史
            return
        tot_n = sum(p.n for p in pts)
        hit_all = sum((p.hit or 0) * p.n for p in pts) / tot_n
        lift_all = sum((p.lift or 0) * p.n for p in pts) / tot_n
        rec = pts[-3:]
        rn = sum(p.n for p in rec)
        series.append(SignalDecaySeries(
            key=key, points=pts,
            hit_all=round(hit_all, 1), lift_all=round(lift_all, 1),
            hit_recent=round(sum((p.hit or 0) * p.n for p in rec) / rn, 1),
            lift_recent=round(sum((p.lift or 0) * p.n for p in rec) / rn, 1),
        ))

    # Score 標籤系（pop=過硬篩；風格=passed_styles JSON 內含字串）
    for key, cond in (
        ("pop", "s.passed_filter = 1"),
        ("explosive", "s.passed_styles LIKE '%\"explosive\"%'"),
        ("strong", "s.passed_styles LIKE '%\"strong\"%'"),
        ("story", "s.passed_styles LIKE '%\"story\"%'"),
        ("crash", "s.passed_styles LIKE '%\"crash\"%'"),
    ):
        rows = session.execute(text(f"""
            SELECT substr(f.date,1,7) ym, COUNT(*) n, AVG(f.mfe10 >= 10.0)*100 hit
            FROM scores s
            JOIN forward_labels f ON f.stock_id = s.stock_id AND f.date = s.date
            WHERE s.track = 'wave' AND f.mfe10 IS NOT NULL AND {cond}
            GROUP BY ym ORDER BY ym""")).all()
        _series(key, rows)

    # 注意/處置（公告窗內的標籤列；以日曆窗近似交易窗，與徽章同口徑）
    ev = session.execute(select(models.AttentionListing)).scalars().all()
    win: dict[str, list[tuple[str, date, date]]] = {}
    for r in ev:
        if r.kind == "punish":
            end = r.end_date if r.end_date and r.end_date > r.date + td_(days=14) else r.date + td_(days=14)
            win.setdefault(r.stock_id, []).append(("punish", r.date, end))
        else:
            win.setdefault(r.stock_id, []).append(("notice", r.date, r.date + td_(days=7)))
    lab_rows = session.execute(text(
        "SELECT stock_id, date, mfe10 FROM forward_labels WHERE mfe10 IS NOT NULL")).all()
    agg: dict[tuple[str, str], list[int]] = {}
    for sid, d_, mfe in lab_rows:
        for kind, b, e in win.get(sid, ()):
            dd = date.fromisoformat(d_) if isinstance(d_, str) else d_
            if b <= dd <= e:
                k = (kind, str(d_)[:7])
                a = agg.setdefault(k, [0, 0])
                a[0] += 1
                a[1] += 1 if mfe >= 10.0 else 0
    for kind in ("punish", "notice"):
        rows = sorted((ym, n_hit[0], n_hit[1] / n_hit[0] * 100)
                      for (k, ym), n_hit in agg.items() if k == kind)
        _series(kind, rows)

    resp = SignalDecayResponse(today_date=date.fromisoformat(str(latest)[:10]), base=base, signals=series)
    _decay_cache["key"] = latest
    _decay_cache["value"] = resp
    return resp


# ─────────────────────────── 參數敏感度 ───────────────────────────


_MIN_DAYS_RELIABLE = 8   # 進場日 < 8 天 → 標為樣本不足（日數才是有效 n）
_MIN_N_RELIABLE = 100    # 個股樣本數下限（同時要滿足）
# 校準分箱邊界：低機率端粗、10~50 這個真正密集的區帶細（查表機率上限約 72%）
_CALIB_EDGES = [0, 10, 15, 20, 25, 30, 35, 40, 45, 50, 60, 101]


def _wilson(p: float, n: float, z: float = 1.96) -> tuple[float, float]:
    """Wilson 95% 區間。傳「觀察到的比例 p」與「有效樣本數 n」而非 (命中數, 筆數)：

    有效 n 經簇內相關折算後會遠小於命中數，若照原式拿 k/n 會算出 p>1。
    """
    if n <= 0:
        return 0.0, 1.0
    p = min(1.0, max(0.0, p))
    d = 1.0 + z * z / n
    c = (p + z * z / (2 * n)) / d
    hw = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return max(0.0, c - hw), min(1.0, c + hw)


def _eff_n(n: int, days: int) -> float:
    """有效樣本數：同日個股高度相關，n 灌水。用幾何平均在 days 與 n 之間取折衷。

    純用 days 過度保守（同日 300 檔確實比 1 檔多帶一點資訊），純用 n 則嚴重高估信心
    （現況 10043 筆其實只有 37 天）。sqrt(n×days) 是常見的簇內相關折衷近似。
    """
    if n <= 0 or days <= 0:
        return 0.0
    return min(float(n), (float(n) * float(days)) ** 0.5)


def _agg_point(prob_min: float, sel: list[tuple], all_days: int,
               base: float | None) -> SensitivityPoint:
    n = len(sel)
    days = len({d for d, _m in sel})
    hits = sum(1 for _d, m in sel if m[0])
    hr = (hits / n) if n else None
    lo, hi = _wilson(hr, _eff_n(n, days)) if n else (None, None)
    by_day: dict[date, list[bool]] = {}
    for d_, m in sel:
        by_day.setdefault(d_, []).append(bool(m[0]))
    day_hr = (sum(sum(v) / len(v) for v in by_day.values()) / len(by_day)) if by_day else None

    def _avg(i: int) -> float | None:
        vs = [m[i] for _d, m in sel if m[i] is not None]
        return round(sum(vs) / len(vs), 2) if vs else None

    return SensitivityPoint(
        prob_min=prob_min, n=n, days=days,
        day_cover=round(days / all_days, 3) if all_days else None,
        avg_daily_n=round(n / all_days, 1) if all_days else None,
        hit_count=hits,
        hit_rate=round(hr, 3) if hr is not None else None,
        hit_rate_lo=round(lo, 3) if lo is not None else None,
        hit_rate_hi=round(hi, 3) if hi is not None else None,
        day_hit_rate=round(day_hr, 3) if day_hr is not None else None,
        lift=round(hr / base, 2) if (hr is not None and base) else None,
        avg_return_pct=_avg(1), avg_mfe_pct=_avg(2), avg_mae_pct=_avg(3),
        avg_return_open_pct=_avg(5),
        reliable=bool(days >= _MIN_DAYS_RELIABLE and n >= _MIN_N_RELIABLE),
    )


def _threshold_grid(probs: list[float]) -> list[float]:
    """門檻格線：0 起跳，其餘取實際機率分布分位數（收斂到 5 的倍數）後去重。

    先前寫死 0/10/…/80：查表機率上限約 72%，累積 ≥70% 恆為 0，永遠留兩列死列；
    同時 30~40% 這個真正密集的區帶只有一格、解析度不足。改資料驅動後格線自動貼合
    當期分布，換了 prob_table 也不必回頭改常數。
    """
    if not probs:
        return [0.0]
    srt = sorted(probs)
    out: list[float] = [0.0]
    for q in (0.20, 0.40, 0.55, 0.70, 0.80, 0.88, 0.94, 0.98):
        v = float(round(srt[min(len(srt) - 1, int(q * len(srt)))] / 5.0) * 5)
        if v > out[-1]:
            out.append(v)
    # 分位數抓不到極稀薄的高機率尾巴（現況 ≥60% 只佔 0.2%），但那正是使用者最想按下去
    # 的一格 —— 補上一列讓它現形、由 reliable=False 說明它有多薄，比整列消失誠實。
    top = float(int(srt[-1] / 5) * 5)
    if top > out[-1]:
        out.append(top)
    return out


@router.get("/recommendations/lookback/sensitivity", response_model=SensitivityResponse)
def lookback_sensitivity(
    since: date | None = Query(None, description="統計起始日；不傳=全部歷史"),
    min_age_days: int = Query(10, ge=0, le=60),
    session: Session = Depends(get_session),
) -> SensitivityResponse:
    """機率門檻掃描 + 機率校準檢查。

    兩張表刻意並存、回答兩個不同的問題：
      points      「門檻拉到 X 划不划算」——累積門檻的成效與樣本代價（調參用）
      calibration 「機率這個數字本身準不準」——非累積分箱的預測 vs 實現
    累積門檻天生看不出校準（≥40% 那格混了 40% 與 60% 的樣本），故必須分開列。

    同一份樣本算所有門檻（機率單調遞減篩選），一次回整條曲線。
    """
    today_d = _latest_score_date(session)
    empty = SensitivityResponse(
        since=since, today_date=None, min_age_days=min_age_days, entry_days=0,
        base_hit_rate=None, points=[], calibration=[], note="尚無推薦歷史可統計。")
    if today_d is None:
        return empty
    cands = _candidates(session, since, "pop", today_d, 0.0)
    px = _load_prices(session, {c[1] for c in cands}, since)
    samples: list[tuple[float, date, tuple]] = []  # (prob, 推薦日, metrics)
    for d_, sid, _score, prob in cands:
        m = _sample_metrics(px, d_, sid, min_age_days)
        if m is not None and prob is not None:
            samples.append((prob, d_, m))
    if not samples:
        return empty.model_copy(update={"today_date": today_d})

    all_days = len({d for _p, d, _m in samples})
    base = sum(1 for _p, _d, m in samples if m[0]) / len(samples)

    points = [
        _agg_point(pmin, [(d, m) for p, d, m in samples if p >= pmin], all_days, base)
        for pmin in _threshold_grid([p for p, _d, _m in samples])
    ]

    # ── 校準表：非累積分箱，比對「查表說幾 %」與「實際幾 %」──
    # 用固定邊界而非等頻：查表機率只有數十個離散值，等頻會切出 18.0–18.2 這種
    # 退化區間，讀者無從對照。小箱再向後合併確保每箱有起碼的樣本。
    calibration: list[CalibrationBin] = []
    buckets: list[list[tuple]] = []
    for lo_e, hi_e in zip(_CALIB_EDGES, _CALIB_EDGES[1:]):
        chunk = [r for r in samples if lo_e <= r[0] < hi_e]
        if not chunk:
            continue
        if buckets and len(chunk) < _MIN_N_RELIABLE:
            buckets[-1].extend(chunk)  # 太薄 → 併進前一箱，避免整排 n=3 的假格
        else:
            buckets.append(chunk)
    for chunk in buckets:
        n = len(chunk)
        days = len({d for _p, d, _m in chunk})
        hr = sum(1 for _p, _d, m in chunk if m[0]) / n
        pred = sum(p for p, _d, _m in chunk) / n
        lo, hi = _wilson(hr, _eff_n(n, days))
        calibration.append(CalibrationBin(
            lo=round(min(p for p, _d, _m in chunk), 1),
            hi=round(max(p for p, _d, _m in chunk), 1),
            n=n, days=days,
            pred_avg=round(pred, 1), hit_rate=round(hr, 3),
            hit_rate_lo=round(lo, 3), hit_rate_hi=round(hi, 3),
            err_pp=round(hr * 100 - pred, 1),
            reliable=bool(days >= _MIN_DAYS_RELIABLE and n >= _MIN_N_RELIABLE),
        ))

    return SensitivityResponse(
        since=since, today_date=today_d, min_age_days=min_age_days,
        entry_days=all_days, base_hit_rate=round(base, 3),
        points=points, calibration=calibration,
        note=(
            f"統計涵蓋 {all_days} 個進場日、{len(samples)} 筆個股樣本。**日數才是有效樣本數**"
            "（同日個股高度相關），故信賴區間以 sqrt(n×日數) 為有效 n，且日數<8 的列標為樣本不足。"
            "報酬同時給兩個進場錨：**隔日最高**(保守、最壞追高)與**隔日開盤**(貼近實務)——只看高錨會"
            "讓平均報酬恆為負，那是追高懲罰不是策略績效。校準表看『查表機率 vs 實際碰到率』，"
            "誤差為負＝機率高估。非投資建議。"
        ),
    )


# ─────────────────────────── 波段命中挑戰（凍結研究產物直讀）───────────────────────────

_WAVE_CHALLENGE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "wave_challenge.json")
_wave_challenge_cache: dict = {}


@router.get("/recommendations/wave-challenge", response_model=WaveChallengeResponse)
def wave_challenge() -> WaveChallengeResponse:
    """「波段軌命中率能不能更高／能不能有更多 ≥70% 的案例」的完整挑戰紀錄。

    直讀 data/wave_challenge.json（scripts/wave_hit_challenge.py --json 凍結產出）：
    整份要跑 4~6 分鐘且吃研究快取，不可能即時算。檔案不存在時回 available=False，
    前端顯示「尚未產生」而不是假資料。以檔案 mtime 當快取鍵，重跑腳本後自動失效。
    """
    if not os.path.exists(_WAVE_CHALLENGE_PATH):
        return WaveChallengeResponse(
            available=False,
            note="尚未產生：在 backend/ 執行 "
                 "`PYTHONUTF8=1 .venv/Scripts/python scripts/wave_hit_challenge.py --json`。")
    key = os.path.getmtime(_WAVE_CHALLENGE_PATH)
    if _wave_challenge_cache.get("key") == key:
        return _wave_challenge_cache["value"]
    with open(_WAVE_CHALLENGE_PATH, encoding="utf-8") as fh:
        raw = json.load(fh)
    resp = WaveChallengeResponse(
        available=True, **{k: v for k, v in raw.items() if k not in ("target",)},
        note=(
            "口徑：訊號隔日最高價進場、之後 10 個交易日內曾摸 +10%（與推薦頁徽章同口徑）；"
            "挖掘窗 2021-01~2024-12 定義、holdout 2025-01~2026-07 只驗一次。"
            "**崩勢型規則的有效樣本數是「崩段數」不是「日數」**——同一段內每天選到的是同一批股票，"
            "所以每條規則都同時給段級中位與最差段。歷史統計，非投資建議。"
        ),
    )
    _wave_challenge_cache.update(key=key, value=resp)
    return resp
