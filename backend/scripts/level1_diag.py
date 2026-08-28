"""Level 1 LGBM 候選 horizon 健檢（FRS §16：不得依賴少數股票/單一期間/不可交易角落）。

對 horizon 1/5/10/20 重建 LGBM walk-forward 分數並診斷：
1. 逐年 IC（單一期間依賴）
2. Top-20 選股集中度（distinct 檔數、被選次數前 10 名占比）
3. Top-20 選股特性（vol20 / dollar_vol20 / ret5 的橫斷面 rank 中位數）
4. 流動性切片：只留 dollar_vol20 rank ≥ 0.3 的股票重排名後的 IC 與 Top-20 超額
   （檢查 edge 是否全躲在不可交易的殭屍股）

輸出 data/level1_diag.json 與 data/level1_scores_lgbm.pkl（自產快取）。
用法：cd backend && .venv/Scripts/python.exe -m scripts.level1_diag
"""

from __future__ import annotations

import json
import pickle
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd
import sklearn.linear_model  # noqa: F401  # 必須先於 lightgbm 載入：本機 OpenMP DLL
# 載入順序問題，單獨 import lightgbm 會在 LGBM_DatasetSetField 觸發 access violation
from lightgbm import LGBMRegressor

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.research.level1 import evaluation as ev  # noqa: E402
from app.research.level1 import features as ft  # noqa: E402
from app.research.level1 import walkforward as wf  # noqa: E402

_DATA = Path(__file__).resolve().parents[1] / "data"
HORIZONS = (1, 5, 10, 20)
FIRST_TEST = wf.DEFAULT_FIRST_TEST


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _build_ranked(close, mask):
    con = sqlite3.connect(_DATA / "twa.db")
    volq = pd.read_sql_query(
        "SELECT stock_id, date, volume FROM daily_prices WHERE volume IS NOT NULL", con)
    mkt = pd.read_sql_query("SELECT date, close FROM market_index", con,
                            index_col="date")["close"]
    con.close()
    volq = volq[volq.stock_id.isin(set(close.columns))]
    volume = volq.pivot_table(index="date", columns="stock_id", values="volume",
                              aggfunc="last").reindex(index=close.index,
                                                      columns=close.columns)
    price_feats = ft.build_price_features(close, volume)
    from app.storage.database import SessionLocal
    with SessionLocal() as s:
        fund_feats = ft.build_fundamental_features(s, close.index, close.columns)
    ranked = ft.rank_transform({**price_feats, **fund_feats}, mask)
    inter = ft.build_regime_interactions(ranked, mkt)
    return {**ranked, **inter}


def _yearly_ic(score, fwd) -> dict:
    ic = ev.daily_rank_ic(score, fwd)
    ic.index = pd.to_datetime(ic.index)
    return {str(y): round(float(g.mean()), 4)
            for y, g in ic.dropna().groupby(ic.dropna().index.year)}


def _top20_diag(score, fwd, ranked) -> dict:
    rk = score.where(fwd.notna()).rank(axis=1, ascending=False, method="first")
    sel = rk <= 20
    picks = sel.sum(axis=0)
    picks = picks[picks > 0].sort_values(ascending=False)
    total = int(picks.sum())
    char = {}
    for k in ("vol20", "dollar_vol20", "ret5"):
        char[k] = round(float(ranked[k].where(sel).stack().median()), 3)
    return {
        "distinct_stocks": int(len(picks)),
        "top10_pick_share": round(float(picks.head(10).sum() / total), 3),
        "picked_char_median_rank": char,
    }


def main() -> None:
    payload = pickle.load(open(_DATA / "level1_targets.pkl", "rb"))  # 自產快取
    close, mask, targets = (payload["close"].astype("float64"),
                            payload["universe"], payload["targets"])
    ranked = _build_ranked(close, mask)
    _log(f"特徵 {len(ranked)} 個備妥")

    liq_ok = ranked["dollar_vol20"] >= 0.3  # 流動性切片（僅診斷用，非 Universe 變更）

    out: dict = {}
    scores: dict = {}
    for n in HORIZONS:
        fwd = targets[n]["fwd"].astype("float64")
        pct = targets[n]["pct"].astype("float64")
        t0 = time.time()
        # n_jobs 降為 2：n_jobs=-1 在 Windows 偶發 native access violation（閃退）
        score = wf.walk_forward_scores(
            lambda: LGBMRegressor(n_estimators=100, random_state=42,
                                  n_jobs=2, verbose=-1),
            ranked, pct, horizon=n)
        scores[n] = score.astype("float32")
        s_oos = score.loc[score.index >= FIRST_TEST]
        f_oos = fwd.loc[s_oos.index]

        d = {
            "yearly_ic": _yearly_ic(s_oos, f_oos),
            "top20": _top20_diag(s_oos, f_oos, {k: v.loc[s_oos.index]
                                                for k, v in ranked.items()}),
        }
        # 流動性切片：切片內重排名，評 IC 與 Top-20
        s_liq = s_oos.where(liq_ok.loc[s_oos.index])
        f_liq = f_oos.where(liq_ok.loc[s_oos.index])
        d["liquid_only"] = {
            "ic": ev.ic_summary(ev.daily_rank_ic(s_liq, f_liq)),
            "topk": ev.topk_summary(s_liq, f_liq, ks=(20,)),
        }
        out[str(n)] = d
        _log(f"{n}D 完成（{time.time()-t0:.0f}s）：yearly={d['yearly_ic']} "
             f"liq_ic={d['liquid_only']['ic'].get('mean_ic')} "
             f"liq_top20xs={d['liquid_only']['topk']['top20']['excess_pct']}%")

    (_DATA / "level1_diag.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(_DATA / "level1_scores_lgbm.pkl", "wb") as f:
        pickle.dump({"first_test": FIRST_TEST, "scores": scores}, f, protocol=4)
    _log("已存 level1_diag.json / level1_scores_lgbm.pkl")


if __name__ == "__main__":
    main()
