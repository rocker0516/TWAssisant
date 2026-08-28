"""Level 1 Target Generator 建置（FRS §19 第 1–2 項：PIT Universe + Target）。

產出 data/level1_targets.pkl：
    {"meta": {...}, "close": 收盤價矩陣, "universe": U_t 布林矩陣,
     "targets": {N: {"fwd": 報酬矩陣, "pct": 百分位矩陣}}}

用法：
    cd backend
    .venv/Scripts/python.exe -m scripts.level1_targets
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

from app.research.level1 import targets as tg, universe as uv  # noqa: E402

_DB = Path(__file__).resolve().parents[1] / "data" / "twa.db"
_OUT = Path(__file__).resolve().parents[1] / "data" / "level1_targets.pkl"

RESEARCH_START = "2020-02-01"   # ADV20 暖身期之後（設計 §4.2）


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    con = sqlite3.connect(_DB)
    db_max_date = con.execute("SELECT max(date) FROM daily_prices").fetchone()[0]
    close, mask = uv.build_tradable_universe(con)   # 唯一入口（設計 §6）
    con.close()

    # ADV20 暖身期（rolling 20 / min_periods 10）不足，起點後推（設計 §4.2）
    keep = close.index >= RESEARCH_START
    close, mask = close.loc[keep], mask.loc[keep]
    _log(f"U_t 已含流動性底線 ADV20 ≥ {uv.ADV20_FLOOR:,.0f} 與處置排除")
    usize = mask.sum(axis=1)
    _log(f"價格矩陣 {close.shape[0]} 日 × {close.shape[1]} 檔"
         f"（{close.index[0]} ~ {close.index[-1]}）")
    _log(f"U_t 規模：min {usize.min()} / median {int(usize.median())} / max {usize.max()}")

    built = tg.build_targets(close, mask)

    _log("horizon  可用列數      缺fwd列數   pct均值")
    report = {}
    for n, m in built.items():
        pct, fwd = m["pct"], m["fwd"]
        n_valid = int(pct.notna().sum().sum())
        n_missing = int((mask & fwd.isna()).sum().sum())
        mean_pct = float(pct.stack().mean())
        report[n] = {"valid": n_valid, "missing_fwd": n_missing, "mean_pct": mean_pct}
        _log(f"  {n:>3}D  {n_valid:>10,}  {n_missing:>10,}   {mean_pct:.4f}")

    payload = {
        "meta": {
            "built_at": date.today().isoformat(),
            "spec": "FRS v1.1 §5: Y = Percentile(R(t,N) | U_t^Tradable)",
            "db_max_date": db_max_date,
            "adv20_floor": uv.ADV20_FLOOR,
            "research_start": RESEARCH_START,
            "horizons": list(built),
            "date_range": (str(close.index[0]), str(close.index[-1])),
            "n_stocks": int(close.shape[1]),
            "universe_size": {"min": int(usize.min()), "median": int(usize.median()),
                              "max": int(usize.max())},
            "report": report,
            "limitations": [
                "close 未還原權息（全市場股利資料不可得）",
                "下市股僅部分保留（系統收錄前消失者補不到）",
                "U_t 已含流動性底線（ADV20 ≥ 5,000 萬）與處置排除，非全市場普通股",
            ],
        },
        "close": close.astype("float32"),
        "universe": mask,
        "targets": built,
    }
    with open(_OUT, "wb") as f:
        pickle.dump(payload, f, protocol=4)
    _log(f"已存 {_OUT.name}（{_OUT.stat().st_size / 1e6:.0f} MB）")


if __name__ == "__main__":
    main()
