"""C1：訓練正式 ServingStack（§12 十模型、§14 calibration、§19.6 monthly retrain）。

- 訓練資料：development 分區、成熟且 label_available_date <= trained_through 的列，rolling train_window_days（與 OOF 同源規則）。
- Calibration：只用 dev OOF（oof/<task>__lgbm/predictions.parquet）fit；方法沿用 oof/calibration.json 的選擇。
- MFE cap：由本次 training 樣本估（存 artifact）。
- feature_reference.json：訓練樣本每個特徵的分位／缺值率（feature drift 的參考分布）與 OOF 預測分布（prediction drift 參考）。
- 產出 stack.json，status = RESEARCH_SHADOW、promotion_eligible 依 B9 promotion_check。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import load_yaml
from ..datasets import api
from ..datasets.store import code_commit
from ..models import oof
from ..models.calibration import Calibrator
from ..models.estimators import make_model
from ..models.tasks import all_tasks, day_weights, task_frame
from ..monitoring.monitor_modes import monitor_mode_for
from ..recommendation.policy import load_policy
from ..registry.versions import STATUS_RESEARCH_SHADOW, SERVING_ROOT, ServingStack, set_champion
from ..validation.purge import purged_train_positions

log = logging.getLogger(__name__)
TASKS = ("execution", "target_3d", "target_5d", "target_10d", "stop_3d", "stop_5d", "stop_10d", "mfe_3d", "mfe_5d", "mfe_10d")
QS = (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)


def _calibration_methods(ds_dir: Path) -> dict[str, str]:
    p = ds_dir / "oof" / "calibration.json"
    if not p.exists():
        return {}
    r = json.loads(p.read_text(encoding="utf-8"))
    return {t: v["method"] for t, v in r["tasks"].items()}


DAY_LEVEL_MAX_UNIQUE = 40      # 每日快照 unique 值 <= 此數 → 日／類股層級特徵（PSI 無意義，改範圍檢查）


def build_feature_reference(features: pd.DataFrame, names: list[str], tr_mask: np.ndarray, sample_every: int = 5) -> dict:
    """每個特徵：pooled 分位、缺值率、以及「訓練期每日快照對 pooled 的 PSI」分布（p99 / max）。

    live 只在當日 PSI 超過自己訓練期的 p99 才算 drift，避免大盤大漲跌日被誤判。
    日層級特徵（unique 少）不算 PSI，記 daily 值分位供範圍檢查。
    """
    from ..monitoring.health import psi
    tr = features.loc[tr_mask]
    X = tr[names].to_numpy(dtype="float32")
    dates = tr["signal_date"].to_numpy()
    uniq_dates = np.unique(dates)[::sample_every]
    ref = {}
    day_groups = {d: np.nonzero(dates == d)[0] for d in uniq_dates}
    med_u = float(np.median([len(idx) for idx in day_groups.values()]))
    day_level_max = min(DAY_LEVEL_MAX_UNIQUE, max(1.0, 0.05 * med_u))     # 相對 U_t 規模：真實 ~1,700 檔 → 40
    for i, n in enumerate(names):
        col = X[:, i].astype(float)
        q = {str(qq): float(np.nanquantile(col, qq)) for qq in QS}
        nun = np.median([len(np.unique(col[idx][np.isfinite(col[idx])])) for idx in day_groups.values()])
        entry = {"missing_rate": float(np.isnan(col).mean()), "q": q, "mean": float(np.nanmean(col)), "std": float(np.nanstd(col)),
                 "day_level": bool(nun <= day_level_max)}
        entry["monitor_mode"] = monitor_mode_for(n)                 # Spec A §2.6：新 stack 原生帶分布監控模式
        if entry["day_level"]:
            daily = np.array([np.nanmedian(col[idx]) for idx in day_groups.values()], dtype=float)
            entry["daily_q"] = {"0.005": float(np.nanquantile(daily, 0.005)), "0.995": float(np.nanquantile(daily, 0.995))}
        else:
            ps = np.array([psi(q, col[idx]) for idx in day_groups.values()], dtype=float)
            ps = ps[np.isfinite(ps)]
            entry["psi_p99"] = float(np.quantile(ps, 0.99)) if len(ps) else 0.25
            entry["psi_max"] = float(ps.max()) if len(ps) else 0.25
        ref[n] = entry
    return ref


def refresh_feature_reference(stack: ServingStack, ds_dir: Path | None = None) -> Path:
    """只重算 feature_reference.json（模型不動）。"""
    ds_dir = ds_dir or api.latest_dataset_dir()
    dev = api.load_development(ds_dir, outcome_columns=["matured"])
    vcfg = load_yaml("validation")
    dates = pd.Index(sorted(dev.features["signal_date"].unique()))
    tr_pos = purged_train_positions(len(dates), int(dev.splits["max_horizon"]), vcfg.get("train_window_days"))
    tr_mask = dev.features["signal_date"].isin(set(dates[tr_pos])).to_numpy()
    p = stack.dir / "feature_reference.json"
    ref = json.loads(p.read_text(encoding="utf-8"))
    ref["features"] = build_feature_reference(dev.features, stack.feature_names, tr_mask)
    p.write_text(json.dumps(ref, ensure_ascii=False), encoding="utf-8")
    return p


def train_stack(ds_dir: Path | None = None, policy_name: str = "policy_baseline_v1", root: Path = SERVING_ROOT,
                models_cfg: dict | None = None, set_as_champion: bool = True) -> ServingStack:
    ds_dir = ds_dir or api.latest_dataset_dir()
    models_cfg = models_cfg or load_yaml("models")
    vcfg = load_yaml("validation")
    specs = all_tasks()
    need = sorted({c for t in TASKS for c in specs[t].outcome_columns} | {"target_first_hit_day", "stop_first_hit_day"})
    dev = api.load_development(ds_dir, outcome_columns=need)
    names = dev.manifest["feature_names"]
    X_all = dev.features.set_index("sample_id")
    Xf = X_all[names].to_numpy(dtype="float32")
    pos = pd.Series(np.arange(len(X_all)), index=X_all.index)
    dates = pd.Index(sorted(dev.features["signal_date"].unique()))
    max_h = int(dev.splits["max_horizon"])
    # 訓練窗：以「最後成熟日」為 test_start 的 purge 規則 → 位置切片
    tr_pos = purged_train_positions(len(dates), max_h, vcfg.get("train_window_days"))
    tr_dates = set(dates[tr_pos])
    trained_through = dates[tr_pos[-1]]
    policy = load_policy(policy_name)
    ver_tag = f"{ds_dir.name.split('_')[-1][:8]}_{trained_through.replace('-', '')}"
    model_version = f"mlentry_lgbm_{ver_tag}"
    d = root / model_version; d.mkdir(parents=True, exist_ok=True)

    cal_methods = _calibration_methods(ds_dir)
    artifacts: dict[str, dict] = {}
    pred_ref: dict[str, dict] = {}
    for t in TASKS:
        task = specs[t]
        tf = task_frame(task, dev.outcomes)
        tf = tf[tf["signal_date"].isin(tr_dates) & tf["sample_id"].isin(X_all.index)]
        rows = pos.loc[tf["sample_id"]].to_numpy()
        y = tf["y"].to_numpy(dtype="float32")
        w = (tf["w_task"].to_numpy() * day_weights(tf["signal_date"])).astype("float32")
        cap = None
        if task.kind == "regression":
            cap = float(np.quantile(y, float(models_cfg.get("mfe_winsor_quantile", 0.99)))); y = np.minimum(y, cap)
        model = make_model("lgbm", task.kind, models_cfg).fit(Xf[rows], y, w)
        model.model.booster_.save_model(str(d / f"{t}.lgbm.txt"))
        meta = {"kind": task.kind, "n_train": int(len(y)), "winsor_cap": cap,
                "train_positive_rate": float(np.average(y, weights=w)) if task.kind == "binary" else None}
        method = cal_methods.get(t, "none") if task.kind == "binary" else "none"
        if method != "none":
            preds, _ = oof.load_oof(ds_dir, t, "lgbm")
            lab = preds[preds["y"].notna() & (preds["w"].fillna(0) > 0)]
            cal = Calibrator(method).fit(lab["pred"].to_numpy(), lab["y"].to_numpy(), lab["w"].to_numpy())
            (d / f"{t}.calibrator.json").write_text(json.dumps(cal.to_dict()), encoding="utf-8")
            meta["calibration"] = {"method": method, "fit_sample_count": int(len(lab)),
                                   "fit_period": [str(lab["signal_date"].min()), str(lab["signal_date"].max())]}
        else:
            meta["calibration"] = {"method": "none"}
        artifacts[t] = meta
        # prediction drift 參考：OOF 預測分布
        oof_dir = ds_dir / "oof" / f"{t}__lgbm" / "predictions.parquet"
        if oof_dir.exists():
            p = oof.load_oof(ds_dir, t, "lgbm")[0]["pred"].to_numpy(dtype=float)
            pred_ref[t] = {"mean": float(np.nanmean(p)), "std": float(np.nanstd(p)),
                           "q": {str(q): float(np.nanquantile(p, q)) for q in QS}}
        log.info("trained %s n=%d cal=%s", t, len(y), method)

    tr_mask = dev.features["signal_date"].isin(tr_dates).to_numpy()
    feat_ref = build_feature_reference(dev.features, names, tr_mask)
    ut = dev.features.loc[tr_mask].groupby("signal_date").size()
    (d / "feature_reference.json").write_text(json.dumps(
        {"features": feat_ref, "predictions": pred_ref, "universe_daily": {"median": float(ut.median()), "p05": float(ut.quantile(0.05))},
         "train_dates": [str(dates[tr_pos[0]]), str(trained_through)]}, ensure_ascii=False), encoding="utf-8")
    (d / "artifacts.json").write_text(json.dumps(artifacts, ensure_ascii=False, indent=2), encoding="utf-8")

    cal_ver = "c_" + __import__("hashlib").sha256(json.dumps({t: artifacts[t]["calibration"] for t in TASKS}, sort_keys=True).encode()).hexdigest()[:8]
    frozen, promo = {}, {}
    mp = ds_dir / "policy" / policy_name / "metrics.json"
    if mp.exists():
        m = json.loads(mp.read_text(encoding="utf-8"))
        promo = m.get("promotion_check", {})
        pe5 = m["at_k"]["5"]["point"]["policy"]; b5 = m["at_k"]["5"]["bootstrap"]["20"]
        frozen = {"target_lift_at_5": pe5["target_lift"], "stop_ratio_at_5": pe5["stop_ratio"], "coverage": pe5["coverage"],
                  "mean_net10": pe5["mean_net10"], "worst_fold_lift_at_5": m["at_k"]["5"]["folds"]["policy"]["worst_fold_lift"],
                  "ci_target_lift": [b5["target_lift"]["lo"], b5["target_lift"]["hi"]],
                  "ci_stop_ratio": [b5["stop_ratio"]["lo"], b5["stop_ratio"]["hi"]],
                  "ci_net10": [b5["net10"]["lo"], b5["net10"]["hi"]], "market_target_rate": pe5["market_target_rate"],
                  "market_stop_rate": pe5["market_stop_rate"], "dev_folds": len(dev.splits["dev_folds"]),
                  "dev_period": [dev.splits["dev_start"], dev.splits["dev_end"]]}
    stack = ServingStack(model_version=model_version, calibration_version=cal_ver, policy_version=policy.version,
                         policy_name=policy_name, feature_version=dev.manifest["versions"]["feature_version"],
                         label_version=dev.manifest["versions"]["label_version"], universe_version=dev.manifest["versions"]["universe_version"],
                         dataset_version=ds_dir.name, split_version=dev.splits["split_version"], trained_through=str(trained_through),
                         train_window_days=vcfg.get("train_window_days"), feature_names=list(names), tasks=list(TASKS),
                         calibration_methods={t: artifacts[t]["calibration"]["method"] for t in TASKS}, code_commit=code_commit(),
                         model_status=STATUS_RESEARCH_SHADOW, deployment_mode="SHADOW",
                         promotion_eligible=bool(promo.get("eligible", False)), promotion_check=promo, frozen_validation=frozen)
    stack.save(root)
    if set_as_champion:
        set_champion(stack, root)
    log.info("stack saved: %s (status=%s, promotion_eligible=%s)", model_version, stack.model_status, stack.promotion_eligible)
    return stack
