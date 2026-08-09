"""高確信角落影子軌端點（實驗）。

角落定義=data/corners.json（挖掘凍結產物，30 個、分年地板≥70%），
訊號=corner_signals（每日盤後 CornerStep 寫入）。純觀察層：與排序無關。
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..engines.corner_defs import load_corners
from ..storage import models
from .deps import get_session

router = APIRouter(prefix="/corners", tags=["corners"])

_FAMILY_LABEL = {"crash": "深崩期", "dip": "回檔期", "allweather": "全天候"}


class CornerStockOut(BaseModel):
    stock_id: str
    name: str
    close: float | None


class CornerOut(BaseModel):
    id: str
    atoms: list[str]
    family: str            # crash / dip / allweather
    family_label: str
    floor: float           # 挖掘窗分年地板命中 %
    per_year: dict         # {"2021": {"hit","n","days"}, ...}
    stocks: list[CornerStockOut]


class CornerSignalsResponse(BaseModel):
    date: date | None
    evaluated: bool        # 該日是否已有影子軌評估（表裡有無該日資料無法區分零訊號/未跑，靠 pipeline 起跑日判斷）
    total_corners: int
    fired: list[CornerOut]
    recent: list[dict]     # 近 20 個訊號日 [{date, signals, corners}]
    note: str


@router.get("", response_model=CornerSignalsResponse)
def corner_signals(
    d: date | None = Query(None, alias="date"),
    session: Session = Depends(get_session),
) -> CornerSignalsResponse:
    corners = load_corners()
    if d is None:
        d = session.execute(
            select(func.max(models.DailyPrice.date))).scalar_one_or_none()

    rows = session.execute(
        select(models.CornerSignal, models.Stock.name)
        .join(models.Stock, models.Stock.id == models.CornerSignal.stock_id)
        .where(models.CornerSignal.date == d)
    ).all() if d else []

    by_corner: dict[str, list[CornerStockOut]] = {}
    for sig, name in rows:
        by_corner.setdefault(sig.corner_id, []).append(
            CornerStockOut(stock_id=sig.stock_id, name=name, close=sig.close))

    fired = [
        CornerOut(
            id=c["id"], atoms=c["atoms"], family=c["family"],
            family_label=_FAMILY_LABEL.get(c["family"], c["family"]),
            floor=c["floor"], per_year=c["per_year"],
            stocks=sorted(by_corner[c["id"]], key=lambda s: s.stock_id),
        )
        for c in corners if c["id"] in by_corner
    ]
    fired.sort(key=lambda c: -c.floor)

    recent_rows = session.execute(
        select(models.CornerSignal.date,
               func.count().label("signals"),
               func.count(func.distinct(models.CornerSignal.corner_id)).label("corners"))
        .group_by(models.CornerSignal.date)
        .order_by(models.CornerSignal.date.desc()).limit(20)
    ).all()
    recent = [{"date": str(r.date), "signals": r.signals, "corners": r.corners}
              for r in recent_rows]

    note = ("高確信角落（實驗中）：條件由 2021~24 反推挖掘、分年地板≥70%，"
            "2025~26 軟檢查通過；forward 驗證累積中，多數日子無訊號屬正常"
            "（低波動期＝空手）。與推薦排序無關。")
    return CornerSignalsResponse(
        date=d, evaluated=bool(rows) or bool(recent),
        total_corners=len(corners), fired=fired, recent=recent, note=note)


# ── 回看結算：每筆訊號「隔日高錨、30 交易日內摸 +10%」實際命中 ──


class CornerReviewRow(BaseModel):
    id: str
    atoms: list[str]
    family_label: str
    floor: float           # 挖掘窗地板（對照用）
    n: int                 # 影子期訊號筆數
    matured: int           # 已滿窗（訊號後滿 30 交易日）——只有這些進命中率
    hits: int
    hit_rate: float | None  # hits / matured（滿窗口徑，無提早結算偏差）
    pending: int           # 未滿窗
    early_hits: int        # 未滿窗但已先摸到 +10%（結果已確定，供參考）


class CornerReviewResponse(BaseModel):
    as_of: date | None
    oos_from: str          # 這天之後的訊號在挖掘資料範圍外（真 out-of-sample）
    overall_unique: dict   # 去重股-日的整體 {n, matured, hits, hit_rate, pending}
    by_corner: list[CornerReviewRow]
    by_day: list[dict]     # 近 30 個訊號日 [{date, n, hits, matured, pending}]
    note: str


@router.get("/review", response_model=CornerReviewResponse)
def corner_review(session: Session = Depends(get_session)) -> CornerReviewResponse:
    import pandas as pd

    corners = {c["id"]: c for c in load_corners()}
    sigs = pd.DataFrame(session.execute(
        select(models.CornerSignal.stock_id, models.CornerSignal.date,
               models.CornerSignal.corner_id)).all(),
        columns=["stock_id", "date", "corner_id"])
    as_of = session.execute(select(func.max(models.DailyPrice.date))).scalar_one_or_none()
    if sigs.empty or as_of is None:
        return CornerReviewResponse(
            as_of=as_of, oos_from="2026-06-11", overall_unique={},
            by_corner=[], by_day=[], note="尚無訊號")

    lo = sigs["date"].min()
    px = pd.DataFrame(session.execute(
        select(models.DailyPrice.stock_id, models.DailyPrice.date, models.DailyPrice.high)
        .where(models.DailyPrice.stock_id.in_(sigs["stock_id"].unique().tolist()),
               models.DailyPrice.date >= lo)
        .order_by(models.DailyPrice.stock_id, models.DailyPrice.date)).all(),
        columns=["stock_id", "date", "high"])

    # 每檔：訊號日之後 1~30 個交易日的最高價 / 隔日高（進場錨）/ 經過天數
    out = {}
    for sid, g in px.groupby("stock_id", sort=False):
        dates = g["date"].tolist()
        highs = g["high"].tolist()
        idx = {dt: k for k, dt in enumerate(dates)}
        out[sid] = (dates, highs, idx)
    ent, mfe, elapsed = [], [], []
    for sid, dt in zip(sigs["stock_id"], sigs["date"]):
        dates, highs, idx = out.get(sid, ([], [], {}))
        k = idx.get(dt)
        if k is None or k + 1 >= len(dates):
            ent.append(None); mfe.append(None); elapsed.append(0)
            continue
        entry = highs[k + 1]
        win = [h for h in highs[k + 1:k + 31] if h is not None]
        ent.append(entry)
        mfe.append(max(win) / entry if entry and win else None)
        elapsed.append(len(dates) - 1 - k)
    sigs["entry"] = ent
    sigs["mfe"] = mfe
    sigs["elapsed"] = elapsed
    sigs["hit"] = (sigs["mfe"] >= 1.10).fillna(False)
    # 滿窗才結算：只用「訊號後已滿 30 交易日」的 cohort 算命中率。
    # 若把「提早摸到 +10%」也提前結算，贏家先進分母、輸家還掛著 → 命中率必然灌水。
    sigs["matured"] = sigs["elapsed"] >= 31

    def _agg(df) -> dict:
        mat = df[df["matured"]]
        early = df[~df["matured"] & df["hit"]]  # 未滿窗但已先摸到（結果已確定為命中）
        return {"n": int(len(df)), "matured": int(len(mat)),
                "hits": int(mat["hit"].sum()),
                "hit_rate": round(float(mat["hit"].mean()) * 100, 1) if len(mat) else None,
                "pending": int(len(df) - len(mat)),
                "early_hits": int(len(early))}

    uniq = sigs.drop_duplicates(["stock_id", "date"])
    by_corner = []
    for cid, g in sigs.groupby("corner_id"):
        c = corners.get(cid)
        if not c:
            continue
        a = _agg(g)
        by_corner.append(CornerReviewRow(
            id=cid, atoms=c["atoms"],
            family_label=_FAMILY_LABEL.get(c["family"], c["family"]),
            floor=c["floor"], hit_rate=a["hit_rate"],
            n=a["n"], matured=a["matured"], hits=a["hits"], pending=a["pending"],
            early_hits=a["early_hits"]))
    by_corner.sort(key=lambda r: -r.matured)

    by_day = [
        {"date": str(dt), **_agg(g)}
        for dt, g in uniq.groupby("date")
    ][-30:]
    by_day.reverse()

    note = ("結算口徑＝訊號隔日最高價進場、之後 30 個交易日內曾摸 +10%（與挖掘同口徑）；"
            "命中率只算「已滿 30 交易日」的訊號（避免贏家提早結算的灌水偏差），"
            "未滿窗但已先摸到的另列供參考。2026-06-11 起的訊號在挖掘資料之外，"
            "屬真 out-of-sample。整體列已去重（同股同日多角落只算一次）。")
    return CornerReviewResponse(
        as_of=as_of, oos_from="2026-06-11", overall_unique=_agg(uniq),
        by_corner=by_corner, by_day=by_day, note=note)
