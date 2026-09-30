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
               P.target_hit_10d, P.stop_hit_10d, P.return_10d, P.mfe_10d, P.mae_10d, P.event_type, R.status, R.no_trade, R.qualified_count)
        .join(R, R.run_id == P.run_id).where(P.matured_at.is_not(None), P.target_hit_10d.is_not(None))
    ).all()
    cols = ["run_id", "signal_date", "stock_id", "recommended", "rank", "p_target_10d", "p_stop_10d", "target_hit_10d",
            "stop_hit_10d", "return_10d", "mfe_10d", "mae_10d", "event_type", "status", "no_trade", "qualified_count"]
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
        live_day = d[d["status"] != "SYSTEM_NO_TRADE"]
        item["lift"] = {}
        for kk in (1, 3, 5):
            rk = live_day[live_day["recommended"] & (live_day["rank"] <= kk)]
            item["lift"][str(kk)] = float(rk["target_hit_10d"].mean() / base_t) if len(rk) and base_t > 0 else None
        per_day = d.groupby("signal_date").agg(n_rec=("recommended", "sum"), q=("qualified_count", "first"),
                                                nt=("no_trade", "first"), st=("status", "first"))
        item["coverage"] = float(((per_day["n_rec"] > 0) & (per_day["st"] != "SYSTEM_NO_TRADE")).mean())
        item["candidates_median"] = float(per_day["q"].median())
        item["no_trade_rate"] = float(per_day["nt"].astype(bool).mean())
        if len(rec):
            item.update({"target_rate": float(rec["target_hit_10d"].mean()), "target_lift": float(rec["target_hit_10d"].mean() / base_t) if base_t > 0 else None,
                         "stop_rate": float(rec["stop_hit_10d"].mean()), "stop_ratio": float(rec["stop_hit_10d"].mean() / base_s) if base_s > 0 else None,
                         "timeout_rate": float((rec["event_type"] == 4).mean()), "mean_net10": float((rec["return_10d"] - cost_rt).mean()),
                         "median_mae": float(rec["mae_10d"].median()), "median_mfe": float(rec["mfe_10d"].median())})
        out["windows"][str(w)] = item
    return out


def daily_matured(df: pd.DataFrame, k: int = 5, cost_rt: float = 0.00585, limit: int = 60) -> list[dict]:
    if df.empty:
        return []
    out = []
    for sd, d in sorted(df.groupby("signal_date"), key=lambda x: x[0], reverse=True)[:limit]:
        rec = d[d["recommended"] & (d["rank"] <= k) & (d["status"] != "SYSTEM_NO_TRADE")]
        base = d["target_hit_10d"].mean()
        out.append({"signal_date": pd.Timestamp(sd).date().isoformat(), "n_rec": int(len(rec)),
                    "target": int((rec["event_type"] == 1).sum()), "stop": int(rec["event_type"].isin([2, 3]).sum()),
                    "timeout": int((rec["event_type"] == 4).sum()),
                    "lift": float(rec["target_hit_10d"].mean() / base) if len(rec) and base > 0 else None,
                    "net10": float((rec["return_10d"] - cost_rt).mean()) if len(rec) else None})
    return out


# (key, label, fmt, frozen 值鍵, band 鍵（ci）或 (lo, hi) 鍵（dist）, live window 取值函式)
_CONV_ROWS = (
    ("lift_at_1", "Lift@1", "x", "lift_at_1", None, lambda w: w.get("lift", {}).get("1")),
    ("lift_at_3", "Lift@3", "x", "lift_at_3", None, lambda w: w.get("lift", {}).get("3")),
    ("lift_at_5", "Lift@5", "x", "target_lift_at_5", "ci_target_lift", lambda w: w.get("lift", {}).get("5")),
    ("stop_ratio_at_5", "StopRatio@5", "ratio", "stop_ratio_at_5", "ci_stop_ratio", lambda w: w.get("stop_ratio")),
    ("net10", "Net10／筆", "pct", "mean_net10", "ci_net10", lambda w: w.get("mean_net10")),
    ("coverage", "Coverage", "pct", "coverage", None, lambda w: w.get("coverage")),
    ("candidates_per_day", "候選數／日", "num", "candidates_median", ("candidates_p05", "candidates_p95"),
     lambda w: w.get("candidates_median")),
    ("no_trade_rate", "NO_TRADE 率", "pct", "no_trade_rate", None, lambda w: w.get("no_trade_rate")),
    ("ece_target_10d", "ECE Target10", "num3", "ece_target_10d", None, lambda w: w.get("ece_target_10d")),
    ("median_mfe_10d", "Median MFE 10D", "pct", "median_mfe_10d", None, lambda w: w.get("median_mfe")),
    ("median_mae_10d", "Median MAE 10D", "pct", "median_mae_10d", None, lambda w: w.get("median_mae")),
)


def _judge(v, days: int, w: int, band, kind) -> str:
    if v is None or days < w:
        return "累積中"
    if band is None:
        return "參考"
    inside = band[0] <= v <= band[1]
    if kind == "ci":
        return "CI 內" if inside else "CI 外"
    return "分布內" if inside else "分布外"


def convergence(frozen: dict, live: dict, windows: tuple[int, ...] = (20, 60)) -> list[dict]:
    """Frozen OOF vs Live 的描述性比對（附錄 C：20 成熟日只看不決策、60 為判斷點）。不寫入任何狀態。"""
    rows = []
    for key, label, fmt, fkey, bkey, live_fn in _CONV_ROWS:
        if isinstance(bkey, tuple):
            lo, hi = frozen.get(bkey[0]), frozen.get(bkey[1])
            band, kind = ([float(lo), float(hi)] if lo is not None and hi is not None else None), "dist"
        elif bkey is not None:
            ci = frozen.get(bkey)
            band, kind = ([float(ci[0]), float(ci[1])] if isinstance(ci, (list, tuple)) and len(ci) == 2 else None), "ci"
        else:
            band, kind = None, None
        fv = frozen.get(fkey)
        lv, vd = {}, {}
        for w in windows:
            item = live.get("windows", {}).get(str(w))
            v = live_fn(item) if item else None
            lv[str(w)] = float(v) if v is not None else None
            vd[str(w)] = _judge(lv[str(w)], int(item["days"]) if item else 0, w, band, kind)
        rows.append({"key": key, "label": label, "fmt": fmt, "frozen": float(fv) if fv is not None else None,
                     "band": band, "band_kind": kind if band is not None else None, "live": lv, "verdict": vd})
    return rows
