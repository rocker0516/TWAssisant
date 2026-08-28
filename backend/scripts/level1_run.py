"""Level 1 §19 4–8：Baseline 0/1 + Ridge v1/v2 + LightGBM + Walk-forward OOS 評估。

模型階梯（§9）：
    Baseline 0  Random Ranking     —— 驗證評估流程（IC 應 ≈ 0）
    Baseline 1  Naive Momentum     —— ret20 / ret20_ex5 直接當 score（無需訓練）
    Model 1a    ridge_v1 (alpha=1) —— 11 個價量特徵
    Model 1b    ridge_v2 (alpha=1) —— + PIT 基本面 5 個 + 市場 regime 交互 4 個
    Model 2     lgbm（預設超參）    —— v2 特徵集，測非線性與交互（§9 升級閘門：
                                      需在 dev OOS 穩定勝 ridge 才算升級成功）

評估切段：dev OOS 2022-01-01~2024-12-31；holdout 2025-01-01 之後（凍結前只看趨勢，
選特徵/超參只准看 dev）。輸出 data/level1_results.json。

注意：level1_targets.pkl 為本專案 scripts.level1_targets 自產的研究快取
（信任來源，同 mega_mine_features.pkl 慣例），非外部輸入。

用法：cd backend && .venv/Scripts/python.exe -m scripts.level1_run
"""

from __future__ import annotations

import json
import pickle
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.research.level1 import evaluation as ev  # noqa: E402
from app.research.level1 import features as ft  # noqa: E402
from app.research.level1 import walkforward as wf  # noqa: E402

_DATA = Path(__file__).resolve().parents[1] / "data"
FIRST_TEST = wf.DEFAULT_FIRST_TEST
PERIODS = {
    "dev_oos": ("2022-01-01", "2024-12-31"),
    "holdout": ("2025-01-01", "2026-12-31"),
}


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _load_volume(index: pd.Index, columns: pd.Index) -> pd.DataFrame:
    con = sqlite3.connect(_DATA / "twa.db")
    df = pd.read_sql_query(
        "SELECT stock_id, date, volume FROM daily_prices WHERE volume IS NOT NULL", con)
    con.close()
    df = df[df["stock_id"].isin(set(columns))]
    mat = df.pivot_table(index="date", columns="stock_id", values="volume", aggfunc="last")
    return mat.reindex(index=index, columns=columns)


def _oos(score: pd.DataFrame) -> pd.DataFrame:
    return score.loc[score.index >= FIRST_TEST]


def _eval_all(score: pd.DataFrame, fwd: pd.DataFrame) -> dict:
    s = _oos(score)
    f = fwd.loc[s.index]
    out = {"full_oos": ev.evaluate(s, f)}
    out.update(ev.evaluate_by_period(s, f, PERIODS))
    return out


def main() -> None:
    payload = pickle.load(open(_DATA / "level1_targets.pkl", "rb"))  # 自產快取
    close, mask, targets = payload["close"], payload["universe"], payload["targets"]
    close = close.astype("float64")
    _log(f"targets 載入：{close.shape[0]} 日 × {close.shape[1]} 檔")

    volume = _load_volume(close.index, close.columns)
    price_feats = ft.build_price_features(close, volume)

    from app.storage.database import SessionLocal
    with SessionLocal() as s:
        fund_feats = ft.build_fundamental_features(s, close.index, close.columns)
    _log(f"基本面特徵覆蓋率：" + ", ".join(
        f"{k} {float(v.where(mask).notna().stack().mean()):.0%}"
        for k, v in fund_feats.items()))

    ranked = ft.rank_transform({**price_feats, **fund_feats}, mask)
    con = sqlite3.connect(_DATA / "twa.db")
    mkt = pd.read_sql_query("SELECT date, close FROM market_index", con,
                            index_col="date")["close"]
    con.close()
    inter = ft.build_regime_interactions(ranked, mkt)
    ranked_v2 = {**ranked, **inter}
    v1_keys = list(price_feats)
    ranked_v1 = {k: ranked[k] for k in v1_keys}
    _log(f"特徵 v1={len(ranked_v1)} / v2={len(ranked_v2)} 個"
         f"（+基本面 {len(fund_feats)}、+regime 交互 {len(inter)}）")

    rng_scores = []
    for seed in (0, 1, 2):
        rng = np.random.default_rng(seed)
        m = pd.DataFrame(rng.random(close.shape), index=close.index,
                         columns=close.columns).where(mask)
        rng_scores.append(m)

    from lightgbm import LGBMRegressor

    models = {
        "ridge_v1": (ranked_v1, lambda: Ridge(alpha=1.0)),
        "ridge_v2": (ranked_v2, lambda: Ridge(alpha=1.0)),
        "lgbm": (ranked_v2, lambda: LGBMRegressor(
            n_estimators=100, random_state=42, n_jobs=-1, verbose=-1)),
    }
    results: dict = {"first_test": FIRST_TEST, "periods": PERIODS,
                     "features_v1": v1_keys, "features_v2": list(ranked_v2),
                     "horizons": {}}
    for n, mat in targets.items():
        fwd = mat["fwd"].astype("float64")
        pct = mat["pct"].astype("float64")
        _log(f"── horizon {n}D ──")
        r: dict = {}

        r["random"] = _eval_all(rng_scores[0], fwd)
        r["random"]["seed_mean_ics"] = [
            ev.ic_summary(ev.daily_rank_ic(_oos(s), fwd.loc[_oos(s).index]))["mean_ic"]
            for s in rng_scores]
        _log(f"  random   mean_ic(3 seeds)={r['random']['seed_mean_ics']}")

        for name in ("ret20", "ret20_ex5"):
            r[f"mom_{name}"] = _eval_all(ranked[name], fwd)
            s = r[f"mom_{name}"]["full_oos"]
            _log(f"  mom_{name:<10} ic={s['mean_ic']:+.4f} icir={s['icir']} "
                 f"mono={s['monotonicity']:+.2f} spread={s['top_bottom_spread_pct']:+.2f}%")

        for mname, (feat_set, make) in models.items():
            t0 = time.time()
            score = wf.walk_forward_scores(make, feat_set, pct, horizon=n)
            r[mname] = _eval_all(score, fwd)
            s, d = r[mname]["full_oos"], r[mname].get("dev_oos", {})
            _log(f"  {mname:<9} ic={s['mean_ic']:+.4f} icir={s['icir']} "
                 f"mono={s['monotonicity']:+.2f} spread={s['top_bottom_spread_pct']:+.2f}% "
                 f"top20xs={s['topk']['top20']['excess_pct']:+.2f}% "
                 f"| dev ic={d.get('mean_ic')} mono={d.get('monotonicity')} "
                 f"({time.time()-t0:.0f}s)")
        results["horizons"][str(n)] = r

    out = _DATA / "level1_results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    _log(f"已存 {out.name}")


if __name__ == "__main__":
    main()
