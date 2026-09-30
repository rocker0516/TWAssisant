"""§23.5 Delayed Performance：從 ledger（成熟列）算 rolling live metrics（20D / 60D / 120D 成熟交易日視窗）。

Live 指標與 B9 frozen validation 同定義：Lift@K = TargetRate@K / 同窗 Market 基率；StopRatio、net10、ECE。
只算 status != SYSTEM_NO_TRADE 的 run（fail-closed 日不出單，但 shadow 預測仍存，這裡不計入推薦）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.storage import models

from ..evaluation.model_metrics import ece


def load_matured(session: Session) -> pd.DataFrame:
    P, R = models.MLEntryPrediction, models.MLEntryRun
    rows = session.execute(
        select(P.run_id, P.signal_date, P.stock_id, P.recommended, P.rank, P.p_target_10d, P.p_stop_10d,
               P.target_hit_10d, P.stop_hit_10d, P.return_10d, P.mfe_10d, P.mae_10d, P.event_type, R.status, R.no_trade)
        .join(R, R.run_id == P.run_id).where(P.matured_at.is_not(None), P.target_hit_10d.is_not(None))
    ).all()
    cols = ["run_id", "signal_date", "stock_id", "recommended", "rank", "p_target_10d", "p_stop_10d", "target_hit_10d",
            "stop_hit_10d", "return_10d", "mfe_10d", "mae_10d", "event_type", "status", "no_trade"]
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df
    # 同一 signal_date 若有多個 run（重跑），取最後一個 run_id
    last = df.groupby("signal_date")["run_id"].max()
    return df[df["run_id"] == df["signal_date"].map(last)].reset_index(drop=True)


def rolling_live_metrics(df: pd.DataFrame, windows=(20, 60, 120), k: int = 5, cost_rt: float = 0.00585) -> dict:
    out = {"matured_days": 0, "windows": {}}
    if df.empty:
        return out
    days = sorted(df["signal_date"].unique())
    out["matured_days"] = len(days)
    for w in windows:
        sel_days = days[-w:]
        d = df[df["signal_date"].isin(sel_days)]
        rec = d[d["recommended"] & (d["rank"] <= k) & (d["status"] != "SYSTEM_NO_TRADE")]
        base_t, base_s = d["target_hit_10d"].mean(), d["stop_hit_10d"].mean()
        item = {"days": len(sel_days), "n_rec": int(len(rec)), "market_target_rate": float(base_t), "market_stop_rate": float(base_s),
                "ece_target_10d": float(ece(d["target_hit_10d"].to_numpy(dtype=float), d["p_target_10d"].to_numpy(dtype=float)))}
        if len(rec):
            item.update({"target_rate": float(rec["target_hit_10d"].mean()), "target_lift": float(rec["target_hit_10d"].mean() / base_t) if base_t > 0 else None,
                         "stop_rate": float(rec["stop_hit_10d"].mean()), "stop_ratio": float(rec["stop_hit_10d"].mean() / base_s) if base_s > 0 else None,
                         "timeout_rate": float((rec["event_type"] == 4).mean()), "mean_net10": float((rec["return_10d"] - cost_rt).mean()),
                         "median_mae": float(rec["mae_10d"].median()), "median_mfe": float(rec["mfe_10d"].median())})
        out["windows"][str(w)] = item
    return out
