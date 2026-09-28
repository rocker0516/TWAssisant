# backend/scripts/level1_targets_v3.py
"""Level 1 v3 Target 快取建置（設計 2026-09-28 §1）。

產出 data/level1_v3_targets.pkl：OHLCV+turnover 矩陣、U_t、Entry、fill、{N: {"net"}}。
用法：cd backend && .venv/Scripts/python.exe -m scripts.level1_targets_v3
"""

from __future__ import annotations

import pickle
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.research.level1 import prices as pr  # noqa: E402
from app.research.level1 import targets_v3 as t3  # noqa: E402
from app.research.level1 import universe as uv  # noqa: E402

_DB = Path(__file__).resolve().parents[1] / "data" / "twa.db"
_OUT = Path(__file__).resolve().parents[1] / "data" / "level1_v3_targets.pkl"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    con = sqlite3.connect(_DB)
    db_max_date = con.execute("SELECT max(date) FROM daily_prices").fetchone()[0]
    close, mask = uv.build_tradable_universe(con)           # 唯一入口
    mats = pr.load_price_matrices(con, close.index, close.columns)
    con.close()
    close = close.astype("float64")
    _log(f"矩陣 {close.shape[0]} 日 × {close.shape[1]} 檔（{close.index[0]} ~ {close.index[-1]}）")

    targets, entry, fill = t3.build_targets_v3(mats["open"], close, mask)

    in_u = mask.to_numpy()
    # 最後一個交易日必為 NO_TRADE（Entry = O(t+1) 尚不存在），是快取邊界的必然結果、非資訊，
    # 排除它才不會虛灌最後一年的 no_trade_pct（M4）。
    fill_u = fill.where(mask).iloc[:-1]
    years = pd.Index(close.index[:-1]).str[:4]
    fill_report = {}
    for y in sorted(set(years)):
        sel = fill_u.loc[years == y]
        n = int(sel.notna().to_numpy().sum())
        if n == 0:
            continue
        limit_up_pct = round(float((sel == t3.LIMIT_UP_UNFILLED).to_numpy().sum()) / n * 100, 2)
        no_trade_pct = round(float((sel == t3.NO_TRADE).to_numpy().sum()) / n * 100, 2)
        fill_report[y] = {
            "n": n,
            "limit_up_unfilled_pct": limit_up_pct,
            "no_trade_pct": no_trade_pct,
        }
    _log("逐年未成交率（U_t 內）：" + "; ".join(
        f"{y} 漲停 {r['limit_up_unfilled_pct']}% / 停牌 {r['no_trade_pct']}%"
        for y, r in fill_report.items()))

    report = {}
    for n, m in targets.items():
        net = m["net"]
        valid = int(net.notna().to_numpy().sum())
        mean_pct = round(float(net.stack().mean()) * 100, 3)
        median_pct = round(float(net.stack().median()) * 100, 3)
        report[n] = {"valid": valid, "mean_net_pct": mean_pct, "median_net_pct": median_pct}
        _log(f"  {n:>3}D  有效列 {valid:>10,}  均值 {mean_pct:+.3f}%  中位 {median_pct:+.3f}%")

    payload = {
        "meta": {
            "built_at": date.today().isoformat(),
            "spec": "v3 §1: R_net = C(t+N)/O(t+1) − 1 − 0.585%; O(t+1) 漲停 → 未成交",
            "db_max_date": db_max_date,
            "cost_rt": t3.COST_RT,
            "horizons": list(targets),
            "research_start": uv.RESEARCH_START,
            "n_universe_cells": int(in_u.sum()),
            "fill_report": fill_report,
            "report": report,
        },
        "close": close.astype("float32"),
        "open": mats["open"].astype("float32"),
        "high": mats["high"].astype("float32"),
        "low": mats["low"].astype("float32"),
        "volume": mats["volume"].astype("float32"),
        "turnover": mats["turnover"].astype("float32"),
        "universe": mask,
        "entry": entry.astype("float32"),
        "fill": fill,
        "targets": targets,
    }
    with open(_OUT, "wb") as f:
        pickle.dump(payload, f, protocol=4)
    _log(f"已存 {_OUT.name}（{_OUT.stat().st_size / 1e6:.0f} MB）")


if __name__ == "__main__":
    main()
