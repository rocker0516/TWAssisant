"""§22 Daily Pipeline（Production Shadow）：

Market Close t → load inputs (as_of=t) → U_t → Data Quality Gate → Feature Snapshot(t) → Feature Health Gate
→ Champion inference → Calibration → Horizon consistency → Prediction Health Gate → Policy → immutable run + ledger。

Fail-Closed（§26）：任一 gate 失敗 → status=SYSTEM_NO_TRADE、no_trade_reason=原因碼；預測仍以 shadow 保存（研究用），
但 recommended 一律 False。研究與生產共用 builder / registry / labels 同一條路（附錄 A 單一入口）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from ..config import MLEntryConfig, load_config, load_yaml
from ..data import quality
from ..data.universe import build_universe
from ..datasets import builder
from ..features import registry
from ..features.context import FeatureContext
from ..labels import canonical_outcome as co
from ..labels.barriers import EntryStatus, Event
from ..models.calibration import Calibrator, project_monotone_horizons
from ..monitoring import health
from ..recommendation.policy import apply_policy, load_policy
from ..registry.versions import ServingStack, load_champion
from app.storage import models
from app.storage.repositories import MLEntryPredictionRepository, MLEntryRunRepository

log = logging.getLogger(__name__)
HORIZONS = (3, 5, 10)
CORE_COLS = ("open", "high", "low", "close", "volume")


@dataclass
class RunResult:
    run_id: str
    signal_date: str
    status: str
    no_trade: bool
    no_trade_reason: str | None
    universe_count: int
    qualified_count: int
    recommendation_count: int
    health: dict = field(default_factory=dict)
    recommendations: list[dict] = field(default_factory=list)


def _load_models(stack: ServingStack):
    import lightgbm as lgb
    d = stack.dir
    boosters = {t: lgb.Booster(model_file=str(d / f"{t}.lgbm.txt")) for t in stack.tasks}
    cals = {}
    for t in stack.tasks:
        p = d / f"{t}.calibrator.json"
        cals[t] = Calibrator.from_dict(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else Calibrator("none")
    ref = json.loads((d / "feature_reference.json").read_text(encoding="utf-8"))
    return boosters, cals, ref


def _predict(boosters, X: np.ndarray) -> dict[str, np.ndarray]:
    return {t: np.asarray(b.predict(X), dtype="float32") for t, b in boosters.items()}


def run_daily(con, session: Session, signal_date: str | None = None, stack: ServingStack | None = None,
              cfg: MLEntryConfig | None = None, monitoring: dict | None = None) -> RunResult:
    stack = stack or load_champion()
    if stack is None:
        raise RuntimeError("no champion stack; run scripts.mlentry_train_stack first")
    cfg = cfg or load_config()
    mon = monitoring or load_yaml("monitoring")
    if registry.feature_version(cfg.features) != stack.feature_version:
        raise RuntimeError(f"config feature_version {registry.feature_version(cfg.features)} != stack {stack.feature_version}")
    policy = load_policy(stack.policy_name)
    if policy.version != stack.policy_version:
        raise RuntimeError("policy config changed since stack was registered (policy_version mismatch)")
    started = datetime.now(timezone.utc)

    cal, m, mkt, sector, events, fundamentals, flows = builder.load_inputs(con, cfg, signal_date)
    as_of = str(cal.dates[-1])
    if signal_date and as_of != signal_date:
        raise RuntimeError(f"{signal_date} is not a trading day in calendar")
    eligible, elig_flags = build_universe(m, cfg.universe)
    up, dn = quality.limits_from_prev_close(m["close"])
    ctx = FeatureContext(as_of=as_of, calendar=cal, prices=m, market_close=mkt, sector_map=sector, eligible=eligible,
                         events=events, limits={"up": up, "down": dn}, fundamentals=fundamentals, flows=flows)
    boosters, cals, ref = _load_models(stack)
    gates: dict[str, dict] = {}
    fail_reason: str | None = None

    # 1. Data Quality Gate
    u_mask = eligible.loc[as_of]
    u_ids = u_mask.index[u_mask.to_numpy()]
    core_missing = {c: float(m[c].loc[as_of, u_ids].isna().mean()) if len(u_ids) else 1.0 for c in CORE_COLS}
    g = health.data_quality_gate(int(len(u_ids)), ref["universe_daily"], core_missing, mon["data_quality"])
    gates["data_quality"] = {"ok": g.ok, **g.details}
    fail_reason = fail_reason or (None if g.ok else g.reason)
    if mon.get("manual_halt"):
        fail_reason = "MANUAL_HALT"

    # 2. Feature snapshot + health
    snap = registry.snapshot(ctx, cfg.features)                       # index = U_t stock_id
    snap = snap.reindex(columns=stack.feature_names)
    g = health.feature_health_gate(snap, ref["features"], mon["feature_drift"])
    gates["feature_health"] = {"ok": g.ok, **g.details}
    fail_reason = fail_reason or (None if g.ok else g.reason)

    # 3. Inference → calibration → horizon projection
    X = snap.to_numpy(dtype="float32")
    raw = _predict(boosters, X) if len(snap) else {t: np.array([], dtype="float32") for t in stack.tasks}
    calibrated = {t: cals[t].transform(raw[t]) if t.startswith(("target_", "stop_")) else raw[t] for t in stack.tasks}
    proj = {}
    for kind in ("target", "stop"):
        pr = project_monotone_horizons({h: calibrated[f"{kind}_{h}d"] for h in HORIZONS}) if len(snap) else {h: calibrated[f"{kind}_{h}d"] for h in HORIZONS}
        for h in HORIZONS:
            proj[f"p_{kind}_{h}d"] = pr[h]
    g = health.prediction_health_gate({t: raw[t] for t in stack.tasks if t.startswith(("target_", "stop_"))}, ref["predictions"], mon["prediction_health"])
    gates["prediction_health"] = {"ok": g.ok, **g.details}
    fail_reason = fail_reason or (None if g.ok else g.reason)

    # 4. Policy（同日 U_t 全體）
    df = pd.DataFrame({"stock_id": snap.index.to_numpy(), "signal_date": as_of, "atr_pct": snap["atr_pct"].to_numpy(dtype=float),
                       "p_executable": 1.0 - raw["execution"], **{k: v for k, v in proj.items()}})
    rows, day = apply_policy(df, policy)
    df = pd.concat([df, rows], axis=1)
    day_row = day.iloc[0].to_dict() if len(day) else {"qualified_count": 0, "recommendation_count": 0, "no_trade": True}
    if fail_reason:
        df["recommended"] = False
        status, no_trade, reason = "SYSTEM_NO_TRADE", True, fail_reason
    elif int(day_row["recommendation_count"]) == 0:
        status, no_trade, reason = "NO_TRADE", True, "POLICY_NO_CANDIDATE"
    else:
        status, no_trade, reason = "OK", False, None
    gates["recommendation"] = health.recommendation_drift(day_row, _recent_days(session, as_of))

    # 5. Immutable run + 全 universe ledger
    run_id = f"{as_of}_{stack.model_version}_{started.strftime('%H%M%S%f')}"
    entry_date = cal.next(as_of)
    run_row = {
        "run_id": run_id, "signal_date": pd.Timestamp(as_of).date(), "as_of_timestamp": started.replace(tzinfo=None),
        "dataset_version": stack.dataset_version, "universe_version": stack.universe_version, "feature_version": stack.feature_version,
        "label_version": stack.label_version, "model_version": stack.model_version, "calibration_version": stack.calibration_version,
        "policy_version": stack.policy_version, "policy_name": stack.policy_name, "model_status": stack.model_status,
        "deployment_mode": stack.deployment_mode, "promotion_eligible": stack.promotion_eligible, "code_commit": stack.code_commit,
        "status": status, "no_trade": no_trade, "no_trade_reason": reason, "universe_count": int(len(u_ids)),
        "qualified_count": int(day_row["qualified_count"]), "recommendation_count": int(df["recommended"].sum()),
        "health_json": json.dumps(gates, ensure_ascii=False, default=str), "log_tail": None,
    }
    pred_rows = []
    for i, sid in enumerate(df["stock_id"]):
        r = {"run_id": run_id, "stock_id": str(sid), "signal_date": run_row["signal_date"],
             "entry_date": pd.Timestamp(entry_date).date() if entry_date else None, "label_available_date": None,
             "p_executable": _f(df["p_executable"].iat[i]), "atr_pct": _f(df["atr_pct"].iat[i]),
             "p_target_vn": _f(df["p_target_vn"].iat[i]), "p_stop_vn": _f(df["p_stop_vn"].iat[i]),
             "gate_pass": bool(df["gate_pass"].iat[i]), "gate_failure_reason": int(df["gate_failure_reason"].iat[i]),
             "recommendation_score": _f(df["recommendation_score"].iat[i]),
             "rank": int(df["rank"].iat[i]) if np.isfinite(df["rank"].iat[i]) else None, "recommended": bool(df["recommended"].iat[i])}
        for h in HORIZONS:
            r[f"p_target_{h}d_raw"] = _f(raw[f"target_{h}d"][i]); r[f"p_stop_{h}d_raw"] = _f(raw[f"stop_{h}d"][i])
            r[f"p_target_{h}d"] = _f(proj[f"p_target_{h}d"][i]); r[f"p_stop_{h}d"] = _f(proj[f"p_stop_{h}d"][i])
            r[f"pred_mfe_{h}d"] = _f(raw[f"mfe_{h}d"][i])
        pred_rows.append(r)
    MLEntryRunRepository().upsert_many(session, [run_row])
    MLEntryPredictionRepository().upsert_many(session, pred_rows)
    session.commit()
    recs = (df[df["recommended"]].sort_values("rank")[["stock_id", "rank", "recommendation_score", "p_target_10d", "p_stop_10d"]]
            .to_dict(orient="records"))
    log.info("run %s status=%s U=%d qualified=%d rec=%d", run_id, status, len(u_ids), run_row["qualified_count"], run_row["recommendation_count"])
    return RunResult(run_id, as_of, status, no_trade, reason, int(len(u_ids)), run_row["qualified_count"],
                     run_row["recommendation_count"], gates, recs)


def _f(v) -> float | None:
    v = float(v)
    return v if np.isfinite(v) else None


def _recent_days(session: Session, as_of: str, n: int = 60) -> pd.DataFrame | None:
    rows = session.query(models.MLEntryRun.signal_date, models.MLEntryRun.qualified_count, models.MLEntryRun.no_trade) \
        .filter(models.MLEntryRun.signal_date < pd.Timestamp(as_of).date()).order_by(models.MLEntryRun.signal_date.desc()).limit(n).all()
    return pd.DataFrame(rows, columns=["signal_date", "qualified_count", "no_trade"]) if rows else None


def mature_outcomes(con, session: Session, cfg: MLEntryConfig | None = None) -> int:
    """§23.5 Delayed outcome：以目前全歷史價格用同一顆 barrier 引擎回填 ledger（entry_status 先填、成熟後填 outcome）。"""
    cfg = cfg or load_config()
    pending = session.query(models.MLEntryPrediction.run_id, models.MLEntryPrediction.stock_id, models.MLEntryPrediction.signal_date) \
        .filter(models.MLEntryPrediction.matured_at.is_(None)).all()
    if not pending:
        return 0
    from ..data import prices
    from ..data.calendar import load_calendar
    cal = load_calendar(con)
    cols = pd.Index(sorted({str(r.stock_id) for r in pending}), name="stock_id")
    m = prices.load_matrices(con, cal, cols)
    mats = co.build_outcome_matrices(m, cfg.labels)
    today = pd.Timestamp(cal.dates[-1]).date()
    updates = []
    for r in pending:
        sd = str(r.signal_date)
        if sd not in cal or str(r.stock_id) not in cols:
            continue
        ev = int(mats["event_type"].loc[sd, str(r.stock_id)])
        st = int(mats["entry_status"].loc[sd, str(r.stock_id)])
        if st == int(EntryStatus.PENDING):
            continue
        u = {"run_id": r.run_id, "stock_id": str(r.stock_id), "signal_date": r.signal_date, "entry_status": st,
             "entry_date": _d(cal.next(sd)), "label_available_date": _d(cal.shift(sd, cfg.labels.max_horizon)),
             "benchmark_entry_price": _f(mats["benchmark_entry_price"].loc[sd, str(r.stock_id)])}
        if ev != int(Event.PENDING):
            for c in ("event_type", "target_first_hit_day", "stop_first_hit_day", "target_hit_3d", "target_hit_5d", "target_hit_10d",
                      "stop_hit_3d", "stop_hit_5d", "stop_hit_10d", "mfe_10d", "mae_10d", "return_10d"):
                v = mats[c].loc[sd, str(r.stock_id)]
                u[c] = (int(v) if c in ("event_type", "target_first_hit_day", "stop_first_hit_day") and np.isfinite(v) else
                        (_f(v) if np.isfinite(v) else None))
            u["matured_at"] = today
        updates.append(u)
    if updates:
        _partial_upsert(session, updates)
        session.commit()
    return len(updates)


def _d(s: str | None):
    return pd.Timestamp(s).date() if s else None


def _partial_upsert(session: Session, updates: list[dict]) -> None:
    """只更新 outcome 欄，不動預測欄（逐列 UPDATE；成熟回填量小）。"""
    for u in updates:
        key = {"run_id": u.pop("run_id"), "stock_id": u.pop("stock_id")}
        u.pop("signal_date", None)
        session.query(models.MLEntryPrediction).filter_by(**key).update(u)
