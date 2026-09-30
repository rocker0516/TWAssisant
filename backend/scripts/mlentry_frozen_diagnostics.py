# backend/scripts/mlentry_frozen_diagnostics.py
"""§18 唯讀 Frozen diagnostics 轉存（Spec B）。

    python -m scripts.mlentry_frozen_diagnostics [--dataset DIR] [--k 5]

只讀 champion dataset 的 dev OOF、policy per_row、dev outcomes／features、sector map、company_profile 股本；
只寫 policy/<policy>/diagnostics_frozen.json。不讀 holdout、不跑 policy grid、不寫回任何 artifact。
Regime 切點由 dev 資料分位算出並寫進輸出；全部標 diagnostic_only。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.mlentry.data import prices  # noqa: E402
from app.mlentry.datasets import api  # noqa: E402
from app.mlentry.datasets.store import DEFAULT_ROOT  # noqa: E402
from app.mlentry.evaluation.diagnostics_frozen import build_diagnostics  # noqa: E402
from app.mlentry.registry.versions import load_champion  # noqa: E402

REGIME_FEATURES = ["market_ret_20d", "market_volatility", "breadth_ma20"]


def _connect():
    return sqlite3.connect(str(get_settings().db_path))


def assemble_frame(ds_dir: Path, policy_name: str, con) -> pd.DataFrame:
    from scripts.mlentry_policy_report import load_frame
    df = load_frame(ds_dir)                                             # OOF + atr_pct + outcomes（含 target/stop/matured）
    df = df[df["target"].notna() & (df["matured"] == 1)].copy()          # 評估列
    per_row = pq.read_table(str(ds_dir / "policy" / policy_name / "per_row.parquet")).to_pandas()
    df = df.merge(per_row[["sample_id", "p_target_vn", "p_stop_vn", "gate_pass", "recommendation_score", "rank", "recommended"]],
                  on="sample_id", how="left")
    dev = api.load_development(ds_dir, feature_columns=REGIME_FEATURES, outcome_columns=["target_first_hit_day"])
    df = df.merge(dev.features[["sample_id", *REGIME_FEATURES]], on="sample_id", how="left")
    df = df.merge(dev.outcomes[["sample_id", "target_first_hit_day"]], on="sample_id", how="left")
    sector = prices.load_sector_map(con)
    df["sector_id"] = sector.reindex(df["stock_id"].astype(str)).to_numpy()
    shares = pd.read_sql_query("SELECT stock_id, issued_shares FROM company_profile", con)
    shares = pd.Series(pd.to_numeric(shares["issued_shares"], errors="coerce").to_numpy(), index=shares["stock_id"].astype(str)).replace(0, float("nan"))
    close = _close_lookup(con, df)
    df["mcap"] = close.to_numpy() * shares.reindex(df["stock_id"].astype(str)).to_numpy()
    return df


def _close_lookup(con, df: pd.DataFrame) -> pd.Series:
    """每列 (stock_id, signal_date) 的收盤；只查用到的日期範圍，不掃全表。"""
    lo, hi = str(df["signal_date"].min()), str(df["signal_date"].max())
    px = pd.read_sql_query("SELECT stock_id, date, close FROM daily_prices WHERE date >= ? AND date <= ?", con, params=(lo, hi))
    key = pd.MultiIndex.from_arrays([px["stock_id"].astype(str), px["date"].astype(str)])
    s = pd.Series(px["close"].to_numpy(dtype=float), index=key)
    want = pd.MultiIndex.from_arrays([df["stock_id"].astype(str), df["signal_date"].astype(str)])
    return s.reindex(want)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--dataset", default=None); ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args(argv)
    champ = load_champion()
    if champ is None:
        print("no champion; abort"); return 1
    ds_dir = Path(args.dataset) if args.dataset else Path(DEFAULT_ROOT) / champ.dataset_version
    pdir = ds_dir / "policy" / champ.policy_name
    metrics = json.loads((pdir / "metrics.json").read_text(encoding="utf-8"))
    if metrics.get("policy") != champ.policy_name:
        print(f"metrics.json policy={metrics.get('policy')} != champion policy={champ.policy_name}; abort"); return 1
    con = _connect()
    try:
        df = assemble_frame(ds_dir, champ.policy_name, con)
    finally:
        if con is not None:
            con.close()
    out = build_diagnostics(df, champ.policy_name, ds_dir.name, k=args.k)
    (pdir / "diagnostics_frozen.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    lk = out["lift_at_k"]
    print("Lift@K row-weighted:", {k: (v["row_weighted"]["target_lift"]) for k, v in lk.items()})
    print("timing:", {k: v for k, v in out["timing"].items() if k.startswith("p_target") or k == "median_time_to_target"})
    print("ic:", out["ranking"]["ic"], "ndcg:", out["ranking"]["ndcg_at_k"])
    for key in ("market", "volatility", "breadth", "mcap", "industry"):
        print(key, {g: (c["n"], c["lift_at_5"]) for g, c in out["regime"][key]["groups"].items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
