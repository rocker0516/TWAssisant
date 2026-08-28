"""P1 讀取端點：推薦頁 + 詳情頁 + K線。

讀取直查算好的結果（架構⑥：讀繞過 Service 直接 repo）。寫入端點 P2 再加。
"""

from __future__ import annotations

from datetime import date, timedelta

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
    ChainNode,
    ChainStream,
    ChainStructure,
    ChainTagDTO,
    ChipSummary,
    ChipHistoryResponse,
    ChipPoint,
    CompanyProfileDTO,
    IndustryChainResponse,
    SectorBriefDTO,
    DividendEntry,
    DividendsResponse,
    EtfInfo,
    EventDTO,
    AttentionEntry,
    AttentionResponse,
    FinancialStatementsResponse,
    FinStatementQuarter,
    PeRiverPoint,
    PeRiverResponse,
    TechSummaryResponse,
    FundamentalHistoryResponse,
    FundamentalSummary,
    QuarterPoint,
    RevenuePoint,
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
    RecommendationMark,
    RecommendationMarksResponse,
    TargetPriceEntry,
    TargetPriceResponse,
    ScoreDTO,
    StockDetail,
    StockSearchItem,
)

router = APIRouter()

_NEAR_BAND = 5.0  # 接近門檻區間寬度


def _latest_score_date(session: Session) -> date | None:
    return session.execute(select(func.max(models.Score.date))).scalar()


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


_prob_cache: dict = {}


def _prob_table() -> dict | None:
    """條件機率查表（mtime 快取，與 _ml_consensus_picks 同款）。

    原本是永久快取：重跑 build_prob_table.py 後不重啟後端就讀不到新表，
    整站機率會停在舊口徑而且完全無聲。
    """
    import json as _json
    from pathlib import Path as _Path

    fp = _Path(__file__).resolve().parents[2] / "data" / "prob_table.json"
    if not fp.exists():
        return None
    mtime = fp.stat().st_mtime
    if _prob_cache.get("mtime") != mtime:
        try:
            _prob_cache["data"] = _json.loads(fp.read_text(encoding="utf-8"))
            _prob_cache["mtime"] = mtime
        except (ValueError, OSError):
            return _prob_cache.get("data")  # 讀壞了就沿用上一版，不要整站沒機率
    return _prob_cache.get("data")


def _bin_label(v: float, edges: list[float], labels: list[str]) -> str | None:
    for i in range(len(labels)):
        if edges[i] <= v < edges[i + 1]:
            return labels[i]
    return None


_STYLE_LABEL = {"explosive": "爆發", "strong": "強勢延伸", "story": "故事股", "crash": "深跌反攻"}
_STYLE_MIN_N = 100  # 風格格子的樣本下限（比 _PROB_MIN_N 鬆：風格本身已是很強的條件）


def _prob_lookup(score: float | None, atr_pct: float | None, mkt_bias60: float | None,
                 styles: list[str] | None = None,
                 ) -> tuple[float | None, int | None, str | None, float | None, str | None]:
    """回 (同條件歷史命中%, n, 條件描述, 平均最深回撤%)。樣本薄逐層回退。

    命中率取 Wilson 95% **下界**(hit_lb)：裸命中率在高機率端 walk-forward 實測系統性
    高估 +5.9pp（薄格子最嚴重），改下界後收斂到 −1.5pp 而鑑別度不變。舊版 prob_table.json
    沒有 hit_lb 欄，退回裸值以免整站沒機率。

    styles（2026-08-24）：這檔當日通過的純門檻風格。有標籤時**優先查該風格自己的
    「風格×波動×大盤」格子**——全市場同格看不到風格多出來的條件（以 crash 為例，
    查表格子 58.5% vs 規則自己的歷史 72.7%，差 14pp 全是條件差異）。
    實測 70,288 筆清單列回算：加權|誤差| 4.3pp→2.9pp，四個風格全改善。
    多標籤取**最高**：多通過一道篩子不應該讓估計變低（交集的真值無從得知，取單篩上界）。
    """
    t = _prob_table()
    if t is None or score is None:
        return None, None, None, None, None
    b = t["bins"]
    s = _bin_label(score, b["score"], b["score_labels"])
    a = _bin_label(atr_pct * 100, b["atr"], b["atr_labels"]) if atr_pct is not None else None
    mk = _bin_label(mkt_bias60, b["mkt"], b["mkt_labels"]) if mkt_bias60 is not None else None

    def _p(c: dict) -> float:
        v = c.get("hit_lb")
        return c["hit"] if v is None else v

    best: tuple[float, int, str, float | None, str] | None = None
    for st in (styles or ()):
        c = t.get("style", {}).get(f"{st}|{a}|{mk}") if a and mk else None
        cond = f"{_STYLE_LABEL.get(st, st)}×波動{a}%×大盤{mk}"
        if not c or c["n"] < _STYLE_MIN_N:
            c = t.get("style_all", {}).get(st)
            cond = f"{_STYLE_LABEL.get(st, st)}（全期）"
        if c and c["n"] >= _STYLE_MIN_N and (best is None or _p(c) > best[0]):
            best = (_p(c), c["n"], cond, c.get("mae"), st)
    if best is not None:
        return best

    if s and a and mk:
        c = t["full"].get(f"{s}|{a}|{mk}")
        if c and c["n"] >= _PROB_MIN_N:
            return _p(c), c["n"], f"分數{s}×波動{a}%×大盤{mk}", c.get("mae"), None
    # 回退1：**先丟分數、保留大盤**（2026-08-24）。分數是池內排序，實測增量上限 +3pp；
    # ATR×大盤才是機制軸。舊版第一步丟大盤，高波動薄格子會被「正常盤」稀釋——實測 crash
    # 卡片因此低估 ~7pp（顯示 55.8% vs 換順序後 62.6%），而整體校準完全沒退步。
    if a and mk and t.get("am"):
        c = t["am"].get(f"{a}|{mk}")
        if c and c["n"] >= _PROB_MIN_N:
            return _p(c), c["n"], f"波動{a}%×大盤{mk}", c.get("mae"), None
    if s and a:
        c = t["sa"].get(f"{s}|{a}")
        if c and c["n"] >= _PROB_MIN_N:
            return _p(c), c["n"], f"分數{s}×波動{a}%", c.get("mae"), None
    if s:
        c = t["s"].get(s)
        if c:
            return _p(c), c["n"], f"分數{s}", c.get("mae"), None
    gl = t.get("global")
    return (_p(gl), gl["n"], "全市場", gl.get("mae"), None) if gl else (None, None, None, None, None)


_ml_consensus_cache: dict = {}


def _ml_consensus_picks(d: date) -> set[str] | None:
    """ML 共識圈選集（scripts/build_ml_consensus.py infer 產出；mtime 快取；日期不符回 None）。"""
    import json as _json
    from pathlib import Path as _Path

    fp = _Path(__file__).resolve().parents[2] / "data" / "ml_consensus.json"
    if not fp.exists():
        return None
    mtime = fp.stat().st_mtime
    if _ml_consensus_cache.get("mtime") != mtime:
        try:
            _ml_consensus_cache["data"] = _json.loads(fp.read_text(encoding="utf-8"))
            _ml_consensus_cache["mtime"] = mtime
        except (ValueError, OSError):
            return None
    data = _ml_consensus_cache.get("data") or {}
    if data.get("date") != d.isoformat():
        return None
    return set(data.get("picks") or [])


def _attach_probabilities(session: Session, items: list[RecommendationItem], d: date) -> None:
    """批次補上每檔「同條件歷史命中率」（波動用當日 atr14/close，大盤用乖離季線）。"""
    if not items or _prob_table() is None:
        return
    ids = [it.stock_id for it in items]
    atr_rows = session.execute(
        select(models.Indicator.stock_id, models.Indicator.atr14, models.DailyPrice.close,
               models.Indicator.vol_ma5, models.Indicator.vol_ma20)
        .join(models.DailyPrice,
              (models.DailyPrice.stock_id == models.Indicator.stock_id)
              & (models.DailyPrice.date == models.Indicator.date))
        .where(models.Indicator.date == d, models.Indicator.stock_id.in_(ids))
    ).all()
    atr_map = {sid: (atr / close if atr is not None and close else None)
               for sid, atr, close, _v5, _v20 in atr_rows}
    volr_map = {sid: (round(v5 / v20, 2) if v5 and v20 else None)
                for sid, _atr, _close, v5, v20 in atr_rows}
    closes = session.execute(
        select(models.MarketIndex.close).where(models.MarketIndex.date <= d)
        .order_by(models.MarketIndex.date.desc()).limit(60)
    ).scalars().all()
    mkt_bias = ((closes[0] / (sum(closes) / len(closes)) - 1.0) * 100
                if len(closes) >= 60 else None)
    # 注意/處置動能旗標（判官雙段驗證：處置後10日 控波動+16pp；近21日窗涵蓋兩種判定）
    att_map: dict[str, set[str]] = {}
    for r in session.execute(
        select(models.AttentionListing)
        .where(models.AttentionListing.stock_id.in_(ids),
               models.AttentionListing.date >= d - timedelta(days=21))
    ).scalars().all():
        if r.kind == "punish" and (
            (r.begin_date and r.end_date and r.begin_date <= d <= r.end_date)
            or (d - r.date).days <= 14  # 公告後 ~10 交易日
        ):
            att_map.setdefault(r.stock_id, set()).add("punish")
        elif r.kind == "notice" and (d - r.date).days <= 7:  # ~5 交易日
            att_map.setdefault(r.stock_id, set()).add("notice")

    for it in items:
        # 帶入當日通過的純門檻風格：查表優先用該風格自己的歷史（見 _prob_lookup docstring）
        hit, n, cond, mae, pstyle = _prob_lookup(
            it.total_score, atr_map.get(it.stock_id), mkt_bias, it.passed_styles)
        it.prob_hit = hit
        it.prob_n = n
        it.prob_cond = cond
        it.prob_mae = mae
        it.prob_style = pstyle
        it.vol_ratio = volr_map.get(it.stock_id)
        flags = att_map.get(it.stock_id, set())
        it.attention = "punish" if "punish" in flags else ("notice" if "notice" in flags else None)
        it.attention_tags = sorted(flags)

    ml_picks = _ml_consensus_picks(d)
    if ml_picks is not None:
        for it in items:
            it.ml_consensus = it.stock_id in ml_picks


@router.get("/recommendations/tag-stats")
def recommendation_tag_stats() -> dict:
    """標籤組合五年實證命中統計（scripts/build_tag_combo_stats.py 產出，靜態檔）。"""
    import json as _json
    from pathlib import Path as _Path
    fp = _Path(__file__).resolve().parents[2] / "data" / "tag_combo_stats.json"
    if not fp.exists():
        return {"stats": {}}
    return _json.loads(fp.read_text(encoding="utf-8"))


@router.get("/recommendations", response_model=RecommendationList)
def recommendations(
    track: str = Query("wave", pattern="^wave$"),  # 長線軌已移除（2026-08-28），參數保留相容
    style: str = Query("pop", pattern="^(pop|explosive|strong|story|crash)$",
                       description="波段風格：pop=會噴(硬篩+前N%)；explosive=爆發(極高波動+上揚月線，純門檻篩)"),
    session: Session = Depends(get_session),
) -> RecommendationList:
    """波段軌＝會噴：回傳全部過硬篩股(依會噴分數高→低)，前端橫桿就地切『前 N%』。
    style=explosive：爆發風格＝atr>7%+上揚月線(不看季線乖離)，純門檻篩全回、無前N%概念。"""
    d = _latest_score_date(session)
    styled = style != "pop"   # 純門檻風格（explosive/strong/story/crash）
    top_pct = None if styled else _wave_top_pct(session)
    cutoff = 0.0 if styled else round(100.0 - _wave_top_pct(session), 2)
    regime = wave_market_regime(session)
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
        # 標籤化清單：過硬篩(會噴候選) 或 任一純門檻風格 都回（前端標籤+排序）
        if not sc.passed_filter and not sc.passed_styles:
            continue
        items.append(_to_item(session, sc, name, sector_name, d))

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


_MARK_GAP = 5  # 推薦中斷 ≥5 個交易日視為新段落
_MARK_HORIZON = 10  # 會噴觀察窗（2026-08 定版：10 日內碰到 +10%＝達標，資金周轉導向）


def _mark_segments(
    rec_dates: list[date], trade_dates: list[date], gap: int = _MARK_GAP
) -> list[date]:
    """連續推薦日合併成段落、回起始日。中斷（未推薦的交易日數）≥ gap 才算新段。"""
    idx = {d: i for i, d in enumerate(trade_dates)}
    starts: list[date] = []
    prev_i: int | None = None
    for d in rec_dates:
        i = idx.get(d)
        if i is None:
            continue
        if prev_i is None or (i - prev_i - 1) >= gap:
            starts.append(d)
        prev_i = i
    return starts


def _mark_status(
    hit_pop: bool, days_to_pop: int | None, days_elapsed: int, horizon: int = _MARK_HORIZON
) -> str:
    """段落起始日的達標狀態：_MARK_HORIZON 交易日內噴=hit；窗走完沒噴=miss；窗未走完=pending。

    窗長跟著 _MARK_HORIZON 走（2026-08 定版 10 日），不要在文件或測試裡寫死天數。
    """
    if hit_pop and days_to_pop is not None and days_to_pop <= horizon:
        return "hit"
    if days_elapsed >= horizon:
        return "miss"
    return "pending"


def _tp_windows(
    entries: list[tuple[date, float]], today: date
) -> list[tuple[date, date, float]]:
    """每筆目標價的有效期間：(生效日, 迄日, 目標價)。迄日＝下一筆生效日前一天；最新一筆到 today。"""
    from datetime import timedelta

    out: list[tuple[date, date, float]] = []
    for i, (d, tp) in enumerate(entries):
        end = entries[i + 1][0] - timedelta(days=1) if i + 1 < len(entries) else today
        out.append((d, end, tp))
    return out


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
            hitp, *_ = _prob_lookup(score_, atrp, bias_map.get(d_))
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

    # 長線軌已移除：只回 wave（歷史 long 列仍在 DB，但不再對外）
    scores: dict[str, ScoreDTO | None] = {}
    sc = session.get(models.Score, {"stock_id": stock_id, "date": d, "track": "wave"}) if d else None
    scores["wave"] = _score_dto(sc)

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
    rev_rows = session.execute(
        select(models.RevenueMonthly).where(models.RevenueMonthly.stock_id == stock_id)
        .order_by(models.RevenueMonthly.year.desc(), models.RevenueMonthly.month.desc()).limit(36)
    ).scalars().all()
    rev = rev_rows[0] if rev_rows else None
    # 月營收 YoY 連續正成長月數（由最新月往回數）
    rev_yoy_streak = 0
    for r in rev_rows:
        if r.yoy is not None and r.yoy > 0:
            rev_yoy_streak += 1
        else:
            break
    fin_rows = session.execute(
        select(models.FinancialQuarter).where(models.FinancialQuarter.stock_id == stock_id)
        .order_by(models.FinancialQuarter.year.desc(), models.FinancialQuarter.quarter.desc()).limit(5)
    ).scalars().all()
    fin = fin_rows[0] if fin_rows else None
    fin_prev = fin_rows[1] if len(fin_rows) >= 2 else None

    def _pp(cur: float | None, prev: float | None) -> float | None:
        return round(cur - prev, 2) if cur is not None and prev is not None else None

    # 單季 EPS 年增：找去年同季
    eps_yoy = None
    if fin and fin.eps:
        fin_ly = next((r for r in fin_rows if r.year == fin.year - 1 and r.quarter == fin.quarter), None)
        if fin_ly and fin_ly.eps:
            eps_yoy = round((fin.eps - fin_ly.eps) / abs(fin_ly.eps) * 100, 1)
    # EPS（近4季）：財報單季 EPS 加總（不足 4 季用現有）；全缺則以 收盤價/本益比 推導
    eps_vals = [r.eps for r in fin_rows[:4] if r.eps is not None]
    eps = round(sum(eps_vals), 2) if eps_vals else None
    if eps is None and val and val.pe and val.pe > 0 and close:
        eps = round(close / val.pe, 2)
    fundamental = FundamentalSummary(
        pe=val.pe if val else None,
        pb=val.pb if val else None,
        dividend_yield=val.dividend_yield if val else None,
        eps=eps,
        revenue_yoy=rev.yoy if rev else None,
        revenue_ym=f"{rev.year}/{rev.month:02d}" if rev else None,
        month_revenue=round(rev.revenue / 1e5, 2) if rev and rev.revenue is not None else None,  # 千元→億
        revenue_mom=rev.mom if rev else None,
        fin_quarter=f"{fin.year}Q{fin.quarter}" if fin else None,
        quarter_eps=fin.eps if fin else None,
        gross_margin=fin.gross_margin if fin else None,
        op_margin=fin.op_margin if fin else None,
        net_margin=fin.net_margin if fin else None,
        roe=fin.roe if fin else None,
        gross_margin_qoq=_pp(fin.gross_margin if fin else None, fin_prev.gross_margin if fin_prev else None),
        op_margin_qoq=_pp(fin.op_margin if fin else None, fin_prev.op_margin if fin_prev else None),
        net_margin_qoq=_pp(fin.net_margin if fin else None, fin_prev.net_margin if fin_prev else None),
        eps_yoy=eps_yoy,
        rev_yoy_streak=rev_yoy_streak if rev_rows else None,
    )

    profile = None
    if not stock.is_etf:
        cp = session.get(models.CompanyProfile, stock_id)
        if cp or stock.industry_category:
            profile = CompanyProfileDTO(
                industry=stock.industry_category,
                listed_date=cp.listed_date if cp else None,  # stocks.listed_date 為來源資料日，不可靠

                established_date=cp.established_date if cp else None,
                chairman=cp.chairman if cp else None,
                president=cp.president if cp else None,
                capital_billion=round(cp.capital / 1e8, 1) if cp and cp.capital else None,
                market_cap_billion=round(cp.issued_shares * close / 1e8, 0)
                if cp and cp.issued_shares and close else None,
                website=cp.website if cp else None,
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

    # 產業鏈定位（業務標籤）
    chain_rows = session.execute(
        select(models.IndustryChainMember)
        .where(models.IndustryChainMember.stock_id == stock_id)
        .order_by(models.IndustryChainMember.chain_id, models.IndustryChainMember.node_id)
    ).scalars().all()
    chains = [
        ChainTagDTO(chain_id=c.chain_id, chain_name=c.chain_name, stream=c.stream,
                    main_node=c.main_node, node_name=c.node_name)
        for c in chain_rows
    ]

    # 所屬類股健康度摘要
    sector_brief = None
    if sector is not None:
        sd_date = session.execute(select(func.max(models.SectorDaily.date))).scalar()
        sd = session.get(models.SectorDaily, {"sector_id": sector.id, "date": sd_date}) if sd_date else None
        if sd is not None:
            sector_brief = SectorBriefDTO(
                sector_id=sector.id, name=sector.name, date=sd_date,
                strength_score=sd.strength_score, trend_short=sd.trend_short,
                trend_long=sd.trend_long, rotation_stage=sd.rotation_stage,
                momentum_5=sd.momentum_5, momentum_20=sd.momentum_20,
                foreign_net=sd.foreign_net,
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
        profile=profile,
        chains=chains,
        sector_brief=sector_brief,
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


@router.get("/stocks/{stock_id}/recommendation-marks", response_model=RecommendationMarksResponse)
def stock_recommendation_marks(
    stock_id: str,
    days: int = Query(120, ge=20, le=3000),
    session: Session = Depends(get_session),
) -> RecommendationMarksResponse:
    """K 線推薦標記：波段軌被推薦的段落起始日 + 達標狀態（口徑同回看）。"""
    today_d = _latest_score_date(session)
    if today_d is None:
        return RecommendationMarksResponse(stock_id=stock_id, marks=[])
    rows = session.execute(
        select(models.Score.date, models.Score.passed_filter, models.Score.passed_styles)
        .where(models.Score.stock_id == stock_id, models.Score.track == "wave")
        .order_by(models.Score.date)
    ).all()
    rec_dates = [r[0] for r in rows if r[1] or r[2]]  # 回看同口徑：過硬篩或有風格標籤
    if not rec_dates:
        return RecommendationMarksResponse(stock_id=stock_id, marks=[])
    trade_dates = session.execute(
        select(models.DailyPrice.date)
        .where(models.DailyPrice.stock_id == stock_id, models.DailyPrice.date <= today_d)
        .order_by(models.DailyPrice.date)
    ).scalars().all()
    visible = set(trade_dates[-days:])
    marks: list[RecommendationMark] = []
    for d0 in _mark_segments(rec_dates, trade_dates):
        if d0 not in visible:
            continue
        rv = _lookback_review(session, stock_id, d0, today_d)
        status = _mark_status(rv.hit_pop, rv.days_to_pop, rv.days_elapsed)
        marks.append(
            RecommendationMark(
                date=d0,
                status=status,
                hit_date=rv.hit_pop_date if status == "hit" else None,
                ret_pct=rv.mfe_pct if status == "hit" else None,
            )
        )
    return RecommendationMarksResponse(stock_id=stock_id, marks=marks)


@router.get("/stocks/{stock_id}/target-price", response_model=TargetPriceResponse)
def stock_target_price(
    stock_id: str,
    session: Session = Depends(get_session),
) -> TargetPriceResponse:
    """FactSet 共識目標價：最新一筆＋歷次調整，每筆附有效期間內是否達標。"""
    rows = session.execute(
        select(models.TargetPrice)
        .where(models.TargetPrice.stock_id == stock_id)
        .order_by(models.TargetPrice.date)
    ).scalars().all()
    if not rows:
        return TargetPriceResponse(stock_id=stock_id, latest=None, history=[])

    today_d = session.execute(
        select(func.max(models.DailyPrice.date)).where(models.DailyPrice.stock_id == stock_id)
    ).scalar() or rows[-1].date
    windows = _tp_windows([(r.date, r.target_price) for r in rows], today_d)

    price_rows = session.execute(
        select(models.DailyPrice.date, models.DailyPrice.high)
        .where(
            models.DailyPrice.stock_id == stock_id,
            models.DailyPrice.date >= rows[0].date,
            models.DailyPrice.high.isnot(None),
        )
        .order_by(models.DailyPrice.date)
    ).all()
    latest_close = session.execute(
        select(models.DailyPrice.close)
        .where(models.DailyPrice.stock_id == stock_id, models.DailyPrice.close.isnot(None))
        .order_by(models.DailyPrice.date.desc())
        .limit(1)
    ).scalar()

    entries: list[TargetPriceEntry] = []
    for r, (start, end, tp) in zip(rows, windows):
        hit_date = next((d for d, h in price_rows if start <= d <= end and h >= tp), None)
        entries.append(
            TargetPriceEntry(
                date=r.date, target_price=r.target_price, prev_target=r.prev_target,
                direction=r.direction, target_high=r.target_high, target_low=r.target_low,
                analyst_count=r.analyst_count, rating_bull=r.rating_bull,
                rating_neutral=r.rating_neutral, rating_bear=r.rating_bear,
                eps_est=r.eps_est, hit=hit_date is not None, hit_date=hit_date,
            )
        )
    latest = entries[-1]
    if latest_close:
        latest.upside_pct = round((latest.target_price / latest_close - 1) * 100, 2)
    return TargetPriceResponse(stock_id=stock_id, latest=latest, history=list(reversed(entries)))


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


@router.get("/stocks/{stock_id}/industry-chain", response_model=IndustryChainResponse)
def stock_industry_chain(
    stock_id: str,
    session: Session = Depends(get_session),
) -> IndustryChainResponse:
    """個股產業鏈上下游全景：所屬每條鏈的 上游/中游/下游 主節點與所在位置。"""
    mine = session.execute(
        select(models.IndustryChainMember).where(models.IndustryChainMember.stock_id == stock_id)
    ).scalars().all()
    if not mine:
        return IndustryChainResponse(stock_id=stock_id, chains=[])

    _ORDER = {"上游": 0, "中游": 1, "下游": 2}
    chains: list[ChainStructure] = []
    for cid in sorted({m.chain_id for m in mine}):
        my_rows = [m for m in mine if m.chain_id == cid]
        my_mains = {m.main_node for m in my_rows}
        # 該鏈全體成員 → 主節點結構（各節點公司數）
        rows = session.execute(
            select(models.IndustryChainMember.stream, models.IndustryChainMember.main_node,
                   func.count(func.distinct(models.IndustryChainMember.stock_id)),
                   func.min(models.IndustryChainMember.node_id))
            .where(models.IndustryChainMember.chain_id == cid,
                   models.IndustryChainMember.main_node.is_not(None))
            .group_by(models.IndustryChainMember.stream, models.IndustryChainMember.main_node)
        ).all()
        by_stream: dict[str, list[tuple[str, str, int]]] = {}
        for stream, main_node, cnt, min_node in rows:
            if stream:
                by_stream.setdefault(stream, []).append((min_node, main_node, cnt))
        streams = [
            ChainStream(
                stream=st,
                nodes=[ChainNode(name=n, count=c, mine=n in my_mains)
                       for _, n, c in sorted(by_stream[st])],
            )
            for st in sorted(by_stream, key=lambda s: _ORDER.get(s, 9))
        ]
        chains.append(ChainStructure(
            chain_id=cid,
            chain_name=my_rows[0].chain_name,
            my_nodes=sorted({m.node_name for m in my_rows if m.node_name}),
            streams=streams,
        ))
    return IndustryChainResponse(stock_id=stock_id, chains=chains)


@router.get("/stocks/{stock_id}/dividends", response_model=DividendsResponse)
def stock_dividends(
    stock_id: str,
    session: Session = Depends(get_session_write),
) -> DividendsResponse:
    """股利政策 + 填息判定。首讀懶抓 FinMind 落庫快取（30 天過期重抓）。"""
    from datetime import datetime, timedelta as td_

    rows = session.execute(
        select(models.Dividend).where(models.Dividend.stock_id == stock_id)
    ).scalars().all()
    stale = not rows or all(
        r.updated_at is None or datetime.now() - r.updated_at > td_(days=30) for r in rows
    )
    if stale:
        from ..sources import registry
        from ..sources.base import SourceError
        from ..storage import repositories as repo_

        try:
            df = registry.get_source("finmind").fetch_dividends(stock_id, date(2020, 1, 1))
            if not df.empty:
                recs = df.astype(object).where(df.notna(), None).to_dict("records")
                for r in recs:
                    r["updated_at"] = datetime.now()
                repo_.DividendRepository().upsert_many(session, recs)
                session.flush()
                rows = session.execute(
                    select(models.Dividend).where(models.Dividend.stock_id == stock_id)
                ).scalars().all()
        except SourceError:
            pass  # 來源失敗用既有快取（可能為空）

    # 填息判定：除息前一交易日收盤 → 之後首次收盤 ≥ 該價的交易日數
    ex_dates = [r.cash_ex_date for r in rows if r.cash_ex_date and (r.cash or 0) > 0]
    price_rows: list[tuple[date, float]] = []
    if ex_dates:
        p_start = min(ex_dates) - timedelta(days=10)
        price_rows = session.execute(
            select(models.DailyPrice.date, models.DailyPrice.close)
            .where(models.DailyPrice.stock_id == stock_id, models.DailyPrice.date >= p_start,
                   models.DailyPrice.close.is_not(None))
            .order_by(models.DailyPrice.date)
        ).all()

    def _fill(ex: date) -> tuple[int | None, bool | None]:
        prev = next((p for d_, p in reversed(price_rows) if d_ < ex), None)
        after = [(d_, p) for d_, p in price_rows if d_ >= ex]
        if prev is None or not after:
            return None, None
        for i, (_, p) in enumerate(after):
            if p >= prev:
                return i + 1, True
        return None, False  # 尚未填息

    entries = []
    for r in sorted(rows, key=lambda x: (x.cash_ex_date or date.min, x.period), reverse=True):
        fill_days, filled = _fill(r.cash_ex_date) if r.cash_ex_date and (r.cash or 0) > 0 else (None, None)
        entries.append(DividendEntry(
            period=r.period, cash=r.cash, stock=r.stock,
            cash_ex_date=r.cash_ex_date, pay_date=r.pay_date,
            fill_days=fill_days, filled=filled,
        ))

    # 近 12 個月現金合計 + 以現價換算殖利率
    cutoff = date.today() - timedelta(days=365)
    cash_12m = sum(r.cash or 0 for r in rows if r.cash_ex_date and r.cash_ex_date >= cutoff) or None
    yield_12m = None
    if cash_12m:
        last_close = session.execute(
            select(models.DailyPrice.close).where(models.DailyPrice.stock_id == stock_id)
            .order_by(models.DailyPrice.date.desc()).limit(1)
        ).scalar()
        if last_close:
            yield_12m = round(cash_12m / last_close * 100, 2)
    return DividendsResponse(
        stock_id=stock_id,
        entries=entries,
        cash_12m=round(cash_12m, 2) if cash_12m else None,
        yield_12m=yield_12m,
    )


def _valuation_river(stock_id: str, session: Session, metric) -> PeRiverResponse:
    """估值河流圖共用邏輯：每日估值倍數（PE/PB）分位數 × 隱含基值 = 價格帶。"""
    _LEVELS = (0.1, 0.3, 0.5, 0.7, 0.9)
    rows = session.execute(
        select(models.Valuation.date, metric, models.DailyPrice.close)
        .join(models.DailyPrice, (models.DailyPrice.stock_id == models.Valuation.stock_id)
              & (models.DailyPrice.date == models.Valuation.date))
        .where(models.Valuation.stock_id == stock_id, metric.is_not(None),
               metric > 0, models.DailyPrice.close.is_not(None))
        .order_by(models.Valuation.date)
    ).all()
    if len(rows) < 60:  # 不足一季資料不畫
        return PeRiverResponse(stock_id=stock_id, pe_levels=[], points=[], backfilling=True)

    vals = sorted(v for _, v, _ in rows)

    def _q(p: float) -> float:
        i = p * (len(vals) - 1)
        lo, hi = int(i), min(int(i) + 1, len(vals) - 1)
        return round(vals[lo] + (vals[hi] - vals[lo]) * (i - lo), 2)

    levels = [_q(p) for p in _LEVELS]
    points = [
        PeRiverPoint(date=d, close=close, bands=[round(lv * close / v, 2) for lv in levels])
        for d, v, close in rows
    ]
    cur = rows[-1][1]
    import bisect
    pct = round(bisect.bisect_left(vals, cur) / len(vals) * 100, 1)
    return PeRiverResponse(
        stock_id=stock_id, pe_levels=levels, points=points,
        current_pe=cur, pe_percentile=pct,
        backfilling=len(rows) < 240,  # 未滿一年提示回補中
    )


@router.get("/stocks/{stock_id}/pe-river", response_model=PeRiverResponse)
def stock_pe_river(
    stock_id: str,
    session: Session = Depends(get_session),
) -> PeRiverResponse:
    """本益比河流圖：官方每日 PE 反推隱含 EPS，PE 分位數 × EPS = 價格帶。"""
    return _valuation_river(stock_id, session, models.Valuation.pe)


@router.get("/stocks/{stock_id}/pb-river", response_model=PeRiverResponse)
def stock_pb_river(
    stock_id: str,
    session: Session = Depends(get_session),
) -> PeRiverResponse:
    """本淨比河流圖：官方每日 PB 反推隱含每股淨值，PB 分位數 × BPS = 價格帶。"""
    return _valuation_river(stock_id, session, models.Valuation.pb)


@router.get("/stocks/{stock_id}/tech-summary", response_model=TechSummaryResponse)
def stock_tech_summary(
    stock_id: str,
    session: Session = Depends(get_session),
) -> TechSummaryResponse:
    """技術指標摘要：KD/MACD/乖離現值 + Beta、52 週位置、年化波動。"""
    ind = session.execute(
        select(models.Indicator).where(models.Indicator.stock_id == stock_id)
        .order_by(models.Indicator.date.desc()).limit(1)
    ).scalar_one_or_none()

    prices = list(reversed(session.execute(
        select(models.DailyPrice.date, models.DailyPrice.close, models.DailyPrice.high, models.DailyPrice.low)
        .where(models.DailyPrice.stock_id == stock_id, models.DailyPrice.close.is_not(None))
        .order_by(models.DailyPrice.date.desc()).limit(250)
    ).all()))

    resp = TechSummaryResponse(stock_id=stock_id, date=ind.date if ind else None)
    if ind:
        resp.kd_k, resp.kd_d = ind.kd_k, ind.kd_d
        resp.macd, resp.macd_signal, resp.macd_hist = ind.macd, ind.macd_signal, ind.macd_hist
        resp.bias_20, resp.bias_60 = ind.bias_20, ind.bias_60

    if len(prices) >= 20:
        cur = prices[-1][1]
        highs = [h if h is not None else c for _, c, h, _ in prices]
        lows = [lo if lo is not None else c for _, c, _, lo in prices]
        hi52, lo52 = max(highs), min(lows)
        resp.high_52w, resp.low_52w = round(hi52, 2), round(lo52, 2)
        if hi52 > 0:
            resp.dist_high_pct = round((cur / hi52 - 1) * 100, 1)
        if lo52 > 0:
            resp.dist_low_pct = round((cur / lo52 - 1) * 100, 1)

        rets = [
            prices[i][1] / prices[i - 1][1] - 1
            for i in range(1, len(prices))
            if prices[i - 1][1]
        ]
        if len(rets) >= 20:
            win = rets[-60:]
            mean = sum(win) / len(win)
            var = sum((r - mean) ** 2 for r in win) / len(win)
            resp.volatility_pct = round((var ** 0.5) * (240 ** 0.5) * 100, 1)

        # Beta：近一年股票 vs 加權指數日報酬（共同交易日）
        idx_rows = session.execute(
            select(models.MarketIndex.date, models.MarketIndex.close)
            .where(models.MarketIndex.close.is_not(None),
                   models.MarketIndex.date >= prices[0][0])
            .order_by(models.MarketIndex.date)
        ).all()
        idx_map = {d: c for d, c in idx_rows}
        stk_ret, idx_ret = [], []
        prev = None  # (stock_close, index_close)
        for d, c, _, _ in prices:
            ic = idx_map.get(d)
            if ic is None or c is None:
                continue
            if prev is not None and prev[0] and prev[1]:
                stk_ret.append(c / prev[0] - 1)
                idx_ret.append(ic / prev[1] - 1)
            prev = (c, ic)
        if len(stk_ret) >= 60:
            m_s = sum(stk_ret) / len(stk_ret)
            m_i = sum(idx_ret) / len(idx_ret)
            cov = sum((s - m_s) * (i - m_i) for s, i in zip(stk_ret, idx_ret))
            var_i = sum((i - m_i) ** 2 for i in idx_ret)
            if var_i > 0:
                resp.beta = round(cov / var_i, 2)
    return resp


@router.get("/stocks/{stock_id}/fundamental-history", response_model=FundamentalHistoryResponse)
def fundamental_history(
    stock_id: str,
    months: int = Query(36, ge=6, le=84),
    quarters: int = Query(12, ge=4, le=28),
    session: Session = Depends(get_session),
) -> FundamentalHistoryResponse:
    """基本面歷史（月營收 + 單季財報），升冪，供趨勢圖。"""
    rev_rows = list(reversed(session.execute(
        select(models.RevenueMonthly).where(models.RevenueMonthly.stock_id == stock_id)
        .order_by(models.RevenueMonthly.year.desc(), models.RevenueMonthly.month.desc())
        .limit(months)
    ).scalars().all()))
    fin_rows = list(reversed(session.execute(
        select(models.FinancialQuarter).where(models.FinancialQuarter.stock_id == stock_id)
        .order_by(models.FinancialQuarter.year.desc(), models.FinancialQuarter.quarter.desc())
        .limit(quarters)
    ).scalars().all()))
    return FundamentalHistoryResponse(
        stock_id=stock_id,
        revenues=[
            RevenuePoint(
                ym=f"{r.year}/{r.month:02d}",
                revenue=round(r.revenue / 1e5, 2) if r.revenue is not None else None,  # 千元→億
                yoy=r.yoy, mom=r.mom,
            )
            for r in rev_rows
        ],
        quarters=[
            QuarterPoint(
                label=f"{r.year}Q{r.quarter}",
                eps=r.eps,
                revenue=round(r.revenue / 1e5, 2) if r.revenue is not None else None,
                gross_margin=r.gross_margin, op_margin=r.op_margin,
                net_margin=r.net_margin, roe=r.roe,
            )
            for r in fin_rows
        ],
        # 回補腳本跑完前月營收/季財報只有最新期 → 前端顯示「回補中」提示
        backfilling=len(rev_rows) < 6,
    )


@router.get("/stocks/{stock_id}/attention", response_model=AttentionResponse)
def stock_attention(
    stock_id: str,
    session: Session = Depends(get_session),
) -> AttentionResponse:
    """注意/處置狀態與近 90 日明細（名單由每日 pipeline AttentionStep 更新）。"""
    today = session.execute(select(func.max(models.DailyPrice.date))).scalar() or date.today()
    rows = session.execute(
        select(models.AttentionListing)
        .where(models.AttentionListing.stock_id == stock_id,
               models.AttentionListing.date >= today - timedelta(days=90))
        .order_by(models.AttentionListing.date.desc())
    ).scalars().all()

    status = None
    punish_end = None
    for r in rows:
        if r.kind == "punish" and r.begin_date and r.end_date and r.begin_date <= today <= r.end_date:
            status, punish_end = "punish", r.end_date
            break
    if status is None and any(
        r.kind == "notice" and (today - r.date).days <= 5 for r in rows
    ):
        status = "notice"

    return AttentionResponse(
        stock_id=stock_id,
        status=status,
        punish_end=punish_end,
        notice_count_30d=sum(1 for r in rows if r.kind == "notice" and (today - r.date).days <= 30),
        entries=[
            AttentionEntry(date=r.date, kind=r.kind, times=r.times,
                           begin_date=r.begin_date, end_date=r.end_date, reason=r.reason)
            for r in rows
        ],
    )


@router.get("/stocks/{stock_id}/financial-statements", response_model=FinancialStatementsResponse)
def stock_financial_statements(
    stock_id: str,
    quarters: int = Query(12, ge=4, le=28),
    session: Session = Depends(get_session_write),
) -> FinancialStatementsResponse:
    """資產負債表＋現金流量表摘要。首讀懶抓 FinMind 落庫快取（30 天過期重抓）。"""
    from datetime import datetime, timedelta as td_

    def _load():
        return session.execute(
            select(models.FinancialStatementQuarter)
            .where(models.FinancialStatementQuarter.stock_id == stock_id)
        ).scalars().all()

    rows = _load()
    stale = not rows or all(
        r.updated_at is None or datetime.now() - r.updated_at > td_(days=30) for r in rows
    )
    if stale:
        from ..sources import registry
        from ..sources.base import SourceError
        from ..storage import repositories as repo_

        try:
            df = registry.get_source("finmind").fetch_financial_statements(stock_id, date(2020, 1, 1))
            if not df.empty:
                recs = df.astype(object).where(df.notna(), None).to_dict("records")
                now = datetime.now()
                for r in recs:
                    r["updated_at"] = now
                repo_.FinancialStatementRepository().upsert_many(session, recs)
                session.flush()
                rows = _load()
        except SourceError:
            pass  # 來源失敗（限流/斷線）用既有快取（可能為空）

    shares = session.execute(
        select(models.CompanyProfile.issued_shares)
        .where(models.CompanyProfile.stock_id == stock_id)
    ).scalar()

    def _yi(v: float | None) -> float | None:  # 元 → 億
        return round(v / 1e8, 1) if v is not None else None

    out: list[FinStatementQuarter] = []
    for r in sorted(rows, key=lambda x: (x.year, x.quarter), reverse=True)[:quarters]:
        fcf = r.op_cf + r.capex if r.op_cf is not None and r.capex is not None else None
        out.append(FinStatementQuarter(
            label=f"{r.year}Q{r.quarter}",
            cash=_yi(r.cash),
            current_assets=_yi(r.current_assets),
            total_assets=_yi(r.total_assets),
            current_liab=_yi(r.current_liab),
            total_liab=_yi(r.total_liab),
            equity=_yi(r.equity),
            inventories=_yi(r.inventories),
            receivables=_yi(r.receivables),
            debt_ratio=round(r.total_liab / r.total_assets * 100, 1) if r.total_liab and r.total_assets else None,
            current_ratio=round(r.current_assets / r.current_liab * 100, 1) if r.current_assets and r.current_liab else None,
            bps=round(r.equity / shares, 2) if r.equity and shares else None,
            op_cf=_yi(r.op_cf),
            inv_cf=_yi(r.inv_cf),
            fin_cf=_yi(r.fin_cf),
            capex=_yi(r.capex),
            fcf=_yi(fcf),
        ))
    return FinancialStatementsResponse(stock_id=stock_id, quarters=out)


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
