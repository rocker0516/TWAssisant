"""Prospective Shadow Observation 報告（唯讀；不改模型、不改 policy）。

    python -m scripts.mlentry_observe

第一階段看 production contract 是否穩定：
  scheduled runs 零漏跑、SYSTEM_NO_TRADE 只在合理情況、feature drift 誤報、prediction 分布、
  candidate count 與 OOF 分布一致、immutable ledger 完整、outcome maturity 正確。
第二階段（20 / 60 個成熟日）：Prospective vs Frozen OOF 對照表（只觀察，不做 promotion 決策）。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.mlentry.config import load_yaml  # noqa: E402
from app.mlentry.data.calendar import load_calendar  # noqa: E402
from app.mlentry.monitoring import performance  # noqa: E402
from app.mlentry.registry.versions import load_champion  # noqa: E402
from app.storage.database import session_scope  # noqa: E402


def main() -> int:
    pd.set_option("display.width", 200)
    con = sqlite3.connect(str(get_settings().db_path))
    cal = load_calendar(con)
    stack = load_champion()
    runs = pd.read_sql_query("SELECT * FROM mlentry_runs ORDER BY signal_date, run_id", con)
    if runs.empty:
        print("尚無 run。"); return 0
    runs["signal_date"] = runs["signal_date"].astype(str)
    last_per_day = runs.groupby("signal_date").tail(1).set_index("signal_date")
    first_day = last_per_day.index.min()
    expected = [d for d in cal.dates if d >= first_day]
    missing = [d for d in expected if d not in last_per_day.index]
    print(f"# Observation ({stack.model_version if stack else '—'} / {stack.policy_name if stack else '—'}, status {stack.model_status if stack else '—'})")
    print(f"observation start {first_day} | calendar days since {len(expected)} | run days {len(last_per_day)} | MISSING {len(missing)} {missing[-5:]}")
    print("\n## run status (last run per day)")
    print(last_per_day["status"].value_counts().to_string())
    print("no_trade reasons:", last_per_day["no_trade_reason"].value_counts(dropna=True).to_dict())
    print("reruns per day >1:", int((runs.groupby("signal_date").size() > 1).sum()))

    # 健康 gate 明細：drift 誤報觀察
    fh = [json.loads(h).get("feature_health", {}) for h in last_per_day["health_json"].fillna("{}")]
    nd = pd.Series([x.get("n_drifted", np.nan) for x in fh], index=last_per_day.index)
    print("\n## feature health: n_drifted per day (max/mean)", nd.max(), round(float(nd.mean()), 2))
    drift_days = last_per_day.index[(nd > 0).to_numpy()]
    for d in drift_days[-5:]:
        i = list(last_per_day.index).index(d)
        print(f"  {d}: {fh[i].get('drifted')} {fh[i].get('out_of_range_day_level')}")

    # candidate count vs OOF 分布
    sens = {}
    try:
        ds = Path(stack.serving_root).parent / stack.dataset_version if stack else None
        mp = ds / "policy" / stack.policy_name / "metrics.json"
        sens = json.loads(mp.read_text(encoding="utf-8"))["sensitivity"]
    except Exception:
        pass
    q = last_per_day["qualified_count"].quantile([0.05, 0.25, 0.5, 0.75, 0.95]).round(1).to_dict()
    print("\n## qualified_count/day prospective quantiles:", q)
    if sens:
        print("   OOF (B9 sensitivity):", sens.get("qualified_count_quantiles"), "| no_trade% OOF", round(sens.get("no_trade_pct", 0) * 100, 2),
              "vs prospective", round(float(last_per_day["no_trade"].mean() * 100), 2))

    # prediction 分布 vs 參考
    preds = pd.read_sql_query("SELECT run_id, signal_date, p_target_10d_raw, p_stop_10d_raw, recommended, matured_at, label_available_date, entry_status FROM mlentry_predictions", con)
    preds = preds[preds["run_id"].isin(last_per_day["run_id"])]
    ref = {}
    try:
        ref = json.loads((stack.dir / "feature_reference.json").read_text(encoding="utf-8"))["predictions"]
    except Exception:
        pass
    for t, col in (("target_10d", "p_target_10d_raw"), ("stop_10d", "p_stop_10d_raw")):
        daily = preds.groupby("signal_date")[col].mean()
        r = ref.get(t, {})
        print(f"## {col} daily mean: min {daily.min():.4f} mean {daily.mean():.4f} max {daily.max():.4f} | OOF ref mean {r.get('mean', float('nan')):.4f} std {r.get('std', float('nan')):.4f}")

    # ledger 完整性與成熟正確性
    cnt = preds.groupby("run_id").size()
    mism = [(rid, int(cnt.get(rid, 0)), int(u)) for rid, u in zip(last_per_day["run_id"], last_per_day["universe_count"]) if cnt.get(rid, 0) != u]
    print("\n## ledger completeness: runs with rows != universe_count:", mism[:5] or "none")
    today = cal.dates[-1]
    due = preds[(preds["label_available_date"].notna()) & (preds["label_available_date"].astype(str) <= today)]
    print(f"## maturity: rows due (label_available_date <= {today}) {len(due)} | matured {int(due['matured_at'].notna().sum())} | overdue-unmatured {int(due['matured_at'].isna().sum())}")
    print("   entry_status filled for past-entry rows:", int(preds.loc[preds['signal_date'].astype(str) < today, 'entry_status'].notna().sum()), "/", int((preds['signal_date'].astype(str) < today).sum()))

    # checkpoint 對照表
    with session_scope() as s:
        live = performance.rolling_live_metrics(performance.load_matured(s), windows=(20, 60, 120), k=5)
    fv = stack.frozen_validation if stack else {}
    print(f"\n## checkpoint: mature_days = {live['matured_days']} / 20 (observation) / 60 (judgement)")
    rows = []
    for w, item in live["windows"].items():
        rows.append({"window": f"{w}D", "days": item["days"], "n_rec": item["n_rec"], "lift@5": item.get("target_lift"), "stop_ratio@5": item.get("stop_ratio"),
                     "net10": item.get("mean_net10"), "median_mae": item.get("median_mae"), "ece": item.get("ece_target_10d")})
    rows.append({"window": "Frozen OOF", "days": None, "n_rec": None, "lift@5": fv.get("target_lift_at_5"), "stop_ratio@5": fv.get("stop_ratio_at_5"),
                 "net10": fv.get("mean_net10"), "median_mae": None, "ece": None})
    print(pd.DataFrame(rows).round(4).to_string(index=False))
    print("\n（只觀察：20D 統計力弱，不因此改模型；60D 才問 Lift>1？StopRatio<1？是否 ≈ OOF 的 1.23 / 0.75。）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
