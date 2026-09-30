"""B6 volatility-controlled diagnostics + B7 Joint Gate grid + B8 Ranking comparison（只用 OOF prediction vector）。

    python -m scripts.mlentry_calibrate            # 先產 prediction_vector.parquet
    python -m scripts.mlentry_policy_grid [--k 5]

輸出 data/mlentry/<ds>/policy/{atr_conditional.csv, gate_grid.csv, ranking_compare.csv, report.md}
"""

from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.mlentry.datasets import api  # noqa: E402
from app.mlentry.evaluation import volatility_control as vc  # noqa: E402
from app.mlentry.recommendation.gate import GateConfig, apply_gate  # noqa: E402
from app.mlentry.recommendation.ranking import RankingConfig, rank_and_select  # noqa: E402

ALPHA_GRID = (0.80, 0.85, 0.90, 0.95)
RISK_GRID = (0.20, 0.30, 0.40, 0.50, 0.60)
STOP_LEVELS = (1.0, 0.8, 0.6)


def load_frame(ds_dir: Path) -> pd.DataFrame:
    vec = pq.read_table(str(ds_dir / "oof" / "prediction_vector.parquet")).to_pandas()
    dev = api.load_development(ds_dir, feature_columns=["atr_pct"],
                               outcome_columns=["event_type", "target_hit_10d", "stop_hit_10d", "return_10d",
                                                "mfe_10d", "mae_10d"])
    df = vec.merge(dev.features[["sample_id", "atr_pct"]], on="sample_id").merge(
        dev.outcomes[["sample_id", "event_type", "target_hit_10d", "stop_hit_10d", "return_10d", "mfe_10d", "mae_10d"]],
        on="sample_id")
    df = df[df["target_hit_10d"].notna()].reset_index(drop=True)      # 只評估可成交且成熟列
    df["target"] = df["target_hit_10d"].astype(float); df["stop"] = df["stop_hit_10d"].astype(float)
    df["atr_pct"] = df["atr_pct"].fillna(df["atr_pct"].median())
    return df


def fold_stats(df: pd.DataFrame, mask: pd.Series) -> dict:
    lifts = []
    for f, g in df.groupby("fold"):
        m = mask.loc[g.index]
        if m.sum() == 0:
            lifts.append(np.nan); continue
        lifts.append(g.loc[m, "target"].mean() / g["target"].mean())
    lifts = np.array(lifts, dtype=float)
    return {"fold_mean_lift": float(np.nanmean(lifts)), "worst_fold_lift": float(np.nanmin(lifts)),
            "positive_fold_ratio": float(np.mean(lifts > 1)), "n_folds": int(np.isfinite(lifts).sum())}


def gate_grid(df: pd.DataFrame, n_days: int, base_t: float, base_s: float) -> pd.DataFrame:
    rows = []
    for a, r in itertools.product(ALPHA_GRID, RISK_GRID):
        ok, _ = apply_gate(df, GateConfig(a, r))
        sel = df.loc[ok]
        per_day = sel.groupby("signal_date").size()
        st = vc.selection_stats(sel, base_t, base_s) if len(sel) else {}
        row = {"theta_alpha_pct": a, "theta_risk_pct": r, "coverage": len(per_day) / n_days,
               "cand_per_day_median": float(per_day.median()) if len(per_day) else 0.0, **st, **fold_stats(df, ok)}
        for lvl in STOP_LEVELS:
            row[f"pass_stop_{lvl:.1f}"] = bool(st) and st["stop_rate"] <= lvl * base_s
        rows.append(row)
    return pd.DataFrame(rows)


def select_policy(grid: pd.DataFrame, s_max_ratio: float, c_min: float, n_min: float, min_n: int) -> pd.Series | None:
    ok = grid[(grid["stop_ratio"] <= s_max_ratio) & (grid["coverage"] >= c_min)
              & (grid["cand_per_day_median"] >= n_min) & (grid["worst_fold_lift"] > 1) & (grid["n"] >= min_n)]
    return None if ok.empty else ok.sort_values("target_lift", ascending=False).iloc[0]


def ranking_compare(df: pd.DataFrame, gate: GateConfig | None, k: int, base_t: float, base_s: float) -> pd.DataFrame:
    ok = apply_gate(df, gate)[0] if gate else pd.Series(True, index=df.index)
    sels = {"ATR Top-K": vc.daily_topk(df, "atr_pct", k)}
    for m in ("A", "B", "C", "D"):
        sels[f"rank {m}" + (" (gated)" if gate else "")] = rank_and_select(df, ok, RankingConfig(m, k_max=k))["recommended"]
    return vc.benchmark_table(df, sels)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=None)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--min-n", type=int, default=2000)
    args = ap.parse_args(argv)
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
    ds_dir = Path(args.dataset) if args.dataset else api.latest_dataset_dir()
    out = ds_dir / "policy"; out.mkdir(exist_ok=True)
    df = load_frame(ds_dir)
    n_days = df["signal_date"].nunique()
    base_t, base_s = df["target"].mean(), df["stop"].mean()
    lines = [f"# Policy grid report ({ds_dir.name})", f"rows={len(df):,} days={n_days} base target={base_t:.4f} stop={base_s:.4f}", ""]

    # B6：ATR-controlled diagnostics
    ct = vc.atr_conditional_lift(df, "p_target_10d", "target", top=True)
    cs = vc.atr_conditional_lift(df, "p_stop_10d", "stop", top=False)
    ct.to_csv(out / "atr_conditional_target.csv", index=False); cs.to_csv(out / "atr_conditional_stop.csv", index=False)
    lines += ["## ATR-decile conditional (top/bottom 10% within same ATR decile)", "ConditionalTargetLift", ct.round(4).to_string(index=False),
              "", "ConditionalStopReduction", cs.round(4).to_string(index=False), ""]

    # B7：gate grid
    grid = gate_grid(df, n_days, base_t, base_s)
    grid.to_csv(out / "gate_grid.csv", index=False)
    cols = ["theta_alpha_pct", "theta_risk_pct", "coverage", "cand_per_day_median", "n", "target_rate", "target_lift",
            "stop_rate", "stop_ratio", "timeout_rate", "median_mfe", "median_mae", "mean_ret10", "worst_fold_lift",
            "positive_fold_ratio", "pass_stop_1.0", "pass_stop_0.8", "pass_stop_0.6"]
    lines += ["## Joint gate grid (percentile thresholds; stop_ratio = StopRate / base)", grid[cols].round(4).to_string(index=False), ""]

    # policy selection sensitivity
    lines.append("## Policy selection: max TargetLift s.t. StopRatio<=S, Coverage>=C, MedianCand/day>=N, WorstFoldLift>1")
    chosen = None
    for s_ratio in STOP_LEVELS:
        for c_min, n_min in ((0.9, 5), (0.7, 3), (0.5, 1)):
            p = select_policy(grid, s_ratio, c_min, n_min, args.min_n)
            if p is None:
                lines.append(f"S<={s_ratio} C>={c_min} N>={n_min}: none")
            else:
                lines.append(f"S<={s_ratio} C>={c_min} N>={n_min}: alpha>={p.theta_alpha_pct} risk<={p.theta_risk_pct} "
                             f"lift={p.target_lift:.2f} stop={p.stop_rate:.3f} cov={p.coverage:.2f} cand/day={p.cand_per_day_median:.0f} "
                             f"worst={p.worst_fold_lift:.2f} ret10={p.mean_ret10:+.4f}")
                if chosen is None and s_ratio == 0.8:
                    chosen = p
    lines.append("")

    # B8：ranking comparison
    rc0 = ranking_compare(df, None, args.k, base_t, base_s)
    lines += [f"## Ranking comparison WITHOUT gate (Top-{args.k}/day) vs ATR Top-K", rc0.round(4).to_string(), ""]
    rc0.to_csv(out / "ranking_compare_nogate.csv")
    if chosen is not None:
        g = GateConfig(float(chosen.theta_alpha_pct), float(chosen.theta_risk_pct))
        rc1 = ranking_compare(df, g, args.k, base_t, base_s)
        lines += [f"## Ranking comparison WITH gate {g} (Top-{args.k}/day)", rc1.round(4).to_string(), ""]
        rc1.to_csv(out / "ranking_compare_gated.csv")
    report = "\n".join(lines)
    (out / "report.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
