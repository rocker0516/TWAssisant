"""IndicatorEngine（架構③第一棒）：daily_prices → indicators 表。

純 pandas 手算（透明可維護、無額外依賴）。指標集對齊 models.Indicator：
均線 ma5/10/20/60、量能均線 vol_ma5/20、KD(9)、MACD(12,26,9)、ATR14、乖離 bias_20/60。

冪等：每次重算全歷史再 upsert 覆寫（本機資料量可接受；日後可只算近窗）。
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..storage import models
from ..storage.repositories import BaseRepository
from .base import BaseEngine

_OUT_COLS = [
    "stock_id", "date", "ma5", "ma10", "ma20", "ma60", "vol_ma5", "vol_ma20",
    "kd_k", "kd_d", "macd", "macd_signal", "macd_hist", "atr14", "bias_20", "bias_60",
]


def compute_one(df: pd.DataFrame) -> pd.DataFrame:
    """單一個股（已依日期升冪）價格 df → 指標 df。"""
    close, high, low, vol = df["close"], df["high"], df["low"], df["volume"].astype("float")
    out = pd.DataFrame({"stock_id": df["stock_id"], "date": df["date"]})

    for n in (5, 10, 20, 60):
        out[f"ma{n}"] = close.rolling(n).mean()
    out["vol_ma5"] = vol.rolling(5).mean()
    out["vol_ma20"] = vol.rolling(20).mean()

    # KD(9)：RSV → K、D 以 1/3 遞迴平滑（ewm alpha=1/3）
    low9 = low.rolling(9).min()
    high9 = high.rolling(9).max()
    rng = (high9 - low9).replace(0, pd.NA)
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

    def run(self, session: Session, trading_date: date) -> dict:
        rows = session.execute(
            select(
                models.DailyPrice.stock_id,
                models.DailyPrice.date,
                models.DailyPrice.open,
                models.DailyPrice.high,
                models.DailyPrice.low,
                models.DailyPrice.close,
                models.DailyPrice.volume,
            ).order_by(models.DailyPrice.stock_id, models.DailyPrice.date)
        ).all()
        if not rows:
            return {"status": "empty"}

        df = pd.DataFrame(rows, columns=["stock_id", "date", "open", "high", "low", "close", "volume"])
        df = df.dropna(subset=["close"])

        parts = [compute_one(g) for _, g in df.groupby("stock_id", sort=False) if len(g) >= 5]
        if not parts:
            return {"status": "empty"}
        result = pd.concat(parts, ignore_index=True)

        # 全 NaN 的領先列（早期不足窗）丟掉再落庫
        ind_cols = [c for c in _OUT_COLS if c not in ("stock_id", "date")]
        result = result.dropna(how="all", subset=ind_cols)
        records = (
            result[_OUT_COLS].astype(object).where(pd.notna(result[_OUT_COLS]), None).to_dict("records")
        )

        repo = BaseRepository(models.Indicator)
        n = repo.upsert_many(session, records)
        session.flush()
        return {"status": "ok", "rows": n, "stocks": result["stock_id"].nunique()}
