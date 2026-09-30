"""Challenger 比較（B checkpoint 2）：conditional direction、multiclass competing-risk、波動度中性化 Gate。

    python -m scripts.mlentry_challenger_report [--k 5]

所有選股都與同日 ATR Top-K 並列（hard benchmark），並報 fold 穩定性。
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
from app.mlentry.evaluation import model_metrics as mm  # noqa: E402
from app.mlentry.evaluation import volatility_control as vc  # noqa: E402
from app.mlentry.models import oof  # noqa: E402
from app.mlentry.recommendation.gate import daily_pct  # noqa: E402


def vol_neutral_pct(df: pd.DataFrame, col: str, q: int = 10) -> pd.Series:
    dec = vc.daily_decile(df, "atr_pct", q)
    return df.groupby([df["signal_date"], dec])[col].rank(pct=True, method="average")


def topk_table(df: pd.DataFrame, scores: dict[str, pd.Series], k: int) -> pd.DataFrame:
    sels = {"ATR Top-K": vc.daily_topk(df, "atr_pct", k)}
    for name, s in scores.items():
        d = df.assign(_s=s.to_numpy())
        sels[name] = vc.daily_topk(d, "_s", k)
    return vc.benchmark_table(df, sels)


def gate_grid(df: pd.DataFrame, a_col: str, r_col: str, n_days: int) -> pd.DataFrame:
    base_t, base_s = df["target"].mean(), df["stop"].mean()
    rows = []
    for a, r in itertools.product((0.80, 0.85, 0.90, 0.95), (0.20, 0.30, 0.40, 0.50, 0.60)):
        ok = (df[a_col] >= a) & (df[r_col] <= r)
        sel = df.loc[ok]
        if len(sel) < 200:
            continue
        per_day = sel.groupby("signal_date").size()
        st = vc.selection_stats(sel, base_t, base_s)
        lifts = [(g.loc[ok.loc[g.index], "target"].mean() / g["target"].mean()) if ok.loc[g.index].any() else np.nan
                 for _, g in df.groupby("fold")]
        rows.append({"alpha>=": a, "risk<=": r, "coverage": len(per_day) / n_days,
                     "cand/day": float(per_day.median()), **{k: st[k] for k in ("n", "target_rate", "target_lift", "stop_rate", "stop_ratio", "timeout_rate", "median_mfe", "median_mae", "mean_ret10")},
                     "worst_fold_lift": float(np.nanmin(lifts)), "pos_folds": float(np.mean(np.array(lifts) > 1))})
    return pd.DataFrame(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--k", type=int, default=5); ap.add_argument("--dataset", default=None)
    args = ap.parse_args(argv)
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
    ds_dir = Path(args.dataset) if args.dataset else api.latest_dataset_dir()
    vec = pq.read_table(str(ds_dir / "oof" / "prediction_vector.parquet")).to_pandas()
    dev = api.load_development(ds_dir, feature_columns=["atr_pct"],
                               outcome_columns=["event_type", "target_hit_10d", "stop_hit_10d", "return_10d", "mfe_10d", "mae_10d"])
    df = vec.merge(dev.features[["sample_id", "atr_pct"]], on="sample_id").merge(dev.outcomes.drop(columns=["signal_date"]), on="sample_id")
    df = df[df["target_hit_10d"].notna()].reset_index(drop=True)
    df["target"] = df["target_hit_10d"].astype(float); df["stop"] = df["stop_hit_10d"].astype(float)
    df["atr_pct"] = df["atr_pct"].fillna(df["atr_pct"].median())
    n_days = df["signal_date"].nunique()
    lines = [f"# Challenger report ({ds_dir.name}) rows={len(df):,} days={n_days} base target={df.target.mean():.4f} stop={df.stop.mean():.4f}", ""]

    # --- direction model ---
    dpred, _ = oof.load_oof(ds_dir, "direction_10d", "lgbm")
    df = df.merge(dpred[["sample_id", "pred"]].rename(columns={"pred": "p_dir"}), on="sample_id", how="left")
    for m in ("prevalence", "logreg", "lgbm"):
        p, _ = oof.load_oof(ds_dir, "direction_10d", m)
        bf = mm.by_fold(p, "binary")
        lines.append(f"direction_10d/{m}: AUC mean {bf.auc.mean():.4f} worst {bf.auc.min():.4f} | top-decile TARGET share {bf.top_decile_lift.mean():.2f}x | decile monotonic {bf.decile_monotonic_pairs.mean():.2f}")
    resolved = df[df["event_type"].isin([1, 2])]
    lines.append(f"direction_10d resolved rows={len(resolved):,} TARGET share={resolved.target.mean():.4f}")
    ct = vc.atr_conditional_lift(df, "p_dir", "target", top=True); cs = vc.atr_conditional_lift(df, "p_dir", "stop", top=False)
    lines += ["", "## direction score, ATR-conditional (top 10% within ATR decile → target; bottom 10% → stop)",
              "target:", ct.round(4).to_string(index=False), "stop (bottom 10% of p_dir):", cs.round(4).to_string(index=False)]
    cs2 = vc.atr_conditional_lift(df, "p_dir", "stop", top=True)
    lines += ["stop rate of TOP 10% p_dir within ATR decile (want <1):", cs2[["atr_decile", "base_rate", "selected_rate", "ratio", "worst_fold_ratio"]].round(4).to_string(index=False), ""]

    # --- multiclass ---
    mc, _ = oof.load_oof(ds_dir, "event_10d", "lgbm")
    mc = mc.rename(columns={"pred_0": "mc_t", "pred_1": "mc_s", "pred_2": "mc_o"})
    df = df.merge(mc[["sample_id", "mc_t", "mc_s", "mc_o"]], on="sample_id", how="left")
    a_t = mm.binary_metrics(df.target.to_numpy(), df.mc_t.to_numpy(), df.w.to_numpy() if "w" in df else None)
    a_s = mm.binary_metrics(df.stop.to_numpy(), df.mc_s.to_numpy())
    b_t = mm.binary_metrics(df.target.to_numpy(), df.p_target_10d.to_numpy()); b_s = mm.binary_metrics(df.stop.to_numpy(), df.p_stop_10d.to_numpy())
    lines += ["## multiclass competing-risk vs independent heads (pooled OOF)",
              f"P(TARGET): mc AUC {a_t['auc']:.4f} vs binary {b_t['auc']:.4f} | top-decile lift {a_t['top_decile_lift']:.2f} vs {b_t['top_decile_lift']:.2f}",
              f"P(STOP):   mc AUC {a_s['auc']:.4f} vs binary {b_s['auc']:.4f}",
              f"corr(mc_t, mc_s) spearman {df.mc_t.corr(df.mc_s, method='spearman'):.3f}; corr(mc_t/(mc_t+mc_s), p_dir) {(df.mc_t/(df.mc_t+df.mc_s)).corr(df.p_dir, method='spearman'):.3f}", ""]

    # --- Top-K comparison ---
    df["p_t_vn"] = vol_neutral_pct(df, "p_target_10d"); df["p_s_vn"] = vol_neutral_pct(df, "p_stop_10d")
    df["dir_vn"] = vol_neutral_pct(df, "p_dir")
    df["pct_t"] = daily_pct(df, "p_target_10d"); df["pct_s"] = daily_pct(df, "p_stop_10d")
    df["atr_dec"] = vc.daily_decile(df, "atr_pct")
    scores = {
        "A p_target": df.p_target_10d,
        "B p_t - p_s": df.p_target_10d - df.p_stop_10d,
        "C pct_t - pct_s": df.pct_t - df.pct_s,
        "dir p_dir": df.p_dir,
        "dir | ATR (within-decile pct)": df.dir_vn,
        "mc P_t - P_s": df.mc_t - df.mc_s,
        "mc P_t": df.mc_t,
        "mc P_t/(P_t+P_s)": df.mc_t / (df.mc_t + df.mc_s),
        "vn: p_t_vn - p_s_vn": df.p_t_vn - df.p_s_vn,
        "hi-vol half ∧ dir": df.p_dir.where(df.atr_dec >= 5, -1),
        "hi-vol half ∧ mc P_t-P_s": (df.mc_t - df.mc_s).where(df.atr_dec >= 5, -9),
        "top-vol 30% ∧ dir": df.p_dir.where(df.atr_dec >= 7, -1),
    }
    t = topk_table(df, scores, args.k)
    cols = ["n", "target_rate", "target_lift", "stop_rate", "stop_ratio", "timeout_rate", "median_mfe", "median_mae", "mean_mae", "mean_ret10",
            "worst_fold_lift", "positive_fold_ratio", "d_target_rate_vs_atr", "d_stop_rate_vs_atr", "d_mean_ret10_vs_atr", "d_mean_mae_vs_atr"]
    lines += [f"## Daily Top-{args.k} vs ATR Top-{args.k}", t[cols].round(4).to_string(), ""]

    # --- volatility-neutral joint gate grids ---
    for name, a_col, r_col in (("vol-neutral gate: p_t_vn >= a, p_s_vn <= r", "p_t_vn", "p_s_vn"),
                               ("direction gate: dir_vn >= a, p_s_vn <= r", "dir_vn", "p_s_vn"),
                               ("raw-vol gate: pct_t >= a, dir_vn >= r?? (use dir as risk proxy: 1-dir_vn <= r)", "pct_t", "one_minus_dir_vn")):
        if r_col == "one_minus_dir_vn":
            df[r_col] = 1 - df.dir_vn
        g = gate_grid(df, a_col, r_col, n_days)
        lines += [f"## {name}", g.round(4).to_string(index=False) if len(g) else "(no cell with n>=200)", ""]
    report = "\n".join(lines)
    out = ds_dir / "policy"; out.mkdir(exist_ok=True)
    (out / "challenger_report.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
