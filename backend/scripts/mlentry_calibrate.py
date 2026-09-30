"""B4 Calibration apply + B5 Horizon monotonic projection（只用 OOF）。

    python -m scripts.mlentry_calibrate [--model lgbm]

輸出 data/mlentry/<ds>/oof/prediction_vector.parquet：
  sample_id, stock_id, signal_date, fold,
  p_target_{3,5,10}d_raw / p_stop_*_raw / p_executable_raw / pred_mfe_*,
  p_target_*d / p_stop_*d（calibrated → horizon-projected）, p_executable
與 oof/calibration.json（每任務選定方法、逐 fold 比較表、CalibrationRecord）。
第一個 fold 沒有前置 OOF 可 fit → 該 fold 的校準值 = raw（記錄在 json）。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.mlentry.datasets import api  # noqa: E402
from app.mlentry.models import calibration as cal  # noqa: E402
from app.mlentry.models import oof  # noqa: E402

HORIZONS = (3, 5, 10)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="lgbm")
    ap.add_argument("--dataset", default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    ds_dir = Path(args.dataset) if args.dataset else api.latest_dataset_dir()
    vec: pd.DataFrame | None = None
    report: dict = {"model": args.model, "tasks": {}}

    def merge(df: pd.DataFrame):
        nonlocal vec
        vec = df if vec is None else vec.merge(df, on="sample_id", how="outer")

    for kind in ("target", "stop"):
        for h in HORIZONS:
            task = f"{kind}_{h}d"
            preds, meta = oof.load_oof(ds_dir, task, args.model)
            table, out, recs = cal.compare_methods_by_fold(preds, task, f"{task}_{args.model}")
            method = cal.choose_method(table)
            calibrated = out[f"pred_{method}"].fillna(out["pred"]) if method != "none" else out["pred"]
            logging.info("%s: method=%s", task, method)
            report["tasks"][task] = {
                "method": method,
                "by_fold": table.round(5).to_dict(orient="records"),
                "records": [r.__dict__ for r in recs if r.method == method],
            }
            base = out[["sample_id", "stock_id", "signal_date", "fold"]] if vec is None else out[["sample_id"]]
            df = base.assign(**{f"p_{task}_raw": out["pred"].astype("float32"),
                                f"p_{task}_cal": calibrated.astype("float32")})
            merge(df)

    # B5：沿 horizon 軸單調投影（target 與 stop 各自）
    for kind in ("target", "stop"):
        cols = {h: vec[f"p_{kind}_{h}d_cal"].to_numpy() for h in HORIZONS}
        proj = cal.project_monotone_horizons(cols)
        viol = float(np.mean((cols[3] > cols[5]) | (cols[5] > cols[10])))
        for h in HORIZONS:
            vec[f"p_{kind}_{h}d"] = proj[h]
        report[f"{kind}_monotone_violation_rate_before"] = viol
        logging.info("%s: horizon violations before projection %.3f", kind, viol)

    for task, col in (("execution", "p_blocked"), ("mfe_3d", "pred_mfe_3d"), ("mfe_5d", "pred_mfe_5d"), ("mfe_10d", "pred_mfe_10d")):
        d = ds_dir / "oof" / f"{task}__{args.model}"
        if (d / "predictions.parquet").exists():
            p, _ = oof.load_oof(ds_dir, task, args.model)
            merge(p[["sample_id"]].assign(**{col: p["pred"].astype("float32")}))
    if "p_blocked" in vec.columns:
        vec["p_executable"] = (1 - vec["p_blocked"]).astype("float32")

    out_path = ds_dir / "oof" / "prediction_vector.parquet"
    pq.write_table(pa.Table.from_pandas(vec, preserve_index=False), out_path, compression="zstd")
    (ds_dir / "oof" / "calibration.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                                                    encoding="utf-8")
    print(f"prediction vector: {out_path}  rows={len(vec):,}  cols={list(vec.columns)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
