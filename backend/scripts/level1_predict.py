"""Level 1 每日 Recommendation Pipeline（FRS §14）＋ Ledger 成熟回填（§15）。

流程：建 T 日 PIT Universe → 產 T 日特徵 → 以截至 T 的成熟標籤重訓 → Prediction
→ Cross-sectional Ranking → 全排名寫入 level1_predictions → 成熟列回填 actual_*。

Model Version 凍結定義（l1_lgbm_v1）：
- 模型：LightGBM(n_estimators=100, random_state=42)，超參不得調（凍結）
- 特徵：v2_feat20 = 11 價量 + 5 PIT 基本面 + 4 市場 regime 交互（每日橫斷面 rank）
- Target：未來 N 日 Close-to-Close 報酬之 U_t 橫斷面百分位（N ∈ 1/5/10，5D 主軌）
- 訓練協定：expanding，用全部「已成熟」標籤——label 需要 t+N 收盤才存在，
  訓練集天然結束在預測日前 N 個交易日，與研究框架的 embargo 同語意。
- 驗證紀錄：data/level1_results.json（walk-forward OOS）+ level1_diag.json（§16 健檢）

用法（cwd=backend）：
  .venv/Scripts/python.exe -m scripts.level1_predict                # 最新交易日
  .venv/Scripts/python.exe -m scripts.level1_predict --date 2026-08-26
  .venv/Scripts/python.exe -m scripts.level1_predict --mature-only  # 只回填
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import sklearn.linear_model  # noqa: F401  # 必須先於 lightgbm：本機 OpenMP DLL 順序坑
from lightgbm import LGBMRegressor
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.research.level1 import features as ft  # noqa: E402
from app.research.level1 import ledger as lg  # noqa: E402
from app.research.level1 import targets as tg  # noqa: E402
from app.research.level1 import universe as uv  # noqa: E402
from app.research.level1 import walkforward as wf  # noqa: E402
from app.storage import models, repositories as repo  # noqa: E402
from app.storage.database import SessionLocal, init_db  # noqa: E402

_DB = Path(__file__).resolve().parents[1] / "data" / "twa.db"
MODEL_VERSION = "l1_lgbm_v1"
FEATURE_VERSION = "v2_feat20"
HORIZONS = (1, 5, 10)  # 5D 主軌，1D/10D 並行入帳（2026-08-28 使用者定案）


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _build_all():
    con = sqlite3.connect(_DB)
    stocks = uv.load_stocks(con)
    elig = uv.eligible_ids(stocks)
    prices = uv.load_close_prices(con, elig)
    volq = pd.read_sql_query(
        "SELECT stock_id, date, volume FROM daily_prices WHERE volume IS NOT NULL", con)
    mkt = pd.read_sql_query("SELECT date, close FROM market_index", con,
                            index_col="date")["close"]
    con.close()

    close = uv.close_matrix(prices)
    mask = uv.universe_mask(close)
    volume = (volq[volq.stock_id.isin(elig)]
              .pivot_table(index="date", columns="stock_id", values="volume",
                           aggfunc="last")
              .reindex(index=close.index, columns=close.columns))

    price_feats = ft.build_price_features(close, volume)
    with SessionLocal() as s:
        fund_feats = ft.build_fundamental_features(s, close.index, close.columns)
    ranked = ft.rank_transform({**price_feats, **fund_feats}, mask)
    ranked.update(ft.build_regime_interactions(ranked, mkt))
    return close, mask, ranked


def predict(close, mask, ranked, pred_date: str) -> None:
    if pred_date not in close.index:
        raise SystemExit(f"{pred_date} 不在交易日曆內（最新：{close.index[-1]}）")
    ledger_repo = repo.Level1PredictionRepository()
    for n in HORIZONS:
        t0 = time.time()
        pct = tg.cross_sectional_pct(tg.forward_returns(close, n), mask)
        # 訓練窗綁 pred_date 而非 DB 最新日——與 OOS 共用同一個 train_slice（設計 §6）
        train_dates = wf.train_slice_for_date(close.index, pred_date, embargo=n)
        x_tr, y_tr, _ = ft.assemble_dataset(ranked, pct, train_dates)
        # n_jobs=1 是 reproducibility control，不是 predictive-performance control
        model = LGBMRegressor(n_estimators=100, random_state=42, n_jobs=1, verbose=-1)
        model.fit(x_tr, y_tr)

        x_te, meta = ft.assemble_for_dates(ranked, mask, pd.Index([pred_date]))
        scores = pd.Series(model.predict(x_te), index=meta["stock_id"])
        board = lg.rank_scores(scores)
        rows = [
            {
                "prediction_date": date.fromisoformat(pred_date), "stock_id": sid,
                "horizon": n, "model_version": MODEL_VERSION,
                "score": float(r["score"]), "rank": int(r["rank"]),  # r.rank 是方法
                "pct_rank": float(r["pct_rank"]),
                "universe_size": int(r["universe_size"]),
                "feature_version": FEATURE_VERSION,
            }
            for sid, r in board.iterrows()
        ]
        with SessionLocal() as s:
            ledger_repo.upsert_many(s, rows)
            s.commit()
        top5 = board.sort_values("rank").head(5).index.tolist()
        _log(f"{n}D：訓練 {len(y_tr):,} 列（迄 {train_dates[-1]}）、寫入 {len(rows)} 檔 "
             f"Top5={top5}（{time.time()-t0:.0f}s）")


def mature(close) -> None:
    """回填 actual_*：t+N 已到期且尚未成熟的 ledger 列。"""
    ledger_repo = repo.Level1PredictionRepository()
    with SessionLocal() as s:
        rows = s.execute(
            select(models.Level1Prediction).where(
                models.Level1Prediction.actual_return.is_(None),
                models.Level1Prediction.model_version == MODEL_VERSION)
        ).scalars().all()
        if not rows:
            _log("成熟回填：無待處理列")
            return
        df = pd.DataFrame([{
            "prediction_date": r.prediction_date, "stock_id": r.stock_id,
            "horizon": r.horizon, "score": r.score, "rank": r.rank,
            "pct_rank": r.pct_rank, "universe_size": r.universe_size,
            "feature_version": r.feature_version,
        } for r in rows])
        total = 0
        for n, g in df.groupby("horizon"):
            done = lg.compute_actuals(g, close, int(n))
            if done.empty:
                continue
            done["matured_at"] = date.today()
            done["model_version"] = MODEL_VERSION
            done["horizon"] = int(n)
            for c in ("rank", "universe_size"):
                done[c] = done[c].astype(int)
            recs = done.to_dict("records")
            total += ledger_repo.upsert_many(s, recs)
        s.commit()
    _log(f"成熟回填：{total} 列（待處理 {len(df)} 列中已到期者）")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="預測日（預設最新交易日）")
    ap.add_argument("--mature-only", action="store_true")
    args = ap.parse_args()

    init_db()
    close, mask, ranked = _build_all()
    _log(f"資料備妥：{close.shape[0]} 日 × {close.shape[1]} 檔，最新 {close.index[-1]}")
    if not args.mature_only:
        predict(close, mask, ranked, args.date or str(close.index[-1]))
    mature(close)


if __name__ == "__main__":
    main()
