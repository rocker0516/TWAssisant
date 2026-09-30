"""追蹤中（/app/level1 榜單頁）：近 N 個交易日推薦的即時路徑。

語意唯一來源：labels.barriers.run_barriers。作法：在價格矩陣尾端補 max_horizon 列 NaN，讓每個 signal
在引擎內都「成熟」，即可讀 first-hit／MFE／MAE／return_10d；未觸者依已走天數判定 LIVE 或 TIMEOUT。
已成熟且 ledger 有 event_type 者以 ledger 為準（與體檢頁同一份事實）。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..config import LabelConfig
from ..labels.barriers import EntryStatus, Event, run_barriers

_EVENT_STATUS = {int(Event.TARGET): "TARGET", int(Event.STOP): "STOP",
                 int(Event.STOP_AMBIGUOUS): "STOP_AMBIGUOUS", int(Event.TIMEOUT): "TIMEOUT",
                 int(Event.NOT_ENTERED): "NOT_ENTERED"}


def _f(x) -> float | None:
    x = float(x)
    return None if math.isnan(x) else x


def track_paths(m: dict[str, pd.DataFrame], items: list[tuple[str, str]], cfg: LabelConfig,
                ledger: dict[tuple[str, str], int] | None = None) -> list[dict]:
    K = cfg.max_horizon
    close = m["close"]
    dates = [str(d) for d in close.index]
    pos = {d: i for i, d in enumerate(dates)}
    last = len(dates) - 1
    pad = pd.Index([f"~pad{i:02d}" for i in range(K)], name=close.index.name)
    padded = {k: pd.concat([m[k].set_axis(dates, axis=0),
                            pd.DataFrame(np.nan, index=pad, columns=m[k].columns)])
              for k in ("open", "high", "low", "close")}
    out = run_barriers(padded, cfg)
    ledger = ledger or {}
    rows = []
    for sd, sid in items:
        base = {"signal_date": sd, "stock_id": sid, "horizon": K, "hit_day": None,
                "ret_now": None, "mfe": None, "mae": None}
        if sd not in pos or sid not in close.columns:
            rows.append({**base, "day_index": 0, "status": "DATA_MISSING"}); continue
        day_index = min(last - pos[sd], K)
        if day_index == 0:
            rows.append({**base, "day_index": 0, "status": "PENDING_ENTRY"}); continue
        st = int(out["entry_status"].at[sd, sid])
        if st != int(EntryStatus.FILLED):
            status = "NOT_ENTERED" if st == int(EntryStatus.PRICE_LIMIT_CONSTRAINT) else "DATA_MISSING"
            rows.append({**base, "day_index": day_index, "status": status}); continue
        ev = int(out["event_type"].at[sd, sid])
        tday = _f(out["target_first_hit_day"].at[sd, sid]); sday = _f(out["stop_first_hit_day"].at[sd, sid])
        if ev == int(Event.TARGET):
            status, hit = "TARGET", tday
        elif ev == int(Event.STOP):
            status, hit = "STOP", sday
        elif ev == int(Event.STOP_AMBIGUOUS):
            status, hit = "STOP_AMBIGUOUS", tday
        else:
            status, hit = ("TIMEOUT" if day_index >= K else "LIVE"), None
        led = ledger.get((sd, sid))
        if led is not None and led in _EVENT_STATUS:
            status = _EVENT_STATUS[led]
        rows.append({**base, "day_index": day_index, "status": status,
                     "hit_day": int(hit) if hit is not None else None,
                     "ret_now": _f(out[f"return_{K}d"].at[sd, sid]),
                     "mfe": _f(out[f"mfe_{K}d"].at[sd, sid]), "mae": _f(out[f"mae_{K}d"].at[sd, sid])})
    return rows


def summarize(rows: list[dict]) -> dict:
    s = [r["status"] for r in rows]
    return {"n": len(s), "target": s.count("TARGET"), "stop": s.count("STOP") + s.count("STOP_AMBIGUOUS"),
            "timeout": s.count("TIMEOUT"), "live": s.count("LIVE"), "pending": s.count("PENDING_ENTRY")}
