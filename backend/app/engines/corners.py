"""高確信角落影子軌引擎（實驗）。

每日盤後對全市場評估 data/corners.json 的角落（挖掘凍結產物），命中寫入
corner_signals 供前端影子區塊顯示與 forward 驗證累積。
不影響既有排序/推薦——純標籤層。

特徵計算逐項復刻研究快取（scripts/pop_condition_judge.py::_build_cache）：
row-based rolling、法人合併到價格軸後 fillna(0)、融資券 ffill、
sq_ratio/chg5 同 clip。已知細微差異（可接受）：
  - pe/pb 用近 30 天內最新一筆（scoring PIT 慣例）；研究是全史 ffill
  - 融資券斷料 >60 天引擎給 NaN（原子不通過）；研究會 ffill 舊值
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..storage import models
from .base import BaseEngine
from .corner_defs import eval_corner, load_corners

_WINDOW = 250   # 交易日：pos_52w rolling(240) 需要
_CHIP_WIN = 60  # 法人/融資券窗（inst_streak≥4、chg5 足夠）


def _read_df(session: Session, stmt) -> pd.DataFrame:
    rows = session.execute(stmt).all()
    return pd.DataFrame(rows, columns=list(stmt.selected_columns.keys()))


def build_features(session: Session, td: date) -> pd.DataFrame:
    """算 td 當日全市場的角落特徵（欄名與研究快取一致）。"""
    axis = session.execute(
        select(models.DailyPrice.date).distinct()
        .where(models.DailyPrice.date <= td)
        .order_by(models.DailyPrice.date.desc()).limit(_WINDOW)
    ).scalars().all()
    if not axis or axis[0] != td:
        return pd.DataFrame()
    lo = axis[-1]
    chip_lo = axis[min(_CHIP_WIN, len(axis)) - 1]

    p = models.DailyPrice
    i = models.Indicator
    df = _read_df(session, select(
        p.stock_id, p.date, p.high, p.low, p.close, p.volume,
        i.ma5, i.ma10, i.ma20, i.ma60, i.ma240, i.vol_ma5, i.vol_ma20,
        i.kd_k, i.atr14, i.bias_20, i.bias_60,
    ).join(i, (i.stock_id == p.stock_id) & (i.date == p.date), isouter=True)
     .where(p.date >= lo, p.date <= td)
     .order_by(p.stock_id, p.date))
    if df.empty:
        return df

    df["atr_pct"] = df["atr14"] / df["close"]
    df["ma_align"] = ((df["ma5"] > df["ma10"]).astype(float)
                      + (df["ma10"] > df["ma20"]).astype(float)
                      + (df["ma20"] > df["ma60"]).astype(float))
    g = df.groupby("stock_id", sort=False)
    df["ret5"] = (df["close"] / g["close"].shift(5) - 1.0) * 100
    df["ret20"] = (df["close"] / g["close"].shift(20) - 1.0) * 100
    df["ma20_up5"] = df["ma20"] > g["ma20"].shift(5)
    df["vol_ratio"] = df["volume"] / df["vol_ma20"]
    df["vol_trend"] = df["vol_ma5"] / df["vol_ma20"]
    df["c_over_ma20"] = df["close"] / df["ma20"] - 1.0
    df["pos_52w"] = ((df["close"] - g["low"].transform(lambda s: s.rolling(240, 60).min()))
                     / (g["high"].transform(lambda s: s.rolling(240, 60).max())
                        - g["low"].transform(lambda s: s.rolling(240, 60).min()) + 1e-9))
    df["dist_60d_high"] = (df["close"] / g["high"].transform(
        lambda s: s.rolling(60, 20).max()) - 1.0) * 100

    # 籌碼族（窗內合併到價格軸，與研究同式）
    inst = _read_df(session, select(
        models.Institutional.stock_id, models.Institutional.date,
        models.Institutional.foreign_net, models.Institutional.total_net,
    ).where(models.Institutional.date >= chip_lo, models.Institutional.date <= td))
    marg = _read_df(session, select(
        models.Margin.stock_id, models.Margin.date,
        models.Margin.margin_balance, models.Margin.short_balance,
    ).where(models.Margin.date >= chip_lo, models.Margin.date <= td))
    df = df.merge(inst, on=["stock_id", "date"], how="left")
    df = df.merge(marg, on=["stock_id", "date"], how="left")
    for c in ("foreign_net", "total_net"):
        df[c] = df[c].fillna(0.0)
    g = df.groupby("stock_id", sort=False)
    for c in ("margin_balance", "short_balance"):
        df[c] = g[c].ffill()
    g = df.groupby("stock_id", sort=False)
    volsum5 = g["volume"].transform(lambda s: s.rolling(5, 3).sum())
    df["inst_f5"] = g["foreign_net"].transform(lambda s: s.rolling(5, 3).sum()) / (volsum5 + 1e-9)
    pos = df["total_net"] > 0
    df["inst_streak"] = pos.groupby(
        [df["stock_id"], (~pos).groupby(df["stock_id"]).cumsum()]).cumsum()
    df["sq_ratio"] = (df["short_balance"] / (df["margin_balance"] + 1e-9)).clip(0, 5) * 100
    df["short_chg5"] = ((df["short_balance"] - g["short_balance"].shift(5))
                        / (g["short_balance"].shift(5) + 1e-9)).clip(-2, 5) * 100
    df["margin_chg5"] = ((df["margin_balance"] - g["margin_balance"].shift(5))
                         / (g["margin_balance"].shift(5) + 1e-9)).clip(-2, 5) * 100

    # 只留 td 當日列，之後全是橫斷面；排除 ETF（研究宇宙=非 ETF）
    day = df[df["date"] == td].copy()
    etf_ids = set(session.execute(select(models.EtfProfile.stock_id)).scalars().all())
    if etf_ids:
        day = day[~day["stock_id"].isin(etf_ids)]

    # 估值（近 30 天內最新一筆，PIT）
    val = _read_df(session, select(
        models.Valuation.stock_id, models.Valuation.date,
        models.Valuation.pe, models.Valuation.pb,
    ).where(models.Valuation.date <= td,
            models.Valuation.date >= axis[min(30, len(axis)) - 1])
     .order_by(models.Valuation.date))
    if not val.empty:
        val = val.drop_duplicates("stock_id", keep="last")[["stock_id", "pe", "pb"]]
        day = day.merge(val, on="stock_id", how="left")
    else:
        day["pe"] = np.nan
        day["pb"] = np.nan

    # 大盤情境
    mkt = _read_df(session, select(models.MarketIndex.date, models.MarketIndex.close)
                   .where(models.MarketIndex.date <= td)
                   .order_by(models.MarketIndex.date))
    closes = mkt["close"].to_numpy()
    day["mkt_bias60"] = ((closes[-1] / closes[-60:].mean() - 1.0) * 100
                         if len(closes) >= 60 else np.nan)
    day["mkt_ret20"] = ((closes[-1] / closes[-21] - 1.0) * 100
                        if len(closes) >= 21 else np.nan)

    # 類股情境（td 橫斷面，成分 <5 檔不給值）
    sec = _read_df(session, select(models.Stock.id.label("stock_id"), models.Stock.sector_id))
    day = day.merge(sec, on="stock_id", how="left")
    gs = day.groupby("sector_id")
    agg = gs.agg(sec_ret20=("ret20", "median"),
                 sec_breadth=("c_over_ma20", lambda s: (s > 0).mean()),
                 peer_surge5=("ret5", lambda s: (s > 10).mean()),
                 sec_n=("ret20", "size")).reset_index()
    agg.loc[agg["sec_n"] < 5, ["sec_ret20", "sec_breadth", "peer_surge5"]] = np.nan
    day = day.merge(agg.drop(columns=["sec_n"]), on="sector_id", how="left")
    day["rel_ret20"] = day["ret20"] - day["sec_ret20"]
    day["sec_breadth"] = day["sec_breadth"] * 100
    day["peer_surge5"] = day["peer_surge5"] * 100
    return day.reset_index(drop=True)


class CornerEngine(BaseEngine):
    """影子軌：評估角落 → corner_signals（冪等，重跑先清當日）。"""

    name = "corners"

    def run(self, session: Session, trading_date: date,
            only_ids: set[str] | None = None) -> dict:
        """only_ids：只重算這幾個角落（其餘當日訊號原封不動）。

        新增角落要補進既有影子期時用。整日重算雖然冪等，但若期間有資料修訂，
        會讓已累積的 forward 驗證紀錄在無聲中改變——影子軌的價值就在那份紀錄，
        所以補跑一律走外科式，只動指定的 corner_id。
        """
        corners = load_corners()
        if only_ids is not None:
            corners = [c for c in corners if c["id"] in only_ids]
        if not corners:
            return {"corners": 0, "signals": 0, "note": "corners.json 不存在或無指定角落"}
        day = build_features(session, trading_date)
        if day.empty:
            return {"corners": len(corners), "signals": 0, "note": "no data"}

        stmt = delete(models.CornerSignal).where(models.CornerSignal.date == trading_date)
        if only_ids is not None:
            stmt = stmt.where(models.CornerSignal.corner_id.in_(only_ids))
        session.execute(stmt)
        n_sig = 0
        fired = 0
        for c in corners:
            mask = eval_corner(day, c["atoms"])
            picks = day[mask]
            if len(picks):
                fired += 1
            for _, r in picks.iterrows():
                session.add(models.CornerSignal(
                    stock_id=r["stock_id"], date=trading_date,
                    corner_id=c["id"], close=float(r["close"])))
                n_sig += 1
        session.commit()
        return {"corners": len(corners), "fired": fired, "signals": n_sig}
