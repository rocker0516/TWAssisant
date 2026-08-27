"""高確信角落影子軌端點（實驗）。

角落定義=data/corners.json（挖掘凍結產物，30 個），訊號=corner_signals
（每日盤後 CornerStep 寫入）。純觀察層：與排序無關。

結算窗跟著 corners.json 的 target 走：2026-08 目標定版為「10 日內碰到 +10%」，
角落已依此重挖（地板隨基率下移），回看端點同步改 10 日窗——用 30 日窗量 10 日
挖出來的地板，命中率必然虛高。
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
_REVIEW_WINDOW = 10  # 結算窗（交易日）；與 corners.json target=hit10 同口徑


class CornerStockOut(BaseModel):
    stock_id: str
    name: str
    close: float | None


class CornerOut(BaseModel):
    """一個角落的當日亮燈。

    origin 區分兩套挖掘紀律：floor=分年地板≥門檻（既有 30 個）；
    stable_edge=挖掘窗與 holdout 的**同日同錨增量皆為正**（試跑中的 2 個）。
    後者刻意不以絕對地板取勝——窮舉 24k 組合證明「兩窗都 ≥70%」是空集合
    （見 scripts/pop_70_mine.py），故改賭超額穩定；edge_* 才是它的主指標。
    """

    id: str
    atoms: list[str]
    family: str            # crash / dip / allweather
    family_label: str
    origin: str            # floor / stable_edge
    floor: float           # 挖掘窗分年地板命中 %
    per_year: dict         # {"2021": {"hit","n","days"}, ...}
    edge_mine_pp: float | None = None      # 同日同錨增量（挖掘窗）
    edge_holdout_pp: float | None = None   # 同日同錨增量（holdout）
    oos_edge_pp: float | None = None       # 真 OOS 期的同日同錨增量（試跑實測）
    holdout_hit: float | None = None
    holdout_n: int | None = None
    caveat: str | None = None              # 已知的資料/口徑風險，必須讓使用者看見
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
            origin=c.get("origin", "floor"),
            floor=c["floor"], per_year=c["per_year"],
            edge_mine_pp=c.get("edge_mine_pp"), edge_holdout_pp=c.get("edge_holdout_pp"),
            oos_edge_pp=c.get("oos_edge_pp"),
            holdout_hit=c.get("holdout_hit"), holdout_n=c.get("holdout_n"),
            caveat=c.get("caveat"),
            stocks=sorted(by_corner[c["id"]], key=lambda s: s.stock_id),
        )
        for c in corners if c["id"] in by_corner
    ]
    # 試跑中的 stable_edge 置頂：它們的證據型態與既有角落不同（超額而非地板），
    # 混在依地板排序的隊伍中間會被誤讀成「又一個中段班角落」。
    fired.sort(key=lambda c: (c.origin != "stable_edge", -c.floor))

    recent_rows = session.execute(
        select(models.CornerSignal.date,
               func.count().label("signals"),
               func.count(func.distinct(models.CornerSignal.corner_id)).label("corners"))
        .group_by(models.CornerSignal.date)
        .order_by(models.CornerSignal.date.desc()).limit(20)
    ).all()
    recent = [{"date": str(r.date), "signals": r.signals, "corners": r.corners}
              for r in recent_rows]

    n_edge = sum(1 for c in corners if c.get("origin") == "stable_edge")
    floors = [c["floor"] for c in corners if c.get("origin", "floor") == "floor"] or [0.0]
    note = (f"高確信角落（實驗中）：條件由 2021~24 反推挖掘、分年地板 "
            f"{min(floors):.0f}~{max(floors):.0f}%（門檻依 10 日基率倍數搬移，"
            f"非固定 70%），2025~26 軟檢查通過；forward 驗證累積中，"
            "多數日子無訊號屬正常（低波動期＝空手）。與推薦排序無關。")
    if n_edge:
        note += (f" 另有 {n_edge} 個標「超額」的角落試跑中：它們不追絕對地板——窮舉 24k "
                 "組合證實「挖掘窗與 holdout 都 ≥70%」是空集合，且地板最高那批在 holdout "
                 "的同日增量已轉負。這批改以「兩窗同日同錨增量皆為正」入選，賭的是超額穩定。"
                 "注意其 holdout 樣本高度集中在 2026，跨 regime 證據仍薄。"
                 "（2026-08-24 補：換一組原子——ATR 門檻連續掃描＋大盤閘＋流動篩——是找得到 13 組"
                 "兩窗皆 ≥70% 的，見策略室『波段命中挑戰』；但那些增量在同日同 ATR 桶內歸零、"
                 "全由波動度買單，所以不牴觸這裡「靠地板取勝是空集合」的結論。）")
    return CornerSignalsResponse(
        date=d, evaluated=bool(rows) or bool(recent),
        total_corners=len(corners), fired=fired, recent=recent, note=note)


# ── 回看結算：每筆訊號「隔日高錨、_REVIEW_WINDOW 交易日內摸 +10%」實際命中 ──


class CornerReviewRow(BaseModel):
    id: str
    atoms: list[str]
    family_label: str
    origin: str            # floor / stable_edge
    floor: float           # 挖掘窗地板（對照用）
    benchmark: float       # 這個角落該被拿來比什麼：floor 用地板、stable_edge 用 holdout 命中
    n: int                 # 影子期訊號筆數
    matured: int           # 已滿窗（訊號後滿 _REVIEW_WINDOW 交易日）——只有這些進命中率
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

    # 每檔：訊號日之後 1~_REVIEW_WINDOW 個交易日的最高價 / 隔日高（進場錨）/ 經過天數
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
        win = [h for h in highs[k + 1:k + 1 + _REVIEW_WINDOW] if h is not None]
        ent.append(entry)
        mfe.append(max(win) / entry if entry and win else None)
        elapsed.append(len(dates) - 1 - k)
    sigs["entry"] = ent
    sigs["mfe"] = mfe
    sigs["elapsed"] = elapsed
    sigs["hit"] = (sigs["mfe"] >= 1.10).fillna(False)
    # 滿窗才結算：只用「訊號後已走滿 _REVIEW_WINDOW 交易日」的 cohort 算命中率。
    # 若把「提早摸到 +10%」也提前結算，贏家先進分母、輸家還掛著 → 命中率必然灌水。
    sigs["matured"] = sigs["elapsed"] >= _REVIEW_WINDOW + 1

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
        origin = c.get("origin", "floor")
        by_corner.append(CornerReviewRow(
            id=cid, atoms=c["atoms"],
            family_label=_FAMILY_LABEL.get(c["family"], c["family"]),
            origin=origin, floor=c["floor"],
            # stable_edge 沒有「地板」承諾（它的地板本來就只有 48~55%），拿地板當
            # 及格線會給出過寬的判定；改用它自己的 holdout 命中當基準。
            benchmark=(c.get("holdout_hit") or c["floor"]) if origin == "stable_edge"
            else c["floor"],
            hit_rate=a["hit_rate"],
            n=a["n"], matured=a["matured"], hits=a["hits"], pending=a["pending"],
            early_hits=a["early_hits"]))
    by_corner.sort(key=lambda r: (r.origin != "stable_edge", -r.matured))

    by_day = [
        {"date": str(dt), **_agg(g)}
        for dt, g in uniq.groupby("date")
    ][-30:]
    by_day.reverse()

    note = (f"結算口徑＝訊號隔日最高價進場、之後 {_REVIEW_WINDOW} 個交易日內曾摸 +10%"
            f"（與 corners.json target=hit10 同口徑）；命中率只算「已滿 {_REVIEW_WINDOW} "
            "交易日」的訊號（避免贏家提早結算的灌水偏差），未滿窗但已先摸到的另列供參考。"
            "2026-06-11 起的訊號在挖掘資料之外，屬真 out-of-sample。"
            "整體列已去重（同股同日多角落只算一次）。")
    return CornerReviewResponse(
        as_of=as_of, oos_from="2026-06-11", overall_unique=_agg(uniq),
        by_corner=by_corner, by_day=by_day, note=note)
