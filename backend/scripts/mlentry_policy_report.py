"""B9 Recommendation Evaluation：policy 對 Market baseline / ATR Top-K 的正式報告（只用 dev OOF）。

    python -m scripts.mlentry_policy_report [--policy policy_baseline_v1]

輸出 data/mlentry/<ds>/policy/<policy_name>/{report.md, metrics.json, per_row.parquet, per_day.parquet}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.mlentry.config import load_yaml  # noqa: E402
from app.mlentry.datasets import api  # noqa: E402
from app.mlentry.datasets.store import code_commit  # noqa: E402
from app.mlentry.evaluation import policy_metrics as pm  # noqa: E402
from app.mlentry.evaluation import volatility_control as vc  # noqa: E402
from app.mlentry.recommendation.policy import apply_policy, load_policy  # noqa: E402

KS = (1, 3, 5)


def load_frame(ds_dir: Path) -> pd.DataFrame:
    vec = pq.read_table(str(ds_dir / "oof" / "prediction_vector.parquet")).to_pandas()
    dev = api.load_development(ds_dir, feature_columns=["atr_pct"],
                               outcome_columns=["event_type", "target_hit_10d", "stop_hit_10d", "return_10d", "mfe_10d", "mae_10d",
                                                "entry_executable", "matured"])
    df = vec.merge(dev.features[["sample_id", "atr_pct"]], on="sample_id").merge(dev.outcomes.drop(columns=["signal_date"]), on="sample_id")
    # policy 對全 U_t 列算（含未成交列），評估只用成熟且可成交列
    df["target"] = df["target_hit_10d"].astype(float); df["stop"] = df["stop_hit_10d"].astype(float)
    return df.reset_index(drop=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", default="policy_baseline_v1"); ap.add_argument("--dataset", default=None)
    args = ap.parse_args(argv)
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
    ds_dir = Path(args.dataset) if args.dataset else api.latest_dataset_dir()
    cfg = load_policy(args.policy); contract = load_yaml("promotion")
    df = load_frame(ds_dir)
    rows, day = apply_policy(df, cfg)
    df = pd.concat([df, rows], axis=1)
    ev = df[df["target"].notna() & (df["matured"] == 1)].copy()          # 評估列
    out = ds_dir / "policy" / cfg.name; out.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(df[["sample_id", "stock_id", "signal_date", "fold", "p_target_vn", "p_stop_vn", "gate_pass",
                                            "gate_failure_reason", "recommendation_score", "rank", "recommended"]], preserve_index=False),
                   out / "per_row.parquet", compression="zstd")
    pq.write_table(pa.Table.from_pandas(day, preserve_index=False), out / "per_day.parquet")

    market = pm.daily_aggregates(ev, pd.Series(True, index=ev.index), cfg.cost_rt)
    bcfg = contract["bootstrap"]
    metrics = {"policy": cfg.name, "policy_version": cfg.version, "dataset_version": ds_dir.name, "code_commit": code_commit(),
               "cost_rt": cfg.cost_rt, "market": pm._rates(market.to_numpy()), "at_k": {}, "sensitivity": {}}
    lines = [f"# B9 report — {cfg.name} ({cfg.version}) on {ds_dir.name}",
             f"eval rows={len(ev):,} days={len(market)} | market target={metrics['market']['target_rate']:.4f} stop={metrics['market']['stop_rate']:.4f} "
             f"ret10={metrics['market']['mean_ret10']:+.4f} net10={metrics['market']['mean_net10']:+.4f} (cost_rt={cfg.cost_rt})", ""]
    for k in KS:
        pol = ev["recommended"] & (ev["rank"] <= k)
        atr = vc.daily_topk(ev, "atr_pct", k)
        aggs = {"policy": pm.daily_aggregates(ev, pol, cfg.cost_rt), "atr_topk": pm.daily_aggregates(ev, atr, cfg.cost_rt)}
        pe = {n: pm.point_estimates(a, market, ev.loc[pol if n == "policy" else atr]) for n, a in aggs.items()}
        fs = {n: pm.fold_stats(ev, pol if n == "policy" else atr) for n in aggs}
        boot = {b: pm.block_bootstrap(aggs["policy"], market, aggs["atr_topk"], b, int(bcfg["n_resamples"]), float(bcfg["ci"]))
                for b in bcfg["block_lengths_robustness"]}
        metrics["at_k"][k] = {"point": pe, "folds": fs, "bootstrap": boot}
        t = pd.DataFrame(pe).T[["n", "coverage", "rec_per_day_mean", "target_rate", "target_lift", "stop_rate", "stop_ratio", "timeout_rate",
                                "mean_mfe", "median_mfe", "mean_mae", "median_mae", "mean_ret10", "mean_net10"]]
        t.loc["market"] = {**{c: metrics["market"].get(c, float("nan")) for c in t.columns}, "target_lift": 1.0, "stop_ratio": 1.0, "n": len(ev),
                           "coverage": 1.0, "rec_per_day_mean": len(ev) / len(market)}
        t["worst_fold_lift"] = [fs.get(n, {}).get("worst_fold_lift", float("nan")) for n in t.index]
        t["pos_fold_ratio"] = [fs.get(n, {}).get("positive_fold_ratio", float("nan")) for n in t.index]
        lines += [f"## @K={k}", t.round(4).to_string(), ""]
        lines.append(f"policy fold lifts {fs['policy']['fold_lifts']} stop ratios {fs['policy']['fold_stop_ratios']}")
        mb = boot[int(bcfg["block_length_main"])]
        lines.append(f"block bootstrap (main block={bcfg['block_length_main']}, n={bcfg['n_resamples']}, CI {bcfg['ci']}):")
        for key in ("net10", "target_lift", "stop_ratio", "d_net10", "d_target_rate", "d_stop_rate"):
            lines.append(f"  {key:14s} mean {mb[key]['mean']:+.4f}  CI [{mb[key]['lo']:+.4f}, {mb[key]['hi']:+.4f}]")
        lines.append("robustness (net10 CI lo/hi by block): " + ", ".join(f"{b}D [{boot[b]['net10']['lo']:+.4f},{boot[b]['net10']['hi']:+.4f}]" for b in boot))
        lines.append("robustness (target_lift CI lo by block): " + ", ".join(f"{b}D {boot[b]['target_lift']['lo']:.3f}" for b in boot))
        lines.append("robustness (stop_ratio CI hi by block): " + ", ".join(f"{b}D {boot[b]['stop_ratio']['hi']:.3f}" for b in boot))
        lines.append("")

    # coverage / no-trade sensitivity（本 policy）
    sens = {"coverage": float((day["recommendation_count"] > 0).mean()), "no_trade_pct": float(day["no_trade"].mean()),
            "median_candidates_per_day": float(day["qualified_count"].median()), "median_recommendations_per_day": float(day["recommendation_count"].median()),
            "qualified_count_quantiles": day["qualified_count"].quantile([0.05, 0.25, 0.5, 0.75, 0.95]).round(1).to_dict()}
    metrics["sensitivity"] = sens
    lines += ["## Coverage / NO_TRADE sensitivity (all dev days, incl. non-evaluable rows in gating)", json.dumps(sens, ensure_ascii=False, indent=2, default=str), ""]
    chk = pm.promotion_check(metrics["at_k"][5]["point"]["policy"], metrics["at_k"][5]["folds"]["policy"], contract)
    metrics["promotion_check"] = chk
    lines += ["## Promotion contract check (@5)", json.dumps(chk, ensure_ascii=False, indent=2), ""]
    report = "\n".join(lines)
    (out / "report.md").write_text(report, encoding="utf-8")
    (out / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
