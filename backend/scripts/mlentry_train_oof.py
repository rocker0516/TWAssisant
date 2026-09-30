"""B2/B3：對 development 分區跑 purged walk-forward OOF，並印 model-level 評估（B6）。

    python -m scripts.mlentry_train_oof --tasks target_10d,stop_10d --models prevalence,logreg,lgbm
    python -m scripts.mlentry_train_oof --report            # 只讀既有 OOF 印報告

只讀 development；holdout 由 datasets.api.load_final_holdout 另走。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.mlentry.datasets import api  # noqa: E402
from app.mlentry.evaluation import model_metrics as mm  # noqa: E402
from app.mlentry.models import oof  # noqa: E402
from app.mlentry.models.estimators import MODEL_NAMES  # noqa: E402
from app.mlentry.models.tasks import all_tasks  # noqa: E402

BIN_COLS = ("auc", "pr_auc", "brier", "ece", "top5_lift", "top_decile_lift", "decile_monotonic_pairs")
REG_COLS = ("mae", "spearman", "decile_monotonic_pairs")


def report(ds_dir: Path, tasks: list[str], models: list[str]) -> None:
    pd.set_option("display.width", 200); pd.set_option("display.max_columns", 30)
    summary_rows = []
    for t in tasks:
        kind = all_tasks()[t].kind
        for m in models:
            d = ds_dir / "oof" / f"{t}__{m}"
            if not (d / "predictions.parquet").exists():
                continue
            preds, meta = oof.load_oof(ds_dir, t, m)
            if preds.empty:
                continue
            bf = mm.by_fold(preds, kind)
            cols = BIN_COLS if kind == "binary" else REG_COLS
            print(f"\n=== {t} / {m}  (day-weighted, n={len(preds):,}) ===")
            print(bf[[c for c in cols if c in bf.columns]].round(4).to_string())
            fs = mm.fold_summary(bf, cols)
            print(fs.round(4).to_string())
            if kind == "binary":
                pooled = mm.binary_metrics(preds["y"].to_numpy(), preds["pred"].to_numpy(), preds["w"].to_numpy())
                print("pooled decile event rate:", pooled["decile_rates"], " base", round(pooled["base_rate"], 4))
            row = {"task": t, "model": m, **{f"{c}_mean": fs.loc[c, "mean"] for c in cols if c in fs.index},
                   **{f"{c}_worst": fs.loc[c, "worst"] for c in cols if c in fs.index}}
            summary_rows.append(row)
    if summary_rows:
        s = pd.DataFrame(summary_rows)
        print("\n=== summary ===")
        print(s.round(4).to_string(index=False))
        (ds_dir / "oof" / "summary.json").write_text(s.to_json(orient="records", indent=2), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default="target_3d,target_5d,target_10d,stop_3d,stop_5d,stop_10d")
    ap.add_argument("--models", default=",".join(MODEL_NAMES))
    ap.add_argument("--dataset", default=None)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ds_dir = Path(args.dataset) if args.dataset else api.latest_dataset_dir()
    tasks = args.tasks.split(","); models = args.models.split(",")
    if not args.report:
        specs = all_tasks()
        need_cols = sorted({c for t in tasks for c in specs[t].outcome_columns})
        dev = api.load_development(ds_dir, outcome_columns=need_cols)
        names = dev.manifest["feature_names"]
        for t in tasks:
            for m in models:
                t0 = time.time()
                oof.run_oof(dev, specs[t], m, names, out_root=ds_dir)
                logging.info("%s/%s done in %.0fs", t, m, time.time() - t0)
    report(ds_dir, tasks, models)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
