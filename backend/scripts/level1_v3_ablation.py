# backend/scripts/level1_v3_ablation.py
"""Level 1 v3 基準線＋逐族 ablation（設計 2026-09-28 §2.5）。

步驟累積：baseline(5) → +A → +B → +C → +DE → +F。每步 × horizon × 三種子跑 walk-forward，
只印 dev（2022~2024）；holdout 算了存進 JSON 但不印、不拿來選（只讀不調）。

增量判定：Top-20 dev 淨報酬相對前一步的差 > noise_floor（= 兩步三種子 std 的較大者）才算過。
種子要有離散，LightGBM 必須開 subsample/colsample（否則 n_jobs=1 下三種子完全相同）。

用法：cd backend && .venv/Scripts/python.exe -m scripts.level1_v3_ablation
      [--steps baseline,+A] [--horizons 1,5,10]
"""

from __future__ import annotations

import argparse
import json
import pickle
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import sklearn.linear_model  # noqa: F401  # 必須先於 lightgbm：Windows OpenMP DLL 順序坑
from lightgbm import LGBMRegressor

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.research.level1 import evaluation_v3 as e3  # noqa: E402
from app.research.level1 import features as ft  # noqa: E402
from app.research.level1 import features_v3 as f3  # noqa: E402
from app.research.level1 import prices as pr  # noqa: E402
from app.research.level1 import walkforward as wf  # noqa: E402

_DATA = Path(__file__).resolve().parents[1] / "data"
PERIODS = {"dev_oos": ("2022-01-01", "2024-12-31"), "holdout": ("2025-01-01", "2026-12-31")}
SEEDS = (42, 7, 2024)
STEPS = ["baseline", "+A", "+B", "+C", "+DE", "+F"]
MODEL_PARAMS = dict(objective="quantile", alpha=0.25, n_estimators=100,
                    subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                    n_jobs=1, verbose=-1)


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _make_model(seed: int):
    return lambda: LGBMRegressor(random_state=seed, **MODEL_PARAMS)


def _features_for_step(step: str, prev: list[str], available: set[str]) -> list[str]:
    if step == "baseline":
        return list(f3.BASELINE)
    fam = step.lstrip("+")
    return prev + [n for n in f3.FAMILIES[fam] if n not in prev and n in available]


def _assert_snapshot_fresh(meta: dict) -> None:
    con = sqlite3.connect(_DATA / "twa.db")
    db_max = con.execute("SELECT max(date) FROM daily_prices").fetchone()[0]
    con.close()
    if meta.get("db_max_date") != db_max:
        raise SystemExit(f"level1_v3_targets.pkl 建於 {meta.get('db_max_date')}，DB 為 {db_max}；"
                         "請先重跑 scripts.level1_v3_targets。")


def _mean_std(vals: list[float]) -> dict:
    a = np.array([v for v in vals if v is not None], dtype=float)
    return ({"mean": round(float(a.mean()), 4), "std": round(float(a.std(ddof=0)), 4)}
            if len(a) else {"mean": None, "std": None})


def _fmt(v, spec: str) -> str:
    return "NA" if v is None else format(v, spec)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", default=",".join(STEPS))
    ap.add_argument("--horizons", default="1,5,10")
    ap.add_argument("--out", default=str(_DATA / "level1_v3_results.json"))
    args = ap.parse_args()
    steps = [s for s in STEPS if s in set(args.steps.split(","))]
    horizons = [int(h) for h in args.horizons.split(",")]

    # pickle 為本專案 scripts.level1_v3_targets（Task 3）自產的內部快取檔，非外部輸入，可信任。
    p = pickle.load(open(_DATA / "level1_v3_targets.pkl", "rb"))
    _assert_snapshot_fresh(p["meta"])
    close, mask = p["close"].astype("float64"), p["universe"]
    f64 = {k: p[k].astype("float64") for k in ("open", "high", "low", "volume", "turnover")}
    con = sqlite3.connect(_DATA / "twa.db")
    mkt = pr.load_market_close(con)
    sector = pr.load_sector_map(con)
    con.close()
    from app.storage.database import SessionLocal
    with SessionLocal() as s:
        fund = ft.build_fundamental_features(s, close.index, close.columns)
    fs_all = f3.build_feature_set(f64["open"], f64["high"], f64["low"], close, f64["volume"],
                                  f64["turnover"], mask, mkt, sector, fund)
    fam_desc = ", ".join(f"{k}={len(v)}" for k, v in f3.FAMILIES.items())
    _log(f"特徵全集 {len(fs_all.names)} 個；族群：{fam_desc}")
    available = set(fs_all.names)

    out_path = Path(args.out)
    results = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    results.update({"generated_at": datetime.now().isoformat(timespec="seconds"),
                    "spec": "v3 §2.5 baseline→+A→+B→+C→+DE→+F，q25，三種子，dev 只印",
                    "periods": PERIODS, "seeds": list(SEEDS), "model_params": MODEL_PARAMS,
                    "steps": STEPS, "holdout_read_only": True})
    results.setdefault("features_by_step", {})
    results.setdefault("horizons", {})

    for n in horizons:
        net = p["targets"][n]["net"].astype("float64")
        hres = results["horizons"].setdefault(str(n), {})
        prev_names: list[str] = []
        prev_key = None
        _log(f"── horizon {n}D ──")
        for step in STEPS:
            names = _features_for_step(step, prev_names, available)
            results["features_by_step"][step] = names
            if step not in steps:
                if step in hres and hres[step].get("features") != names:
                    _log(f"⚠ {step}: 快取的舊結果特徵定義已變，未重跑，沿用舊快取")
                prev_names, prev_key = names, step if step in hres else prev_key
                continue
            fs = fs_all.subset(names)
            t0 = time.time()
            seeds_out = {}
            for seed in SEEDS:
                score = wf.walk_forward_scores_v3(_make_model(seed), fs, net, horizon=n)
                seeds_out[str(seed)] = e3.evaluate_v3_by_period(score, net, PERIODS)
            dev = [v["dev_oos"] for v in seeds_out.values() if "dev_oos" in v]
            summ = {
                "top20_mean_net_pct": _mean_std(
                    [d["topk"]["top20"]["mean_net_pct"] for d in dev]),
                "top20_day_win_rate": _mean_std(
                    [d["topk"]["top20"]["day_win_rate"] for d in dev]),
                "calibration_abs_error_pp": _mean_std(
                    [d["calibration"]["abs_error_pp"] for d in dev]),
                "no_entry_diff_pp": _mean_std([d["no_entry"]["diff_pp"] for d in dev]),
                "mean_ic": _mean_std([d["mean_ic"] for d in dev]),
            }
            entry = {"features": names, "seeds": seeds_out, "dev_summary": summ}
            if prev_key and prev_key in hres:
                if hres[prev_key].get("features") != prev_names:
                    entry["delta_vs_prev"] = {
                        "vs": prev_key, "stale": True,
                        "reason": "previous step features differ from current definition"}
                    _log(f"⚠ {step}: 前一步 {prev_key} 特徵定義已變，Δ 不計")
                else:
                    pm = hres[prev_key]["dev_summary"]["top20_mean_net_pct"]
                    cur = summ["top20_mean_net_pct"]
                    if pm["mean"] is None or cur["mean"] is None:
                        entry["delta_vs_prev"] = {
                            "vs": prev_key, "top20_mean_net_pct": None,
                            "noise_floor": None, "passes": None}
                    else:
                        floor = max(pm["std"] or 0.0, cur["std"] or 0.0)
                        delta = round(cur["mean"] - pm["mean"], 4)
                        entry["delta_vs_prev"] = {
                            "vs": prev_key, "top20_mean_net_pct": delta,
                            "noise_floor": round(floor, 4), "passes": bool(delta > floor)}
            hres[step] = entry
            d = entry.get("delta_vs_prev", {})
            t20, win = summ["top20_mean_net_pct"], summ["top20_day_win_rate"]
            cal, ic = summ["calibration_abs_error_pp"], summ["mean_ic"]
            _log(f"  {step:<9} n_feat={len(names):>2} "
                 f"top20={_fmt(t20['mean'], '+.3f')}%±{_fmt(t20['std'], '.3f')} "
                 f"win={_fmt(win['mean'], '.3f')} cal_err={_fmt(cal['mean'], '.2f')}pp "
                 f"no_entry_diff={_fmt(summ['no_entry_diff_pp']['mean'], '+.4f')} "
                 f"ic={_fmt(ic['mean'], '+.4f')} "
                 f"| Δ={_fmt(d.get('top20_mean_net_pct'), '+.4f')} "
                 f"floor={_fmt(d.get('noise_floor'), '.4f')} "
                 f"pass={d.get('passes')} ({time.time()-t0:.0f}s)")
            prev_names, prev_key = names, step
            out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                encoding="utf-8")
    _log(f"已存 {out_path.name}")


if __name__ == "__main__":
    main()
