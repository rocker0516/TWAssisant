"""Level 2 walk-forward 回測（FRS §7，M2）。

流程：level1_targets.pkl（研究快取，fresh check）→ 重建 v2 特徵 →
walk_forward_scores（lgbm，horizon 1/5，embargo=horizon）→ 逐日橫斷面
pct_rank → 模擬引擎重放（app.research.level2，與 live 同一份程式碼）。

組合：
  P5            5D 再平衡＋1D 防禦（Baseline v0 主組合）
  P1            1D 每日再平衡對照
  random_top20  隨機排名 3 種子（引擎健檢：超額應 ≈ 0 − 成本拖累）
  universe_ew   U_t 等權日報酬解析解（市場對照；不經引擎——590 檔 × 100 萬
                會被 20 元低消支配，非有意義的引擎測試，FRS §5 0a 的偏離記錄於此）

紀律：--segment dev 可重複跑（調參只准看 dev）；--segment holdout 需
--confirm-holdout（凍結後一次性，FRS §7）。

用法（cwd=backend；TWA_DATA_DIR 可指向另一份 data 目錄）：
  .venv/Scripts/python.exe -m scripts.level2_backtest --segment dev
"""

from __future__ import annotations

import argparse
import json
import pickle
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn.linear_model  # noqa: F401  # 必須先於 lightgbm（OpenMP DLL 順序坑）
from lightgbm import LGBMRegressor

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.research.level1 import features as ft  # noqa: E402
from app.research.level1 import walkforward as wf  # noqa: E402
from app.research.level2 import metrics as mt  # noqa: E402
from app.research.level2.costs import CostModel  # noqa: E402
from app.research.level2.policy import (P1_PARAMS, P5_PARAMS,  # noqa: E402
                                        POLICY_VERSION, BaselineParams)
from app.research.level2.simulate import run_simulation  # noqa: E402

SEGMENTS = {
    "dev": ("2022-01-01", "2024-12-31"),
    "holdout": ("2025-01-01", "2099-12-31"),
}
INITIAL_CASH = 1_000_000.0
HORIZONS = (1, 5)


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _load_snapshot(data_dir: Path) -> dict:
    # level1_targets.pkl 為 scripts.level1_targets 自產研究快取（信任來源，
    # 同 level1_run 慣例），非外部輸入——pickle 安全性風險不適用。
    payload = pickle.load(open(data_dir / "level1_targets.pkl", "rb"))
    con = sqlite3.connect(data_dir / "twa.db")
    db_max = con.execute("SELECT max(date) FROM daily_prices").fetchone()[0]
    con.close()
    if payload["meta"].get("db_max_date") != db_max:
        raise SystemExit(
            f"level1_targets.pkl 建於 {payload['meta'].get('db_max_date')}，"
            f"DB 現況 {db_max}。先重跑 scripts.level1_targets。")
    return payload


def _load_open(data_dir: Path, close: pd.DataFrame) -> pd.DataFrame:
    con = sqlite3.connect(data_dir / "twa.db")
    df = pd.read_sql_query(
        "SELECT stock_id, date, open FROM daily_prices WHERE open IS NOT NULL",
        con)
    con.close()
    df = df[df.stock_id.isin(set(close.columns))]
    mat = df.pivot_table(index="date", columns="stock_id", values="open",
                         aggfunc="last")
    return mat.reindex(index=close.index, columns=close.columns)


def _load_benchmark(data_dir: Path) -> pd.Series:
    con = sqlite3.connect(data_dir / "twa.db")
    s = pd.read_sql_query("SELECT date, close FROM market_index", con,
                          index_col="date")["close"]
    con.close()
    return s


def _build_scores(payload: dict, data_dir: Path) -> dict[int, pd.DataFrame]:
    """重建 v2 特徵並跑 walk-forward OOS 分數（同 level1_run 的 lgbm 路徑）。"""
    close = payload["close"].astype("float64")
    mask = payload["universe"]
    con = sqlite3.connect(data_dir / "twa.db")
    vol = pd.read_sql_query(
        "SELECT stock_id, date, volume FROM daily_prices WHERE volume IS NOT NULL",
        con)
    mkt = pd.read_sql_query("SELECT date, close FROM market_index", con,
                            index_col="date")["close"]
    con.close()
    vol = vol[vol.stock_id.isin(set(close.columns))]
    volume = (vol.pivot_table(index="date", columns="stock_id",
                              values="volume", aggfunc="last")
              .reindex(index=close.index, columns=close.columns))

    price_feats = ft.build_price_features(close, volume)
    from app.storage.database import SessionLocal
    with SessionLocal() as s:
        fund_feats = ft.build_fundamental_features(s, close.index, close.columns)
    ranked = ft.rank_transform({**price_feats, **fund_feats}, mask)
    ranked.update(ft.build_regime_interactions(ranked, mkt))
    _log(f"特徵 {len(ranked)} 個（v2_feat20_u2 同構）")

    make = lambda: LGBMRegressor(n_estimators=100, random_state=42,  # noqa: E731
                                 n_jobs=1, verbose=-1)
    scores = {}
    for n in HORIZONS:
        t0 = time.time()
        pct = payload["targets"][n]["pct"].astype("float64")
        scores[n] = wf.walk_forward_scores(make, ranked, pct, horizon=n)
        _log(f"walk-forward {n}D 完成（{time.time() - t0:.0f}s）")
    return scores


def _get_scores(payload: dict, data_dir: Path) -> dict[int, pd.DataFrame]:
    """walk-forward 分數快取（自產、以 db_max_date 驗證新鮮度）——
    dev 調參迭代不必每輪重訓 7 分鐘。"""
    cache = data_dir / "level2_scores.pkl"
    key = payload["meta"]["db_max_date"]
    if cache.exists():
        blob = pickle.load(open(cache, "rb"))  # 自產快取，信任來源
        if blob.get("db_max_date") == key:
            _log("walk-forward 分數：用快取")
            return blob["scores"]
    scores = _build_scores(payload, data_dir)
    pickle.dump({"db_max_date": key, "scores": scores}, open(cache, "wb"))
    return scores


def _pct_rank(score: pd.DataFrame) -> pd.DataFrame:
    """逐日橫斷面百分位（1.0=最強），與 ledger.rank_scores 同語意。"""
    return score.rank(axis=1, ascending=True, pct=True)


def _slice(df: pd.DataFrame | pd.Series, lo: str, hi: str):
    return df.loc[(df.index >= lo) & (df.index <= hi)]


def _run_segment(payload: dict, scores: dict, data_dir: Path,
                 segment: str) -> dict:
    lo, hi = SEGMENTS[segment]
    close = payload["close"].astype("float64")
    mask = payload["universe"]
    open_ = _load_open(data_dir, close)
    bench = _load_benchmark(data_dir)

    pct1 = _pct_rank(scores[1])
    pct5 = _pct_rank(scores[5])
    # 模擬窗：段內且有訊號的日子起跑
    sim_dates = close.index[(close.index >= lo) & (close.index <= hi)]
    first_sig = pct5.loc[sim_dates].dropna(how="all").index.min()
    sim_dates = sim_dates[sim_dates >= first_sig]
    o, c = open_.loc[sim_dates], close.loc[sim_dates]
    p1, p5 = pct1.loc[sim_dates], pct5.loc[sim_dates]
    b = bench.reindex(sim_dates).ffill()
    _log(f"{segment}: {sim_dates[0]} ~ {sim_dates[-1]}（{len(sim_dates)} 日）")

    cost = CostModel()
    out: dict = {}

    # 主組合＋對照；dev 段另跑診斷變體（調參只准看 dev——FRS §7）
    variants: dict[str, tuple[pd.DataFrame, pd.DataFrame | None, BaselineParams]] = {
        "P5": (p5, p1, P5_PARAMS),
        "P1": (p1, None, P1_PARAMS),
    }
    if segment == "dev":
        variants.update({
            "P5_nodef": (p5, None, BaselineParams(use_defense=False)),
            "P5_def010": (p5, p1, BaselineParams(defense_pct=0.1)),
            "P5_reb10_hold120": (p5, p1, BaselineParams(
                rebalance_every=10, k_hold=120)),
            "P5_reb10_hold120_nodef": (p5, None, BaselineParams(
                rebalance_every=10, k_hold=120, use_defense=False)),
            "P5_reb20_hold200_nodef": (p5, None, BaselineParams(
                rebalance_every=20, k_hold=200, use_defense=False)),
            "P5_reb10_hold120_def010": (p5, p1, BaselineParams(
                rebalance_every=10, k_hold=120, defense_pct=0.1)),
            "P5_reb20_hold200_def020": (p5, p1, BaselineParams(
                rebalance_every=20, k_hold=200)),
            "P5_reb20_hold200_def010": (p5, p1, BaselineParams(
                rebalance_every=20, k_hold=200, defense_pct=0.1)),
            "P5_reb10_n10_hold120": (p5, p1, BaselineParams(
                target_n=10, k_in=20, rebalance_every=10, k_hold=120)),
        })
    for name, (rank_df, def_df, params) in variants.items():
        res = run_simulation(o, c, rank_df, def_df, params, INITIAL_CASH, cost)
        out[name] = mt.summarize(res.nav, b, res.fills)
        out[name]["params"] = params.__dict__
        if name in ("P5", "P1"):
            out[name]["nav"] = {"date": list(res.nav.index),
                                "nav": [round(v, 0) for v in res.nav]}
        s = out[name]
        _log(f"  {name:<24} 超額 {s['excess_pct']:+8.2f}% (t={s['excess_t']:+.2f}) "
             f"換手 {s['turnover_annual']:5.1f} 成本 {s['cost_drag_pct']:5.1f}pp "
             f"MDD {s['mdd_pct']}%")

    rand = []
    for seed in (0, 1, 2):
        rng = np.random.default_rng(seed)
        rpct = pd.DataFrame(rng.random(p5.shape), index=p5.index,
                            columns=p5.columns).where(p5.notna())
        rr = run_simulation(o, c, rpct, None,
                            BaselineParams(use_defense=False), INITIAL_CASH,
                            cost)
        rand.append(mt.summarize(rr.nav, b, rr.fills))
    out["random_top20"] = {"seeds": rand,
                           "mean_excess_pct": round(
                               sum(r["excess_pct"] for r in rand) / 3, 2)}
    _log(f"  random×3 平均超額 {out['random_top20']['mean_excess_pct']:+.2f}%"
         f"（應 ≈ 0 − 成本拖累）")

    # universe 等權（解析解，市場對照）
    m = mask.loc[sim_dates]
    ew_ret = close.loc[sim_dates].pct_change(fill_method=None).where(m.shift(1)).mean(axis=1)
    ew_nav = (1 + ew_ret.fillna(0)).cumprod() * INITIAL_CASH
    out["universe_ew"] = mt.summarize(ew_nav, b, pd.DataFrame())
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--segment", choices=list(SEGMENTS), default="dev")
    ap.add_argument("--confirm-holdout", action="store_true",
                    help="holdout 一次性跑的確認旗標（FRS §7）")
    args = ap.parse_args()
    if args.segment == "holdout" and not args.confirm_holdout:
        raise SystemExit("holdout 是凍結後一次性評估：請帶 --confirm-holdout。")

    data_dir = Path(settings.data_dir)
    payload = _load_snapshot(data_dir)
    _log(f"snapshot OK（db_max={payload['meta']['db_max_date']}）")
    scores = _get_scores(payload, data_dir)
    out = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "segment": args.segment,
            "policy_version": POLICY_VERSION,
            "initial_cash": INITIAL_CASH,
            "cost_model": CostModel().__dict__,
            "params": {"P5": P5_PARAMS.__dict__, "P1": P1_PARAMS.__dict__},
            "db_max_date": payload["meta"]["db_max_date"],
        },
        "result": _run_segment(payload, scores, data_dir, args.segment),
    }
    path = data_dir / f"level2_backtest_{args.segment}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    _log(f"已存 {path}")


if __name__ == "__main__":
    main()
