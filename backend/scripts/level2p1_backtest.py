"""Level 2.1 市值加權頂分位組合回測（設計 §2）。

dev：THRESHOLD {0.7,0.8} × REBALANCE {10,20,40} × TOP_N {15,30} 全格子。
holdout：僅凍結參數（CAPTOP_FROZEN），需 --confirm-holdout，一次性。

歸因欄：主 KPI vs 加權指數；另附 vs 市值加權 U_t（解析解、零成本）——
分離「選股 alpha」與「core 貼指數誤差」。

用法（cwd=backend）：
  TWA_DATA_DIR=<data> python -m scripts.level2p1_backtest --segment dev
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from itertools import product
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.research.level2 import metrics as mt  # noqa: E402
from app.research.level2.capweight import (CAPTOP_VERSION,  # noqa: E402
                                           CapTopParams, plan_captop)
from app.research.level2.costs import CostModel  # noqa: E402
from app.research.level2.simulate import run_simulation  # noqa: E402
from scripts.level2_backtest import (INITIAL_CASH, SEGMENTS,  # noqa: E402
                                     _get_scores, _load_benchmark, _load_open,
                                     _load_snapshot, _log, _pct_rank)

# 凍結參數（dev 定案後由使用者核可寫入；holdout 只跑這一組）
CAPTOP_FROZEN: CapTopParams | None = None

GRID = {"threshold": (0.7, 0.8), "rebalance_every": (10, 20, 40),
        "top_n": (15, 30)}


def _load_caps(data_dir: Path, close: pd.DataFrame,
               mask: pd.DataFrame) -> pd.DataFrame:
    con = sqlite3.connect(data_dir / "twa.db")
    sh = pd.read_sql_query(
        "SELECT stock_id, issued_shares FROM company_profile "
        "WHERE issued_shares IS NOT NULL", con,
        index_col="stock_id")["issued_shares"]
    con.close()
    return close.mul(sh.reindex(close.columns), axis=1).where(mask)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--segment", choices=list(SEGMENTS), default="dev")
    ap.add_argument("--confirm-holdout", action="store_true")
    args = ap.parse_args()
    if args.segment == "holdout":
        if not args.confirm_holdout:
            raise SystemExit("holdout 一次性：請帶 --confirm-holdout。")
        if CAPTOP_FROZEN is None:
            raise SystemExit("CAPTOP_FROZEN 未定：dev 定案並經使用者核可後"
                             "才可跑 holdout（設計 §2）。")

    data_dir = Path(settings.data_dir)
    payload = _load_snapshot(data_dir)
    scores = _get_scores(payload, data_dir)
    close = payload["close"].astype("float64")
    mask = payload["universe"]
    open_ = _load_open(data_dir, close)
    bench = _load_benchmark(data_dir)
    caps = _load_caps(data_dir, close, mask)
    pct5 = _pct_rank(scores[5])

    lo, hi = SEGMENTS[args.segment]
    sim_dates = close.index[(close.index >= lo) & (close.index <= hi)]
    first_sig = pct5.loc[sim_dates].dropna(how="all").index.min()
    sim_dates = sim_dates[sim_dates >= first_sig]
    o, c = open_.loc[sim_dates], close.loc[sim_dates]
    p5 = pct5.loc[sim_dates]
    cp = caps.loc[sim_dates]
    b = bench.reindex(sim_dates).ffill()
    _log(f"{args.segment}: {sim_dates[0]} ~ {sim_dates[-1]}（{len(sim_dates)} 日）")

    # 市值加權 U_t（解析解，歸因基準）
    w = cp.div(cp.sum(axis=1), axis=0)
    cw_ret = (w.shift(1) * c.pct_change(fill_method=None)).sum(axis=1,
                                                               min_count=1)
    cw_nav = (1 + cw_ret.fillna(0)).cumprod() * INITIAL_CASH
    cw_total = float(cw_nav.iloc[-1] / cw_nav.iloc[0] - 1) * 100

    if args.segment == "dev":
        param_sets = [CapTopParams(threshold=t, rebalance_every=r, top_n=n)
                      for t, r, n in product(*GRID.values())]
    else:
        param_sets = [CAPTOP_FROZEN]

    out: dict = {}
    for params in param_sets:
        name = (f"T{params.threshold}_R{params.rebalance_every}"
                f"_N{params.top_n}")
        planner = (lambda st, nav, ref, sig, d, _p=params:
                   plan_captop(st, nav, ref, sig, cp.loc[d], _p))
        res = run_simulation(o, c, p5, None, params, INITIAL_CASH,
                             CostModel(), planner=planner)
        s = mt.summarize(res.nav, b, res.fills)
        s["params"] = params.__dict__
        s["excess_vs_capw_pct"] = round(s["return_pct"] - cw_total, 2)
        out[name] = s
        _log(f"  {name:<14} 超額 {s['excess_pct']:+8.2f}% (t={s['excess_t']:+.2f}) "
             f"vs市值加權U_t {s['excess_vs_capw_pct']:+7.2f}% "
             f"換手 {s['turnover_annual']:4.1f} 成本 {s['cost_drag_pct']:4.1f}pp "
             f"MDD {s['mdd_pct']}%")

    out["capweighted_ut"] = mt.summarize(cw_nav, b, pd.DataFrame())
    path = data_dir / f"level2p1_backtest_{args.segment}.json"
    path.write_text(json.dumps({
        "meta": {"generated_at": datetime.now().isoformat(timespec="seconds"),
                 "segment": args.segment, "version": CAPTOP_VERSION,
                 "db_max_date": payload["meta"]["db_max_date"],
                 "cost_model": CostModel().__dict__,
                 "shares_note": "issued_shares 現況快照（設計 §3 限制）"},
        "result": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    _log(f"已存 {path}")


if __name__ == "__main__":
    main()
