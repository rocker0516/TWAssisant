"""IndicatorEngine（架構③第一棒）：daily_prices → indicators 表。

純 pandas 手算（透明可維護、無額外依賴）。指標集對齊 models.Indicator：
均線 ma5/10/20/60、量能均線 vol_ma5/20、KD(9)、MACD(12,26,9)、ATR14、乖離 bias_20/60。

增量：只處理「價格日期比指標新」的股票，載入暖身窗（近 _WARMUP_DAYS 日曆天，
涵蓋 ma240 + ewm 收斂所需的 ~400 交易日），只 upsert 比該股既有指標新的列。
K 線回補到 2020 後全量重算+重寫 300 萬列一次要 7 分鐘，增量後秒級。
ewm 指標（KD/MACD/ATR）理論上吃全歷史，但 400 交易日暖身後與全量差異在 1e-13
量級，數值上等同。冷啟動（該股無任何指標）仍載全歷史。
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..storage import models
from ..storage.repositories import BaseRepository
from .base import BaseEngine

_OUT_COLS = [
    "stock_id", "date", "ma5", "ma10", "ma20", "ma60", "ma120", "ma240",
    "vol_ma5", "vol_ma20",
    "kd_k", "kd_d", "macd", "macd_signal", "macd_hist", "atr14", "bias_20", "bias_60",
]


def compute_one(df: pd.DataFrame) -> pd.DataFrame:
    """單一個股（已依日期升冪）價格 df → 指標 df。"""
    close, high, low, vol = df["close"], df["high"], df["low"], df["volume"].astype("float")
    out = pd.DataFrame({"stock_id": df["stock_id"], "date": df["date"]})

    for n in (5, 10, 20, 60, 120, 240):
        out[f"ma{n}"] = close.rolling(n).mean()
    out["vol_ma5"] = vol.rolling(5).mean()
    out["vol_ma20"] = vol.rolling(20).mean()

    # KD(9)：RSV → K、D 以 1/3 遞迴平滑（ewm alpha=1/3）
    low9 = low.rolling(9).min()
    high9 = high.rolling(9).max()
    # 平盤窗（high9==low9）→ 0；用 np.nan 而非 pd.NA，後續 astype("float") 才不會
    # 在含缺值序列上炸（pd.NAType 無法轉 float）。
    rng = (high9 - low9).replace(0, np.nan)
    rsv = ((close - low9) / rng * 100).astype("float")
    out["kd_k"] = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    out["kd_d"] = out["kd_k"].ewm(alpha=1 / 3, adjust=False).mean()

    # MACD(12,26,9)：DIF、訊號線、柱
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    out["macd"] = dif
    out["macd_signal"] = dif.ewm(span=9, adjust=False).mean()
    out["macd_hist"] = out["macd"] - out["macd_signal"]

    # ATR14（Wilder 平滑）
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    out["atr14"] = tr.ewm(alpha=1 / 14, adjust=False).mean()

    out["bias_20"] = (close - out["ma20"]) / out["ma20"] * 100
    out["bias_60"] = (close - out["ma60"]) / out["ma60"] * 100
    return out


class IndicatorEngine(BaseEngine):
    name = "indicator"

    # 一次處理 N 檔。全市場 ×多年一次載入會吃爆記憶體（OOM），故依股票分批串流。
    _BATCH = 300
    # 暖身窗（日曆天）≈ 410 交易日：ma240 需 240 交易日、ewm 再收斂 ~150 日即誤差 <1e-13。
    _WARMUP_DAYS = 600

    def run(self, session: Session, trading_date: date, *, full: bool = False) -> dict:
        """full=True 強制全量重算（回補「更舊的」歷史價格後用；日常增量即可）。"""
        price_max = dict(
            session.execute(
                select(models.DailyPrice.stock_id, func.max(models.DailyPrice.date))
                .group_by(models.DailyPrice.stock_id)
            ).all()
        )
        if not price_max:
            return {"status": "empty"}
        ind_max: dict = {} if full else dict(
            session.execute(
                select(models.Indicator.stock_id, func.max(models.Indicator.date))
                .group_by(models.Indicator.stock_id)
            ).all()
        )

        # 只處理有新價格的股票（下市/停更股 price==indicator max → 直接跳過）
        work_ids = sorted(
            s for s, pmax in price_max.items()
            if ind_max.get(s) is None or pmax > ind_max[s]
        )
        if not work_ids:
            return {"status": "ok", "rows": 0, "stocks": 0, "note": "up_to_date"}

        repo = BaseRepository(models.Indicator)
        ind_cols = [c for c in _OUT_COLS if c not in ("stock_id", "date")]
        total = 0
        stocks = 0
        for i in range(0, len(work_ids), self._BATCH):
            ids = work_ids[i : i + self._BATCH]
            # 批內暖身起點：既有指標最舊的續算點再往前 _WARMUP_DAYS；含冷啟動股則載全歷史
            batch_cutoffs = [ind_max[s] for s in ids if ind_max.get(s) is not None]
            q = (
                select(
                    models.DailyPrice.stock_id,
                    models.DailyPrice.date,
                    models.DailyPrice.open,
                    models.DailyPrice.high,
                    models.DailyPrice.low,
                    models.DailyPrice.close,
                    models.DailyPrice.volume,
                )
                .where(models.DailyPrice.stock_id.in_(ids))
                .order_by(models.DailyPrice.stock_id, models.DailyPrice.date)
            )
            if len(batch_cutoffs) == len(ids):
                warm_start = min(batch_cutoffs) - timedelta(days=self._WARMUP_DAYS)
                q = q.where(models.DailyPrice.date >= warm_start)
            rows = session.execute(q).all()
            if not rows:
                continue

            df = pd.DataFrame(
                rows, columns=["stock_id", "date", "open", "high", "low", "close", "volume"]
            )
            df = df.dropna(subset=["close"])
            parts = [compute_one(g) for _, g in df.groupby("stock_id", sort=False) if len(g) >= 5]
            if not parts:
                continue
            result = pd.concat(parts, ignore_index=True)

            # 只留「比該股既有指標新」的列（冷啟動股全留）
            cutoff = result["stock_id"].map(ind_max)
            result = result[cutoff.isna() | (result["date"] > cutoff)]
            # 全 NaN 的領先列（早期不足窗）丟掉再落庫
            result = result.dropna(how="all", subset=ind_cols)
            if result.empty:
                continue
            records = (
                result[_OUT_COLS]
                .astype(object)
                .where(pd.notna(result[_OUT_COLS]), None)
                .to_dict("records")
            )
            total += repo.upsert_many(session, records)
            stocks += result["stock_id"].nunique()
            session.flush()

        if total == 0:
            return {"status": "ok", "rows": 0, "stocks": 0, "note": "up_to_date"}
        return {"status": "ok", "rows": total, "stocks": stocks}
