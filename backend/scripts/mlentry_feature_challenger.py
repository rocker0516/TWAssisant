"""Feature challenger harness（promotion.yaml challenger_rules）：只改 feature_version，其餘全同 baseline。

    python -m scripts.mlentry_feature_challenger --challenger ds_<v2 dir name> [--baseline ds_<v1 dir name>]

Stage A：target_10d / stop_10d lgbm OOF → ConditionalTargetLift / ConditionalStopReduction（ATR decile 內 top/bottom 10%）
         對 baseline 的配對差；沒有改善即淘汰。
Stage B：只有 Stage A 通過才跑——policy_baseline_v1 相同 gate（95/20）、ranking、K=5，區塊 bootstrap，
         與 baseline policy 在同一組日上配對。
輸出 data/mlentry/<challenger>/challenger/{report.md, metrics.json}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.mlentry.config import load_yaml  # noqa: E402
from app.mlentry.datasets import api  # noqa: E402
from app.mlentry.datasets.store import DEFAULT_ROOT  # noqa: E402
from app.mlentry.evaluation import policy_metrics as pm  # noqa: E402
from app.mlentry.evaluation import volatility_control as vc  # noqa: E402
from app.mlentry.models import oof  # noqa: E402
from app.mlentry.models.tasks import all_tasks  # noqa: E402
from app.mlentry.recommendation.policy import apply_policy, load_policy  # noqa: E402

STAGE_A_TASKS = ("target_10d", "stop_10d")
OUTCOME_COLS = ["event_type", "target_hit_10d", "stop_hit_10d", "return_10d", "mfe_10d", "mae_10d", "entry_executable", "matured",
                "entry_status"]


def ensure_oof(ds_dir: Path) -> None:
    specs = all_tasks()
    missing = [t for t in STAGE_A_TASKS if not (ds_dir / "oof" / f"{t}__lgbm" / "predictions.parquet").exists()]
    if not missing:
        return
    need = sorted({c for t in missing for c in specs[t].outcome_columns})
    dev = api.load_development(ds_dir, outcome_columns=need)
    for t in missing:
        oof.run_oof(dev, specs[t], "lgbm", dev.manifest["feature_names"], out_root=ds_dir)


def frame(ds_dir: Path) -> pd.DataFrame:
    dev = api.load_development(ds_dir, feature_columns=["atr_pct"], outcome_columns=OUTCOME_COLS)
    t, _ = oof.load_oof(ds_dir, "target_10d", "lgbm"); s, _ = oof.load_oof(ds_dir, "stop_10d", "lgbm")
    df = (t[["sample_id", "stock_id", "signal_date", "fold", "pred"]].rename(columns={"pred": "p_target_10d"})
          .merge(s[["sample_id", "pred"]].rename(columns={"pred": "p_stop_10d"}), on="sample_id")
          .merge(dev.features[["sample_id", "atr_pct"]], on="sample_id")
          .merge(dev.outcomes.drop(columns=["signal_date"]), on="sample_id"))
    df["target"] = df["target_hit_10d"].astype(float); df["stop"] = df["stop_hit_10d"].astype(float)
    df["atr_pct"] = df["atr_pct"].fillna(df["atr_pct"].median())
    return df


def stage_a(df: pd.DataFrame) -> dict:
    ev = df[df["target"].notna() & (df["matured"] == 1)]
    ct = vc.atr_conditional_lift(ev, "p_target_10d", "target", top=True)
    cs = vc.atr_conditional_lift(ev, "p_stop_10d", "stop", top=False)
    return {"conditional_target_lift": float(ct.iloc[-1]["ratio"]), "ctl_worst_fold": float(ct.iloc[-1]["worst_fold_ratio"]),
            "ctl_by_decile": ct.iloc[:-1]["ratio"].round(3).tolist(),
            "conditional_stop_reduction": float(cs.iloc[-1]["ratio"]), "csr_worst_fold": float(cs.iloc[-1]["worst_fold_ratio"]),
            "csr_by_decile": cs.iloc[:-1]["ratio"].round(3).tolist()}


def stage_b(df: pd.DataFrame, cfg, contract: dict) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    rows, _ = apply_policy(df, cfg)
    df = pd.concat([df, rows], axis=1)
    ev = df[df["target"].notna() & (df["matured"] == 1)]
    market = pm.daily_aggregates(ev, pd.Series(True, index=ev.index), cfg.cost_rt)
    pol = ev["recommended"] & (ev["rank"] <= 5)
    agg = pm.daily_aggregates(ev, pol, cfg.cost_rt)
    pe = pm.point_estimates(agg, market, ev.loc[pol]); fs = pm.fold_stats(ev, pol)
    return {"point": pe, "folds": fs, "promotion": pm.promotion_check(pe, fs, contract)}, agg, market


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--challenger", required=True); ap.add_argument("--baseline", default="ds_2026-09-29_6613b41a35737d0ca763aaa2")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    args = ap.parse_args(argv)
    root = Path(args.root); ch, bl = root / args.challenger, root / args.baseline
    contract = load_yaml("promotion"); cfg = load_policy("policy_baseline_v1"); bcfg = contract["bootstrap"]
    ensure_oof(ch); ensure_oof(bl)
    dch, dbl = frame(ch), frame(bl)
    mch, mbl = json.load(open(ch / "manifest.json", encoding="utf-8")), json.load(open(bl / "manifest.json", encoding="utf-8"))
    a_ch, a_bl = stage_a(dch), stage_a(dbl)
    lines = [f"# Feature challenger: {ch.name} (feature {mch['versions']['feature_version']}, {len(mch['feature_names'])} feats) "
             f"vs baseline {bl.name} (feature {mbl['versions']['feature_version']}, {len(mbl['feature_names'])} feats)",
             f"same labels {mch['versions']['label_version']}=={mbl['versions']['label_version']}, split {mch['versions']['split_version']}=={mbl['versions']['split_version']}", "",
             "## Stage A — conditional directional information (ATR-decile top/bottom 10%)",
             f"ConditionalTargetLift    challenger {a_ch['conditional_target_lift']:.4f} (worst fold {a_ch['ctl_worst_fold']:.3f}) | baseline {a_bl['conditional_target_lift']:.4f} (worst {a_bl['ctl_worst_fold']:.3f}) | Δ {a_ch['conditional_target_lift'] - a_bl['conditional_target_lift']:+.4f}",
             f"ConditionalStopReduction challenger {a_ch['conditional_stop_reduction']:.4f} (worst fold {a_ch['csr_worst_fold']:.3f}) | baseline {a_bl['conditional_stop_reduction']:.4f} (worst {a_bl['csr_worst_fold']:.3f}) | Δ {a_ch['conditional_stop_reduction'] - a_bl['conditional_stop_reduction']:+.4f}",
             f"by ATR decile CTL  ch {a_ch['ctl_by_decile']}", f"                   bl {a_bl['ctl_by_decile']}",
             f"by ATR decile CSR  ch {a_ch['csr_by_decile']}", f"                   bl {a_bl['csr_by_decile']}"]
    passed_a = (a_ch["conditional_target_lift"] > a_bl["conditional_target_lift"]) or (a_ch["conditional_stop_reduction"] < a_bl["conditional_stop_reduction"])
    strict_a = (a_ch["conditional_target_lift"] >= a_bl["conditional_target_lift"]) and (a_ch["conditional_stop_reduction"] <= a_bl["conditional_stop_reduction"])
    lines += [f"Stage A: {'PASS (at least one improved)' if passed_a else 'FAIL — feature_version eliminated'}; both-improved={strict_a}", ""]
    metrics = {"challenger": ch.name, "baseline": bl.name, "stage_a": {"challenger": a_ch, "baseline": a_bl, "pass": passed_a, "both_improved": strict_a}}
    if passed_a:
        b_ch, agg_ch, mkt = stage_b(dch, cfg, contract)
        b_bl, agg_bl, _ = stage_b(dbl, cfg, contract)
        boot = pm.block_bootstrap(agg_ch, mkt, agg_bl, int(bcfg["block_length_main"]), int(bcfg["n_resamples"]), float(bcfg["ci"]))
        t = pd.DataFrame({"challenger": b_ch["point"], "baseline": b_bl["point"]}).T[
            ["n", "coverage", "target_rate", "target_lift", "stop_rate", "stop_ratio", "timeout_rate", "median_mfe", "median_mae", "mean_ret10", "mean_net10"]]
        t["worst_fold_lift"] = [b_ch["folds"]["worst_fold_lift"], b_bl["folds"]["worst_fold_lift"]]
        t["pos_fold_ratio"] = [b_ch["folds"]["positive_fold_ratio"], b_bl["folds"]["positive_fold_ratio"]]
        lines += ["## Stage B — policy_baseline_v1 harness (gate 95/20, vn_diff, K=5)", t.round(4).to_string(), "",
                  f"challenger fold lifts {b_ch['folds']['fold_lifts']} stop ratios {b_ch['folds']['fold_stop_ratios']}",
                  f"paired block bootstrap vs baseline (block {bcfg['block_length_main']}): d_net10 {boot['d_net10']['mean']:+.4f} CI [{boot['d_net10']['lo']:+.4f},{boot['d_net10']['hi']:+.4f}] | "
                  f"d_target_rate {boot['d_target_rate']['mean']:+.4f} CI [{boot['d_target_rate']['lo']:+.4f},{boot['d_target_rate']['hi']:+.4f}] | "
                  f"d_stop_rate {boot['d_stop_rate']['mean']:+.4f} CI [{boot['d_stop_rate']['lo']:+.4f},{boot['d_stop_rate']['hi']:+.4f}]",
                  f"challenger CI: target_lift [{boot['target_lift']['lo']:.3f},{boot['target_lift']['hi']:.3f}] stop_ratio [{boot['stop_ratio']['lo']:.3f},{boot['stop_ratio']['hi']:.3f}] net10 [{boot['net10']['lo']:+.4f},{boot['net10']['hi']:+.4f}]",
                  "promotion check (challenger @5): " + json.dumps(b_ch["promotion"], ensure_ascii=False), ""]
        metrics["stage_b"] = {"challenger": b_ch, "baseline": b_bl, "bootstrap": boot}
    out = ch / "challenger"; out.mkdir(exist_ok=True)
    report = "\n".join(lines)
    (out / "report.md").write_text(report, encoding="utf-8")
    (out / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
