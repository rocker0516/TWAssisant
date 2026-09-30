"""ML 進場推薦 FRS v1（Production Shadow）端點。

所有回應都帶 model_status / deployment_mode / promotion_eligible：目前是 RESEARCH_SHADOW，
頁面呈現為「Research Recommendation」，不是正式進場推薦（附錄 C 定位）。
資料源：mlentry_runs / mlentry_predictions（每日 MLEntryDailyStep 寫入）、registry champion.json、
frozen validation（B9 metrics）、promotion.yaml。門檻與解讀文案在後端。
"""

from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..mlentry.config import load_yaml
from ..mlentry.monitoring import performance
from ..mlentry.registry.versions import load_champion
from ..storage import models
from .deps import get_session

router = APIRouter(prefix="/mlentry", tags=["mlentry"])

STATUS_LABEL = {"RESEARCH_SHADOW": "Research Shadow", "PROMOTED": "Promoted"}
NO_TRADE_TEXT = {
    "POLICY_NO_CANDIDATE": "今日沒有股票同時通過 Alpha 與 Risk Gate（市場無機會，非系統故障）",
    "DATA_HEALTH_FAIL": "資料品質未通過（Universe 規模或核心價量缺值異常），系統依 fail-closed 不出單",
    "FEATURE_DRIFT": "特徵分布漂移超過門檻，系統依 fail-closed 不出單",
    "MODEL_HEALTH_FAIL": "預測分布異常，系統依 fail-closed 不出單",
    "CALIBRATION_FAIL": "校準失效，系統依 fail-closed 不出單",
    "MANUAL_HALT": "人工暫停",
    "MARKET_NO_OPPORTUNITY": "市場無機會",
    "EXECUTION_RISK": "可執行性風險",
}


class StackInfo(BaseModel):
    model_version: str | None
    calibration_version: str | None
    policy_version: str | None
    policy_name: str | None
    feature_version: str | None
    label_version: str | None
    dataset_version: str | None
    trained_through: str | None
    model_status: str
    model_status_label: str
    deployment_mode: str
    promotion_eligible: bool
    recommendation_label: str


class RunInfo(BaseModel):
    run_id: str
    signal_date: date
    status: str
    no_trade: bool
    no_trade_reason: str | None
    no_trade_text: str | None
    universe_count: int
    qualified_count: int
    recommendation_count: int
    model_version: str
    policy_version: str
    code_commit: str
    health: dict


class BoardItem(BaseModel):
    rank: int | None
    stock_id: str
    name: str | None
    close: float | None
    recommended: bool
    gate_pass: bool
    gate_failure_reason: int
    recommendation_score: float | None
    p_target_3d: float | None
    p_target_5d: float | None
    p_target_10d: float | None
    p_stop_3d: float | None
    p_stop_5d: float | None
    p_stop_10d: float | None
    pred_mfe_10d: float | None
    p_executable: float | None
    p_target_vn: float | None
    p_stop_vn: float | None
    atr_pct: float | None


class Board(BaseModel):
    stack: StackInfo
    run: RunInfo | None
    items: list[BoardItem]
    candidates: list[BoardItem]
    market_base: dict


def _stack_info() -> StackInfo:
    s = load_champion()
    if s is None:
        return StackInfo(model_version=None, calibration_version=None, policy_version=None, policy_name=None, feature_version=None,
                         label_version=None, dataset_version=None, trained_through=None, model_status="NONE", model_status_label="未部署",
                         deployment_mode="NONE", promotion_eligible=False, recommendation_label="Research Recommendation")
    return StackInfo(model_version=s.model_version, calibration_version=s.calibration_version, policy_version=s.policy_version,
                     policy_name=s.policy_name, feature_version=s.feature_version, label_version=s.label_version,
                     dataset_version=s.dataset_version, trained_through=s.trained_through, model_status=s.model_status,
                     model_status_label=STATUS_LABEL.get(s.model_status, s.model_status), deployment_mode=s.deployment_mode,
                     promotion_eligible=s.promotion_eligible,
                     recommendation_label="正式進場推薦" if s.model_status == "PROMOTED" else "Research Recommendation")


def _run_info(r: models.MLEntryRun) -> RunInfo:
    health = json.loads(r.health_json) if r.health_json else {}
    return RunInfo(run_id=r.run_id, signal_date=r.signal_date, status=r.status, no_trade=r.no_trade, no_trade_reason=r.no_trade_reason,
                   no_trade_text=NO_TRADE_TEXT.get(r.no_trade_reason or "", None), universe_count=r.universe_count,
                   qualified_count=r.qualified_count, recommendation_count=r.recommendation_count, model_version=r.model_version,
                   policy_version=r.policy_version, code_commit=r.code_commit,
                   health={k: {kk: vv for kk, vv in v.items() if kk in ("ok", "n_drifted", "universe_count", "ref_median", "psi_max",
                                                                          "drifted", "out_of_range_day_level", "bad", "why",
                                                                          "qualified_count", "qualified_median_60d", "no_trade_rate_60d")}
                           for k, v in health.items() if isinstance(v, dict)})


def _latest_run(session: Session, signal_date: date | None) -> models.MLEntryRun | None:
    q = select(models.MLEntryRun)
    if signal_date is not None:
        q = q.where(models.MLEntryRun.signal_date == signal_date)
    return session.execute(q.order_by(models.MLEntryRun.signal_date.desc(), models.MLEntryRun.run_id.desc()).limit(1)).scalar_one_or_none()


@router.get("/status", response_model=dict)
def status(session: Session = Depends(get_session)):
    s = load_champion()
    r = _latest_run(session, None)
    contract = load_yaml("promotion")["final_holdout_eligibility"]
    return {"stack": _stack_info().model_dump(), "last_run": _run_info(r).model_dump() if r else None,
            "promotion_check": s.promotion_check if s else {}, "promotion_contract": contract,
            "final_holdout_access": False}


@router.get("/board", response_model=Board)
def board(signal_date: date | None = Query(None), max_candidates: int = Query(50, ge=1, le=500),
          session: Session = Depends(get_session)):
    r = _latest_run(session, signal_date)
    stack = _stack_info()
    if r is None:
        return Board(stack=stack, run=None, items=[], candidates=[], market_base={})
    P = models.MLEntryPrediction
    rows = session.execute(
        select(P, models.Stock.name, models.DailyPrice.close)
        .outerjoin(models.Stock, models.Stock.id == P.stock_id)
        .outerjoin(models.DailyPrice, (models.DailyPrice.stock_id == P.stock_id) & (models.DailyPrice.date == P.signal_date))
        .where(P.run_id == r.run_id)
        .order_by(P.recommended.desc(), P.rank.asc().nulls_last(), P.recommendation_score.desc().nulls_last())
    ).all()

    def item(p, name, close):
        return BoardItem(rank=p.rank, stock_id=p.stock_id, name=name, close=close, recommended=p.recommended, gate_pass=p.gate_pass,
                         gate_failure_reason=p.gate_failure_reason, recommendation_score=p.recommendation_score,
                         p_target_3d=p.p_target_3d, p_target_5d=p.p_target_5d, p_target_10d=p.p_target_10d,
                         p_stop_3d=p.p_stop_3d, p_stop_5d=p.p_stop_5d, p_stop_10d=p.p_stop_10d, pred_mfe_10d=p.pred_mfe_10d,
                         p_executable=p.p_executable, p_target_vn=p.p_target_vn, p_stop_vn=p.p_stop_vn, atr_pct=p.atr_pct)

    items = [item(p, n, c) for p, n, c in rows if p.recommended]
    cands = [item(p, n, c) for p, n, c in rows if p.gate_pass and not p.recommended][:max_candidates]
    s = load_champion()
    fv = s.frozen_validation if s else {}
    market = {"market_target_rate": fv.get("market_target_rate"), "market_stop_rate": fv.get("market_stop_rate"),
              "p_target_10d_mean": float(sum((p.p_target_10d or 0) for p, _, _ in rows) / len(rows)) if rows else None,
              "p_stop_10d_mean": float(sum((p.p_stop_10d or 0) for p, _, _ in rows) / len(rows)) if rows else None}
    return Board(stack=stack, run=_run_info(r), items=items, candidates=cands, market_base=market)


@router.get("/health", response_model=dict)
def health_page(limit: int = Query(60, ge=1, le=500), session: Session = Depends(get_session)):
    s = load_champion()
    contract = load_yaml("promotion")
    mon = load_yaml("monitoring")
    live = performance.rolling_live_metrics(performance.load_matured(session), windows=tuple(mon["live_metrics"]["windows"]),
                                            k=int(mon["live_metrics"]["k"]))
    runs = session.execute(select(models.MLEntryRun).order_by(models.MLEntryRun.signal_date.desc(), models.MLEntryRun.run_id.desc())
                           .limit(limit * 3)).scalars().all()
    seen, hist = set(), []
    for r in runs:                                       # 每日只留最後一個 run
        if r.signal_date in seen:
            continue
        seen.add(r.signal_date)
        hist.append({"signal_date": r.signal_date.isoformat(), "status": r.status, "no_trade_reason": r.no_trade_reason,
                     "universe_count": r.universe_count, "qualified_count": r.qualified_count,
                     "recommendation_count": r.recommendation_count})
        if len(hist) >= limit:
            break
    fv = s.frozen_validation if s else {}
    thr = contract["final_holdout_eligibility"]
    frozen = {"metrics": fv, "thresholds": thr, "promotion_check": s.promotion_check if s else {},
              "promotion_result": "PASS" if (s and s.promotion_eligible) else "FAIL",
              "note": "Frozen Validation：dev 4 個 validation fold 的 OOF（policy_baseline_v1，K=5）；Final Holdout 未開封。"}
    return {"stack": _stack_info().model_dump(), "frozen_validation": frozen, "live": live, "history": hist,
            "monitoring_thresholds": {k: mon[k] for k in ("data_quality", "feature_drift", "prediction_health")}}


@router.get("/runs", response_model=list[RunInfo])
def runs(limit: int = Query(30, ge=1, le=200), session: Session = Depends(get_session)):
    rows = session.execute(select(models.MLEntryRun).order_by(models.MLEntryRun.signal_date.desc(), models.MLEntryRun.run_id.desc())
                           .limit(limit)).scalars().all()
    return [_run_info(r) for r in rows]


@router.get("/runs/{run_id}", response_model=RunInfo)
def run_detail(run_id: str, session: Session = Depends(get_session)):
    r = session.get(models.MLEntryRun, run_id)
    if r is None:
        raise HTTPException(404, "run not found")
    return _run_info(r)
