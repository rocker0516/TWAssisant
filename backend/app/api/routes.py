"""P1 讀取端點：推薦頁 + 詳情頁 + K線。

讀取直查算好的結果（架構⑥：讀繞過 Service 直接 repo）。寫入端點 P2 再加。
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from ..engines.market_regime import wave_market_regime
from ..storage import models
from .deps import get_session, get_session_write
from ..llm.assistant import _etf_kind, _scale_label
from ..llm.news_digest import stock_digest
from .schemas import (
    Candle,
    ChipSummary,
    ChipHistoryResponse,
    ChipPoint,
    EtfInfo,
    EventDTO,
    FundamentalSummary,
    HoldingHistoryResponse,
    HoldingPoint,
    LevelDTO,
    LevelsResponse,
    LookbackCalendar,
    LookbackDatePoint,
    LookbackReview,
    LookbackSummary,
    OhlcvResponse,
    RecommendationItem,
    RecommendationList,
    RecommendationLookbackResponse,
    ScoreDTO,
    StockDetail,
    StockSearchItem,
)

router = APIRouter()

_NEAR_BAND = 5.0  # 接近門檻區間寬度


def _latest_score_date(session: Session) -> date | None:
    return session.execute(select(func.max(models.Score.date))).scalar()


def _threshold(session: Session, track: str) -> float:
    row = session.get(models.Setting, "scoring")
    cfg = row.value if row and isinstance(row.value, dict) else {}
    return float(cfg.get(track, {}).get("threshold", 70.0))


_DEFAULT_TOP_PCT = 20.0  # 會噴推薦預設前 N%（門檻分數 = 100 − N）


def _wave_top_pct(session: Session) -> float:
    """會噴推薦的前 N%（設定頁可調；推薦頁橫桿可即時覆寫）。"""
    row = session.get(models.Setting, "scoring")
    cfg = row.value if row and isinstance(row.value, dict) else {}
    try:
        return float((cfg.get("wave") or {}).get("top_pct", _DEFAULT_TOP_PCT))
    except (TypeError, ValueError):
        return _DEFAULT_TOP_PCT


_SPARK_DAYS = 120  # 推薦卡片走勢取樣交易日數（約 6 個月；前端可切 1/3/6 月就地切片）


def _recent_prices(
    session: Session, stock_id: str, d: date, n: int = _SPARK_DAYS
) -> tuple[float | None, float | None, list[float] | None]:
    """回 (close, change_pct, spark)。spark 為近 n 個交易日收盤、由舊到新。"""
    rows = session.execute(
        select(models.DailyPrice.close)
        .where(models.DailyPrice.stock_id == stock_id, models.DailyPrice.date <= d)
        .order_by(models.DailyPrice.date.desc())
        .limit(n)
    ).scalars().all()
    if not rows:
        return None, None, None
    close = rows[0]
    change_pct = None
    if len(rows) >= 2 and rows[1] not in (None, 0) and close is not None:
        change_pct = round((close - rows[1]) / rows[1] * 100, 2)
    spark = [c for c in reversed(rows) if c is not None]
    return close, change_pct, (spark if len(spark) >= 2 else None)


def _price_change(
    session: Session, stock_id: str, d: date
) -> tuple[float | None, float | None, float | None]:
    """回 (close, change, change_pct)：當日收盤、對前一交易日的漲跌額與漲跌幅。"""
    rows = session.execute(
        select(models.DailyPrice.close)
        .where(models.DailyPrice.stock_id == stock_id, models.DailyPrice.date <= d)
        .order_by(models.DailyPrice.date.desc())
        .limit(2)
    ).scalars().all()
    if not rows:
        return None, None, None
    close = rows[0]
    change = change_pct = None
    if len(rows) >= 2 and rows[1] not in (None, 0) and close is not None:
        change = round(close - rows[1], 2)
        change_pct = round((close - rows[1]) / rows[1] * 100, 2)
    return close, change, change_pct


def _to_item(
    session: Session, sc: models.Score, name: str, sector_name: str | None, d: date,
) -> RecommendationItem:
    close, change_pct, spark = _recent_prices(session, sc.stock_id, d)
    return RecommendationItem(
        stock_id=sc.stock_id,
        name=name,
        sector_name=sector_name,
        track=sc.track,
        total_score=sc.total_score,
        sub_scores=sc.sub_scores,
        coverage=sc.coverage,
        confidence=sc.confidence,
        stability=sc.stability,
        close=close,
        change_pct=change_pct,
        buy_low=sc.buy_low,
        buy_high=sc.buy_high,
        stop_loss=sc.stop_loss,
        loss_pct=sc.loss_pct,
        reasons=sc.reasons,
        details=sc.details,
        spark=spark,
        passed_styles=sc.passed_styles or [],
        passed_filter=bool(sc.passed_filter),
    )


# ── 條件機率（每檔「同條件歷史命中率」，scripts/build_prob_table.py 產出）──

_PROB_MIN_N = 150  # 格子樣本不足 → 逐層回退（去大盤 → 去波動 → 全域）


def _prob_table() -> dict | None:
    import json as _json
    from pathlib import Path as _Path
    global _PROB_CACHE
    try:
        return _PROB_CACHE  # type: ignore[name-defined]
    except NameError:
        pass
    fp = _Path(__file__).resolve().parents[2] / "data" / "prob_table.json"
    _PROB_CACHE = _json.loads(fp.read_text()) if fp.exists() else None
    return _PROB_CACHE


def _bin_label(v: float, edges: list[float], labels: list[str]) -> str | None:
    for i in range(len(labels)):
        if edges[i] <= v < edges[i + 1]:
            return labels[i]
    return None


def _prob_lookup(score: float | None, atr_pct: float | None,
                 mkt_bias60: float | None) -> tuple[float | None, int | None, str | None, float | None]:
    """回 (同條件歷史命中%, n, 條件描述, 平均最深回撤%)。樣本薄逐層回退。"""
    t = _prob_table()
    if t is None or score is None:
        return None, None, None, None
    b = t["bins"]
    s = _bin_label(score, b["score"], b["score_labels"])
    a = _bin_label(atr_pct * 100, b["atr"], b["atr_labels"]) if atr_pct is not None else None
    mk = _bin_label(mkt_bias60, b["mkt"], b["mkt_labels"]) if mkt_bias60 is not None else None
    if s and a and mk:
        c = t["full"].get(f"{s}|{a}|{mk}")
        if c and c["n"] >= _PROB_MIN_N:
            return c["hit"], c["n"], f"分數{s}×波動{a}%×大盤{mk}", c.get("mae")
    if s and a:
        c = t["sa"].get(f"{s}|{a}")
        if c and c["n"] >= _PROB_MIN_N:
            return c["hit"], c["n"], f"分數{s}×波動{a}%", c.get("mae")
    if s:
        c = t["s"].get(s)
        if c:
            return c["hit"], c["n"], f"分數{s}", c.get("mae")
    gl = t.get("global")
    return (gl["hit"], gl["n"], "全市場", gl.get("mae")) if gl else (None, None, None, None)


def _attach_probabilities(session: Session, items: list[RecommendationItem], d: date) -> None:
    """批次補上每檔「同條件歷史命中率」（波動用當日 atr14/close，大盤用乖離季線）。"""
    if not items or _prob_table() is None:
        return
    ids = [it.stock_id for it in items]
    atr_rows = session.execute(
        select(models.Indicator.stock_id, models.Indicator.atr14, models.DailyPrice.close)
        .join(models.DailyPrice,
              (models.DailyPrice.stock_id == models.Indicator.stock_id)
              & (models.DailyPrice.date == models.Indicator.date))
        .where(models.Indicator.date == d, models.Indicator.stock_id.in_(ids))
    ).all()
    atr_map = {sid: (atr / close if atr is not None and close else None)
               for sid, atr, close in atr_rows}
    closes = session.execute(
        select(models.MarketIndex.close).where(models.MarketIndex.date <= d)
        .order_by(models.MarketIndex.date.desc()).limit(60)
    ).scalars().all()
    mkt_bias = ((closes[0] / (sum(closes) / len(closes)) - 1.0) * 100
                if len(closes) >= 60 else None)
    for it in items:
        hit, n, cond, mae = _prob_lookup(it.total_score, atr_map.get(it.stock_id), mkt_bias)
        it.prob_hit = hit
        it.prob_n = n
        it.prob_cond = cond
        it.prob_mae = mae


@router.get("/recommendations/tag-stats")
def recommendation_tag_stats() -> dict:
    """標籤組合五年實證命中統計（scripts/build_tag_combo_stats.py 產出，靜態檔）。"""
    import json as _json
    from pathlib import Path as _Path
    fp = _Path(__file__).resolve().parents[2] / "data" / "tag_combo_stats.json"
    if not fp.exists():
        return {"stats": {}}
    return _json.loads(fp.read_text())


@router.get("/recommendations", response_model=RecommendationList)
def recommendations(
    track: str = Query("wave", pattern="^(wave|long)$"),
    style: str = Query("pop", pattern="^(pop|explosive|strong|story|crash)$",
                       description="波段風格：pop=會噴(硬篩+前N%)；explosive=爆發(極高波動+上揚月線，純門檻篩)"),
    session: Session = Depends(get_session),
) -> RecommendationList:
    """波段軌＝會噴：回傳全部過硬篩股(依會噴分數高→低)，前端橫桿就地切『前 N%』。
    style=explosive：爆發風格＝atr>7%+上揚月線(不看季線乖離)，純門檻篩全回、無前N%概念。
    長線軌：沿用門檻切 items / near。"""
    d = _latest_score_date(session)
    styled = track == "wave" and style != "pop"   # 純門檻風格（explosive/strong/story/crash）
    if track == "wave":
        top_pct = None if styled else _wave_top_pct(session)
        cutoff = 0.0 if styled else round(100.0 - _wave_top_pct(session), 2)
    else:
        top_pct = None
        cutoff = _threshold(session, track)
    regime = wave_market_regime(session) if track == "wave" else None
    if d is None:
        return RecommendationList(
            track=track, style=style if styled else "pop",
            date=None, threshold=cutoff, top_pct=top_pct, items=[], near=[],
            regime=regime,
        )

    base = (
        select(models.Score, models.Stock.name, models.Sector.name)
        .join(models.Stock, models.Score.stock_id == models.Stock.id)
        .join(models.Sector, models.Stock.sector_id == models.Sector.id, isouter=True)
        .where(models.Score.track == track, models.Score.date == d)
    )

    items, near = [], []
    for sc, name, sector_name in session.execute(base).all():
        if styled:
            if not sc.passed_styles or style not in sc.passed_styles:
                continue
            items.append(_to_item(session, sc, name, sector_name, d))
            continue
        if track == "wave":
            # 標籤化清單：過硬篩(會噴候選) 或 任一純門檻風格 都回（前端標籤+排序）
            if not sc.passed_filter and not sc.passed_styles:
                continue
            items.append(_to_item(session, sc, name, sector_name, d))
            continue
        if not sc.passed_filter or sc.total_score is None:
            continue
        if sc.total_score >= cutoff:
            items.append(_to_item(session, sc, name, sector_name, d))
        elif sc.total_score >= cutoff - _NEAR_BAND:
            near.append(_to_item(session, sc, name, sector_name, d))

    if track == "wave":
        _attach_probabilities(session, items + near, d)
    items.sort(key=lambda it: it.total_score or 0, reverse=True)
    near.sort(key=lambda it: it.total_score or 0, reverse=True)
    return RecommendationList(
        track=track, style=style if styled else "pop",
        date=d, threshold=cutoff, top_pct=top_pct, items=items, near=near,
        regime=regime,
    )


_POP_TARGET = 0.10  # 「會噴」門檻：摸到 +10%（與 PoppabilityEfficacyEngine 對齊）


def _lookback_review(
    session: Session, stock_id: str, lookback_d: date, today_d: date
) -> LookbackReview:
    """從推薦日到今日的實況：以「隔天最高價」為進場錨（實務：盤後看到推薦、隔日追高最壞情境）。

    MFE/MAE/hit 都以此錨算，days_to_pop 從隔天起算=1；days_elapsed 不含隔天當日。
    """
    rows = session.execute(
        select(
            models.DailyPrice.date,
            models.DailyPrice.high,
            models.DailyPrice.low,
            models.DailyPrice.close,
        )
        .where(
            models.DailyPrice.stock_id == stock_id,
            models.DailyPrice.date >= lookback_d,
            models.DailyPrice.date <= today_d,
        )
        .order_by(models.DailyPrice.date)
    ).all()
    # 沒有隔天資料 → 無法回測
    if len(rows) < 2 or rows[1][1] is None:
        return LookbackReview(
            entry_close=None, current_close=None, return_pct=None,
            mfe_pct=None, mae_pct=None,
        )
    entry = float(rows[1][1])  # 隔天最高（保守：追高進場的最壞情況）
    following = rows[2:]  # 隔天之後的實現（隔天當日 high=entry 不可能自破+10%）
    if not following:
        return LookbackReview(
            entry_close=round(entry, 2), current_close=round(rows[1][3], 2) if rows[1][3] is not None else None,
            return_pct=(round((rows[1][3] - entry) / entry * 100, 2)
                        if rows[1][3] is not None and entry > 0 else None),
            mfe_pct=None, mae_pct=None, days_elapsed=0,
        )
    current_close = next((r[3] for r in reversed(following) if r[3] is not None), None)
    highs = [r[1] for r in following if r[1] is not None]
    lows = [r[2] for r in following if r[2] is not None]
    pop_target_px = entry * (1.0 + _POP_TARGET)
    hit = False
    hit_date: date | None = None
    days_to_pop: int | None = None
    for i, r in enumerate(following, start=1):
        if r[1] is not None and r[1] >= pop_target_px:
            hit = True
            hit_date = r[0]
            days_to_pop = i
            break
    return LookbackReview(
        entry_close=round(entry, 2),
        current_close=round(current_close, 2) if current_close is not None else None,
        return_pct=(
            round((current_close - entry) / entry * 100, 2)
            if current_close is not None and entry > 0 else None
        ),
        mfe_pct=round((max(highs) - entry) / entry * 100, 2) if highs and entry > 0 else None,
        mae_pct=round((min(lows) - entry) / entry * 100, 2) if lows and entry > 0 else None,
        hit_pop=hit,
        hit_pop_date=hit_date,
        days_to_pop=days_to_pop,
        days_elapsed=len(following),
    )


def _build_lookback_response(
    session: Session, lookback_d: date, today_d: date,
    eff_top_pct: float, cutoff: float, days_back: int,
    style: str = "pop",
    prob_min: float = 0.0,
) -> RecommendationLookbackResponse:
    """給定推薦日與今日，組出該日回看清單（含每檔 review、整批摘要）。

    機率口徑（2026-08-09 改版）：成員＝當日過硬篩或有風格標籤（同今日清單標籤制），
    每檔附「當日 PIT 達標機率」（用那天的分數/波動/大盤狀態查表），prob_min 篩選、
    摘要對篩後集合計算——「當時說 X%、實際命中多少」直接可對照。
    style="explosive" 等：成員=當日 passed_styles 含該風格（純門檻篩）。
    """
    base = (
        select(models.Score, models.Stock.name, models.Sector.name)
        .join(models.Stock, models.Score.stock_id == models.Stock.id)
        .join(models.Sector, models.Stock.sector_id == models.Sector.id, isouter=True)
        .where(models.Score.track == "wave", models.Score.date == lookback_d)
    )
    items: list[RecommendationItem] = []
    for sc, name, sector_name in session.execute(base).all():
        if style != "pop":
            if not sc.passed_styles or style not in sc.passed_styles:
                continue
        elif not sc.passed_filter and not sc.passed_styles:
            continue
        items.append(_to_item(session, sc, name, sector_name, lookback_d))
    _attach_probabilities(session, items, lookback_d)  # PIT：用回看日的波動/大盤
    if prob_min > 0:
        items = [it for it in items if (it.prob_hit or 0) >= prob_min]
    hit_count = 0
    returns: list[float] = []
    mfes: list[float] = []
    maes: list[float] = []
    for it in items:
        review = _lookback_review(session, it.stock_id, lookback_d, today_d)
        it.review = review
        if review.hit_pop:
            hit_count += 1
        if review.return_pct is not None:
            returns.append(review.return_pct)
        if review.mfe_pct is not None:
            mfes.append(review.mfe_pct)
        if review.mae_pct is not None:
            maes.append(review.mae_pct)
    items.sort(key=lambda it: (it.prob_hit or 0, it.total_score or 0), reverse=True)
    n = len(items)
    return RecommendationLookbackResponse(
        track="wave",
        lookback_date=lookback_d,
        today_date=today_d,
        days_back=days_back,
        top_pct=eff_top_pct,
        cutoff=cutoff,
        items=items,
        summary=LookbackSummary(
            n=n,
            hit_count=hit_count,
            hit_rate=round(hit_count / n, 3) if n else None,
            avg_return_pct=round(sum(returns) / len(returns), 2) if returns else None,
            avg_mfe_pct=round(sum(mfes) / len(mfes), 2) if mfes else None,
            avg_mae_pct=round(sum(maes) / len(maes), 2) if maes else None,
        ),
    )


@router.get("/recommendations/lookback", response_model=RecommendationLookbackResponse)
def recommendations_lookback(
    date_: date | None = Query(None, alias="date", description="直接指定推薦日；不傳=用 days 算"),
    days: int = Query(3, ge=1, le=60, description="N 個交易日前（date 未指定時用）"),
    top_pct: float | None = Query(None, ge=1.0, le=50.0, description="舊參數（前 N%），機率口徑下僅回顯不篩選"),
    prob_min: float = Query(0.0, ge=0.0, le=95.0, description="達標機率門檻%（0=全部有標籤者）"),
    style: str = Query("pop", pattern="^(pop|explosive|strong|story|crash)$", description="波段風格（爆發=純門檻篩，不看 top_pct）"),
    session: Session = Depends(get_session),
) -> RecommendationLookbackResponse:
    """波段(會噴)軌「回看」：那天推薦清單到今天的實況（已噴 / 至今報酬 / 期間 MFE/MAE）。

    優先用 `date` 直接指定推薦日（月曆點選）；否則以 DailyPrice 交易日曆定位 `days` 個交易日前，
    Score 表可能有空隙，退到目標日 ≤ 的最近可用快照。
    """
    eff_top_pct = float(top_pct) if top_pct is not None else _wave_top_pct(session)
    cutoff = round(100.0 - eff_top_pct, 2)

    def _empty(lb_d: date | None, td_d: date | None) -> RecommendationLookbackResponse:
        return RecommendationLookbackResponse(
            track="wave", lookback_date=lb_d, today_date=td_d,
            days_back=days, top_pct=eff_top_pct, cutoff=cutoff,
            items=[],
            summary=LookbackSummary(
                n=0, hit_count=0, hit_rate=None,
                avg_return_pct=None, avg_mfe_pct=None, avg_mae_pct=None,
            ),
        )

    today_d = _latest_score_date(session)
    if today_d is None:
        return _empty(None, None)

    if date_ is not None:
        has_score = session.execute(
            select(func.count()).select_from(models.Score)
            .where(models.Score.track == "wave", models.Score.date == date_)
        ).scalar() or 0
        if not has_score or date_ >= today_d:
            return _empty(None, today_d)
        return _build_lookback_response(
            session, date_, today_d, eff_top_pct, cutoff, 0, style=style, prob_min=prob_min,
        )

    # 沒指定 date：以 days 為主，退到最近可用快照
    trading_dates = session.execute(
        select(models.DailyPrice.date)
        .where(models.DailyPrice.date <= today_d)
        .group_by(models.DailyPrice.date)
        .order_by(models.DailyPrice.date.desc())
        .limit(days + 1)
    ).scalars().all()
    if len(trading_dates) < days + 1:
        return _empty(None, today_d)
    target_day = trading_dates[days]
    lookback_d = session.execute(
        select(models.Score.date)
        .where(models.Score.track == "wave", models.Score.date <= target_day)
        .group_by(models.Score.date)
        .order_by(models.Score.date.desc())
        .limit(1)
    ).scalar()
    if lookback_d is None or lookback_d >= today_d:
        return _empty(None, today_d)
    return _build_lookback_response(
        session, lookback_d, today_d, eff_top_pct, cutoff, days, style=style, prob_min=prob_min,
    )


@router.get("/recommendations/lookback/calendar", response_model=LookbackCalendar)
def recommendations_lookback_calendar(
    since: date | None = Query(None, description="起始日；不傳=全部歷史"),
    top_pct: float | None = Query(None, ge=1.0, le=50.0, description="舊參數（前 N%），機率口徑下僅回顯"),
    prob_min: float = Query(0.0, ge=0.0, le=95.0, description="達標機率門檻%（0=全部有標籤者）"),
    style: str = Query("pop", pattern="^(pop|explosive|strong|story|crash)$", description="波段風格（爆發=純門檻篩）"),
    session: Session = Depends(get_session),
) -> LookbackCalendar:
    """回看月曆：每個過去的 Score 日一筆命中率（過硬篩且分數≥cutoff、期間 high ≥ entry×1.10）。

    進場錨＝隔天最高價（實務：盤後看到推薦、隔日追高的最壞情況）；MFE 只看隔天之後的 high。
    路徑無關（與 `_lookback_review` 一致）。
    """
    eff_top_pct = float(top_pct) if top_pct is not None else _wave_top_pct(session)
    cutoff = round(100.0 - eff_top_pct, 2)
    today_d = _latest_score_date(session)
    if today_d is None:
        return LookbackCalendar(today_date=None, top_pct=eff_top_pct, cutoff=cutoff, dates=[])

    q = (
        select(models.Score.date)
        .where(models.Score.track == "wave", models.Score.date < today_d)
        .group_by(models.Score.date)
        .order_by(models.Score.date)
    )
    if since is not None:
        q = q.where(models.Score.date >= since)
    score_dates = session.execute(q).scalars().all()

    # 機率口徑（pop）：成員=過硬篩或有標籤，PIT 機率 ≥ prob_min 才計入。
    # 批次備料：分數+ATR（indicators×daily_prices）一次撈全期、大盤乖離逐日算。
    prob_members: dict[date, list[str]] = {}
    if style == "pop":
        # 注意：passed_styles 空陣列 [] 非 NULL；成員=過硬篩 或 標籤陣列非空（LIKE '%"%' 表含字串元素）
        cond_since = models.Score.date >= since if since is not None else True
        rows = session.execute(
            select(models.Score.date, models.Score.stock_id, models.Score.total_score,
                   models.Indicator.atr14, models.DailyPrice.close)
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
        mkt_dates = [r[0] for r in mkt]
        mkt_closes = [r[1] for r in mkt]
        bias_map: dict[date, float] = {}
        for i in range(59, len(mkt)):
            ma = sum(mkt_closes[i - 59:i + 1]) / 60
            bias_map[mkt_dates[i]] = (mkt_closes[i] / ma - 1.0) * 100
        for d_, sid, score_, atr14, close_ in rows:
            atrp = (atr14 / close_) if atr14 is not None and close_ else None
            hitp, _, _, _ = _prob_lookup(score_, atrp, bias_map.get(d_))
            if hitp is not None and hitp >= prob_min:
                prob_members.setdefault(d_, []).append(sid)

    pop_ratio = 1.0 + _POP_TARGET
    points: list[LookbackDatePoint] = []
    styled = style != "pop"
    # 交易日軸一次算好：隔一交易日 = 軸上的下一天（避免每個日期全表 GROUP BY）
    axis = session.execute(
        select(models.DailyPrice.date).distinct()
        .where(models.DailyPrice.date <= today_d)
        .order_by(models.DailyPrice.date)
    ).scalars().all()
    next_day = {d0: d1 for d0, d1 in zip(axis, axis[1:])}
    for lb_d in score_dates:
        if styled:
            # SQLite JSON 存 TEXT，LIKE 足夠精準（值為風格名陣列，名稱互不為子字串）
            sids = session.execute(
                select(models.Score.stock_id).where(
                    models.Score.track == "wave",
                    models.Score.date == lb_d,
                    cast(models.Score.passed_styles, String).like(f'%"{style}"%'),
                )
            ).scalars().all()
        else:
            sids = prob_members.get(lb_d, [])
        n = len(sids)
        if n == 0:
            points.append(LookbackDatePoint(date=lb_d, n=0, hit_count=0, hit_rate=None))
            continue
        # 隔一交易日（推薦錨定的進場日）
        entry_day = next_day.get(lb_d)
        if entry_day is None:
            points.append(LookbackDatePoint(date=lb_d, n=n, hit_count=0, hit_rate=None))
            continue
        entry_rows = session.execute(
            select(models.DailyPrice.stock_id, models.DailyPrice.high)
            .where(models.DailyPrice.date == entry_day, models.DailyPrice.stock_id.in_(sids))
        ).all()
        entry_map = {sid: float(h) for sid, h in entry_rows if h is not None and h > 0}
        high_rows = session.execute(
            select(models.DailyPrice.stock_id, func.max(models.DailyPrice.high))
            .where(
                models.DailyPrice.date > entry_day,
                models.DailyPrice.date <= today_d,
                models.DailyPrice.stock_id.in_(sids),
            )
            .group_by(models.DailyPrice.stock_id)
        ).all()
        high_map = {sid: float(h) for sid, h in high_rows if h is not None}
        hit = sum(
            1 for sid, e in entry_map.items()
            if high_map.get(sid) is not None and high_map[sid] >= e * pop_ratio
        )
        points.append(LookbackDatePoint(
            date=lb_d, n=n, hit_count=hit,
            hit_rate=round(hit / n, 3) if n else None,
        ))
    return LookbackCalendar(
        today_date=today_d, top_pct=eff_top_pct, cutoff=cutoff, dates=points,
    )


def _score_dto(sc: models.Score | None) -> ScoreDTO | None:
    if sc is None:
        return None
    return ScoreDTO(
        track=sc.track,
        passed=sc.passed,
        total_score=sc.total_score,
        sub_scores=sc.sub_scores,
        coverage=sc.coverage,
        confidence=sc.confidence,
        stability=sc.stability,
        buy_low=sc.buy_low,
        buy_high=sc.buy_high,
        stop_loss=sc.stop_loss,
        loss_pct=sc.loss_pct,
        reasons=sc.reasons,
        details=sc.details,
    )


@router.get("/stocks/search", response_model=list[StockSearchItem])
def stock_search(
    q: str = Query(..., min_length=1, description="股號或股名關鍵字"),
    session: Session = Depends(get_session),
) -> list[StockSearchItem]:
    """股號/股名查詢（給查詢框跳轉用）。代號前綴或名稱包含皆比對，依相關度排序取前 10。"""
    term = q.strip()
    if not term:
        return []
    rows = session.execute(
        select(models.Stock).where(
            or_(models.Stock.id.like(f"{term}%"), models.Stock.name.like(f"%{term}%"))
        ).limit(50)
    ).scalars().all()

    def rank(s: models.Stock) -> tuple:
        low = term.lower()
        if s.id.lower() == low:
            return (0, s.id)
        if s.id.lower().startswith(low):
            return (1, s.id)
        if low in s.name.lower():
            return (2, s.id)
        return (3, s.id)

    ranked = sorted(rows, key=rank)[:10]
    return [
        StockSearchItem(stock_id=s.id, name=s.name, market=s.market, is_etf=s.is_etf)
        for s in ranked
    ]


@router.get("/poppable-efficacy")
def poppable_efficacy(session: Session = Depends(get_session)) -> dict:
    """會噴清單成效回測（波段軌 poppable 風格）。讀快取，重算用 POST /poppable-efficacy/recompute。"""
    row = session.get(models.Setting, "poppable_efficacy")
    if row and isinstance(row.value, dict):
        return row.value
    return {"track": "wave", "style": "poppable", "by_date": [], "detail": [], "total_list": 0,
            "window": {"entry_dates": 0}, "note": "尚未計算，請按重新計算。"}


@router.post("/poppable-efficacy/recompute")
def poppable_efficacy_recompute() -> dict:
    """背景重跑會噴清單成效回測（~分鐘級）。立即回傳狀態；前端輪詢 status 端點直到 running=False。"""
    from ..services.poppability_recompute import trigger

    return trigger()


@router.get("/poppable-efficacy/recompute/status")
def poppable_efficacy_recompute_status() -> dict:
    """背景重算狀態：{running, started_at, finished_at, error}。"""
    from ..services.poppability_recompute import status

    return status()


@router.get("/stocks/{stock_id}", response_model=StockDetail)
def stock_detail(stock_id: str, session: Session = Depends(get_session)) -> StockDetail:
    stock = session.get(models.Stock, stock_id)
    if stock is None:
        raise HTTPException(404, f"找不到股票 {stock_id}")
    sector = session.get(models.Sector, stock.sector_id) if stock.sector_id else None

    d = _latest_score_date(session) or session.execute(
        select(func.max(models.DailyPrice.date)).where(models.DailyPrice.stock_id == stock_id)
    ).scalar()
    close, change, change_pct = _price_change(session, stock_id, d) if d else (None, None, None)

    scores: dict[str, ScoreDTO | None] = {}
    for tk in ("wave", "long"):
        sc = session.get(models.Score, {"stock_id": stock_id, "date": d, "track": tk}) if d else None
        scores[tk] = _score_dto(sc)

    inst = session.execute(
        select(models.Institutional).where(models.Institutional.stock_id == stock_id)
        .order_by(models.Institutional.date.desc()).limit(1)
    ).scalars().first()
    mg = session.execute(
        select(models.Margin).where(models.Margin.stock_id == stock_id)
        .order_by(models.Margin.date.desc()).limit(1)
    ).scalars().first()
    # 集保股權分散：取近 5 週（升冪），算大戶占比近月趨勢（史料不足→None）
    hold_rows = list(reversed(session.execute(
        select(models.ShareholdingDistribution)
        .where(models.ShareholdingDistribution.stock_id == stock_id)
        .order_by(models.ShareholdingDistribution.date.desc()).limit(5)
    ).scalars().all()))
    hold = hold_rows[-1] if hold_rows else None
    big_trend = None
    if hold is not None and len(hold_rows) >= 2:
        bigs = [r.big_pct for r in hold_rows if r.big_pct is not None]
        if len(bigs) >= 2:
            big_trend = round(bigs[-1] - bigs[0], 2)
    # 借券賣出餘額：最新 + 近 20 個資料日增減
    sbl_rows = list(reversed(session.execute(
        select(models.ShortLending.sbl_balance)
        .where(models.ShortLending.stock_id == stock_id, models.ShortLending.sbl_balance.is_not(None))
        .order_by(models.ShortLending.date.desc()).limit(20)
    ).scalars().all()))
    sbl_balance = sbl_rows[-1] if sbl_rows else None
    sbl_chg20 = (sbl_rows[-1] - sbl_rows[0]) if len(sbl_rows) >= 2 else None
    # 近 5 日當沖占成交量比（上市限定；無資料 None）
    dt_rows = session.execute(
        select(models.DayTrading.date, models.DayTrading.dt_volume)
        .where(models.DayTrading.stock_id == stock_id)
        .order_by(models.DayTrading.date.desc()).limit(5)
    ).all()
    dt_ratio5 = None
    if dt_rows:
        d_lo = min(r[0] for r in dt_rows)
        vol_sum = session.execute(
            select(func.sum(models.DailyPrice.volume))
            .where(models.DailyPrice.stock_id == stock_id, models.DailyPrice.date >= d_lo)
        ).scalar()
        dt_sum = sum(r[1] or 0 for r in dt_rows)
        if vol_sum:
            dt_ratio5 = round(dt_sum / (vol_sum / 1000.0) * 100, 1)
    # 董監持股：最近兩個月比變化 + 設質比率
    ins_rows = session.execute(
        select(models.InsiderHolding)
        .where(models.InsiderHolding.stock_id == stock_id)
        .order_by(models.InsiderHolding.year.desc(), models.InsiderHolding.month.desc()).limit(2)
    ).scalars().all()
    insider_pct_chg = None
    insider_pledge_pct = ins_rows[0].pledge_pct if ins_rows else None
    if len(ins_rows) == 2 and ins_rows[1].director_shares and ins_rows[0].director_shares is not None:
        insider_pct_chg = round(
            (ins_rows[0].director_shares - ins_rows[1].director_shares)
            / ins_rows[1].director_shares * 100, 2,
        )
    chip = ChipSummary(
        date=inst.date if inst else (mg.date if mg else None),
        foreign_net=inst.foreign_net if inst else None,
        trust_net=inst.trust_net if inst else None,
        dealer_net=inst.dealer_net if inst else None,
        total_net=inst.total_net if inst else None,
        margin_balance=mg.margin_balance if mg else None,
        short_balance=mg.short_balance if mg else None,
        holding_date=hold.date if hold else None,
        big_pct=hold.big_pct if hold else None,
        over1000_pct=hold.over1000_pct if hold else None,
        small_pct=hold.small_pct if hold else None,
        holders=hold.holders if hold else None,
        big_trend=big_trend,
        sbl_balance=sbl_balance,
        sbl_chg20=sbl_chg20,
        dt_ratio5=dt_ratio5,
        insider_pct_chg=insider_pct_chg,
        insider_pledge_pct=insider_pledge_pct,
    )

    val = session.execute(
        select(models.Valuation).where(models.Valuation.stock_id == stock_id)
        .order_by(models.Valuation.date.desc()).limit(1)
    ).scalars().first()
    rev = session.execute(
        select(models.RevenueMonthly).where(models.RevenueMonthly.stock_id == stock_id)
        .order_by(models.RevenueMonthly.year.desc(), models.RevenueMonthly.month.desc()).limit(1)
    ).scalars().first()
    fin = session.execute(
        select(models.FinancialQuarter).where(models.FinancialQuarter.stock_id == stock_id)
        .order_by(models.FinancialQuarter.year.desc(), models.FinancialQuarter.quarter.desc()).limit(1)
    ).scalars().first()
    # EPS：優先用財報；無則以 收盤價/本益比 推導近4季 trailing EPS
    eps = fin.eps if fin and fin.eps is not None else None
    if eps is None and val and val.pe and val.pe > 0 and close:
        eps = round(close / val.pe, 2)
    fundamental = FundamentalSummary(
        pe=val.pe if val else None,
        pb=val.pb if val else None,
        dividend_yield=val.dividend_yield if val else None,
        eps=eps,
        revenue_yoy=rev.yoy if rev else None,
    )

    etf_info = None
    if stock.is_etf:
        prof = session.get(models.EtfProfile, stock_id)
        if prof:
            billion = round(prof.units * close / 1e8, 0) if (prof.units and close) else None
            etf_info = EtfInfo(
                kind=_etf_kind(prof.fund_type),
                fund_type=prof.fund_type,
                track_index=prof.track_index,
                has_foreign=prof.has_foreign,
                scale_label=_scale_label(billion),
                scale_billion=billion,
                listed_date=prof.etf_listed_date,
            )

    events = session.execute(
        select(models.Event).where(models.Event.stock_id == stock_id)
        .order_by(models.Event.date.desc(), models.Event.id.desc()).limit(10)
    ).scalars().all()

    market_td = session.execute(select(func.max(models.DailyPrice.date))).scalar()
    news_digest = stock_digest(session, stock_id, market_td) if market_td else None

    return StockDetail(
        stock_id=stock.id,
        name=stock.name,
        sector_name=sector.name if sector else None,
        market=stock.market,
        date=d,
        close=close,
        change=change,
        change_pct=change_pct,
        is_etf=stock.is_etf,
        scores=scores,
        chip=chip,
        fundamental=fundamental,
        etf=etf_info,
        events=[
            EventDTO(date=e.date, category=e.category, title=e.title, summary=e.summary,
                     is_risk=e.is_risk, source=e.source, url=e.url)
            for e in events
        ],
        news_digest=news_digest,
    )


@router.get("/stocks/{stock_id}/ohlcv", response_model=OhlcvResponse)
def stock_ohlcv(
    stock_id: str,
    days: int = Query(120, ge=20, le=3000),
    session: Session = Depends(get_session),
) -> OhlcvResponse:
    rows = session.execute(
        select(models.DailyPrice, models.Indicator)
        .join(
            models.Indicator,
            (models.Indicator.stock_id == models.DailyPrice.stock_id)
            & (models.Indicator.date == models.DailyPrice.date),
            isouter=True,
        )
        .where(models.DailyPrice.stock_id == stock_id)
        .order_by(models.DailyPrice.date.desc())
        .limit(days)
    ).all()
    candles = [
        Candle(
            date=p.date, open=p.open, high=p.high, low=p.low, close=p.close, volume=p.volume,
            ma5=i.ma5 if i else None, ma20=i.ma20 if i else None, ma60=i.ma60 if i else None,
            kd_k=i.kd_k if i else None, kd_d=i.kd_d if i else None,
            macd=i.macd if i else None, macd_signal=i.macd_signal if i else None,
            macd_hist=i.macd_hist if i else None,
        )
        for p, i in reversed(rows)
    ]
    return OhlcvResponse(stock_id=stock_id, candles=candles)


@router.get("/stocks/{stock_id}/levels", response_model=LevelsResponse)
def stock_levels(
    stock_id: str,
    session: Session = Depends(get_session),
) -> LevelsResponse:
    """客觀支撐/壓力位（均線群+波段前低+量價套牢區，純算不靠 LLM）。"""
    from ..engines.support import levels_for_stock

    levels = levels_for_stock(session, stock_id)
    close = session.execute(
        select(models.DailyPrice.close)
        .where(models.DailyPrice.stock_id == stock_id)
        .order_by(models.DailyPrice.date.desc())
        .limit(1)
    ).scalar()
    dto = [
        LevelDTO(
            price=l.price, kind=l.kind, strength=l.strength,
            methods=l.methods, distance_pct=l.distance_pct,
        )
        for l in levels
    ]
    return LevelsResponse(
        stock_id=stock_id,
        close=float(close) if close is not None else None,
        supports=[d for d in dto if d.kind == "support"],
        resistances=[d for d in dto if d.kind == "resistance"],
    )


# 週數低於此 → 觸發背景回補（TDCC 智慧網逐週爬近一年）。回補後常駐快取、不再重抓。
_HOLDING_MIN_WEEKS = 6


@router.get("/stocks/{stock_id}/holding-history", response_model=HoldingHistoryResponse)
def holding_history(
    stock_id: str,
    session: Session = Depends(get_session),
) -> HoldingHistoryResponse:
    """集保大戶/散戶占比週序列（曲線用）。史料不足時背景回補近一年（看哪檔補哪檔）。"""
    rows = session.execute(
        select(models.ShareholdingDistribution)
        .where(models.ShareholdingDistribution.stock_id == stock_id)
        .order_by(models.ShareholdingDistribution.date)
    ).scalars().all()

    backfilling = False
    if len(rows) < _HOLDING_MIN_WEEKS and session.get(models.Stock, stock_id) is not None:
        from ..services.shareholding_backfill import trigger_backfill

        backfilling = trigger_backfill(stock_id)

    return HoldingHistoryResponse(
        stock_id=stock_id,
        points=[
            HoldingPoint(
                date=r.date, big_pct=r.big_pct, over1000_pct=r.over1000_pct,
                small_pct=r.small_pct, holders=r.holders,
            )
            for r in rows
        ],
        backfilling=backfilling,
    )


@router.get("/stocks/{stock_id}/chip-history", response_model=ChipHistoryResponse)
def chip_history(
    stock_id: str,
    days: int = Query(120, ge=20, le=3000),
    session: Session = Depends(get_session),
) -> ChipHistoryResponse:
    """籌碼每日序列（三大法人買賣超 + 融資融券餘額），供每日買賣量 + 累計曲線。"""
    start = session.execute(
        select(models.DailyPrice.date)
        .where(models.DailyPrice.stock_id == stock_id)
        .order_by(models.DailyPrice.date.desc())
        .offset(days - 1)
        .limit(1)
    ).scalar()

    def _window(model):
        stmt = select(model).where(model.stock_id == stock_id)
        if start is not None:
            stmt = stmt.where(model.date >= start)
        return session.execute(stmt.order_by(model.date)).scalars().all()

    merged: dict = {}
    for r in _window(models.Institutional):
        merged.setdefault(r.date, {})["inst"] = r
    for r in _window(models.Margin):
        merged.setdefault(r.date, {})["mg"] = r

    points = []
    for d in sorted(merged):
        inst = merged[d].get("inst")
        mg = merged[d].get("mg")
        points.append(
            ChipPoint(
                date=d,
                foreign_net=inst.foreign_net if inst else None,
                trust_net=inst.trust_net if inst else None,
                dealer_net=inst.dealer_net if inst else None,
                total_net=inst.total_net if inst else None,
                margin_balance=mg.margin_balance if mg else None,
                short_balance=mg.short_balance if mg else None,
            )
        )
    return ChipHistoryResponse(stock_id=stock_id, points=points)
