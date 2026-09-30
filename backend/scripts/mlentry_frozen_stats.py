"""Frozen 描述統計轉存（/app/level1 體檢頁收斂對照表用）。唯讀：不重跑 OOF、不重算 policy、不寫回 champion。

    python -m scripts.mlentry_frozen_stats [--dataset DIR]

來源：data/mlentry/<ds>/policy/<policy>/metrics.json（B9 報告）＋ OOF 成熟可評估列的 ECE（p_target_10d vs target_hit_10d）。
輸出：同目錄 frozen_stats.json。policy 必須與 champion 相同。
未指定 --dataset 時預設用 champion 的 dataset_version 目錄（不存在才退回 latest_dataset_dir）。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.mlentry.datasets import api  # noqa: E402
from app.mlentry.evaluation.model_metrics import ece  # noqa: E402
from app.mlentry.registry.versions import load_champion  # noqa: E402


def build_frozen_stats(metrics: dict, ece_value: float | None, expected_policy: str) -> dict:
    if metrics.get("policy") != expected_policy:
        raise ValueError(f"metrics.json policy={metrics.get('policy')} != champion policy={expected_policy}")
    at = metrics["at_k"]; sens = metrics["sensitivity"]; q = sens.get("qualified_count_quantiles", {})
    p5 = at["5"]["point"]["policy"]
    return {"policy_name": metrics["policy"], "dataset_version": metrics.get("dataset_version"),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "lift_at_1": at["1"]["point"]["policy"]["target_lift"], "lift_at_3": at["3"]["point"]["policy"]["target_lift"],
            "candidates_median": sens["median_candidates_per_day"], "candidates_p05": q.get("0.05"), "candidates_p95": q.get("0.95"),
            "no_trade_rate": sens["no_trade_pct"], "ece_target_10d": ece_value,
            "median_mfe_10d": p5.get("median_mfe"), "median_mae_10d": p5.get("median_mae")}


def _oof_ece(ds_dir: Path) -> float:
    from scripts.mlentry_policy_report import load_frame
    df = load_frame(ds_dir)
    ev = df[df["target"].notna() & (df["matured"] == 1)]
    return float(ece(ev["target"].to_numpy(dtype=float), ev["p_target_10d"].to_numpy(dtype=float)))


def _default_dataset_dir(champ) -> Path:
    latest = Path(api.latest_dataset_dir())
    cand = latest.parent / str(champ.dataset_version)
    return cand if cand.exists() else latest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--dataset", default=None)
    args = ap.parse_args(argv)
    champ = load_champion()
    if champ is None:
        print("no champion; abort"); return 1
    ds_dir = Path(args.dataset) if args.dataset else _default_dataset_dir(champ)
    pdir = ds_dir / "policy" / champ.policy_name
    metrics = json.loads((pdir / "metrics.json").read_text(encoding="utf-8"))
    out = build_frozen_stats(metrics, _oof_ece(ds_dir), champ.policy_name)
    (pdir / "frozen_stats.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
