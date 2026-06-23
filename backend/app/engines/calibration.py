"""回測共用資料載入 helpers（point-in-time 分批串流）。

原本是 CalibrationEngine（驗證「分數越高越會漲」的會漲論）所在；進場推薦重定錨為
「會噴」後，會漲論的回測（校準/逐筆期望值/參數掃描）已退役，只保留這裡的資料載入
helpers 供 PoppabilityEfficacyEngine（會噴成效回測）共用。

回測窗資料若一次 select(model).all() 會把多表 ORM（百萬列）同時載進記憶體 → OOM；
故依股票分批、用欄位投影（Row tuple 不進 identity map）串流載入，峰值記憶體 ~
一批股票 × 窗長，而非全表。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..storage import models

_MIN_BARS = 60  # 與 ListedLongEnoughFilter 一致

# 載入的欄位（與 ScoringEngine 對齊）。
_PRICE_COLS = ["stock_id", "date", "open", "high", "low", "close", "volume"]
_IND_COLS = [
    "stock_id", "date", "ma5", "ma10", "ma20", "ma60", "vol_ma5", "vol_ma20",
    "kd_k", "kd_d", "macd", "macd_signal", "macd_hist", "atr14", "bias_20", "bias_60",
]
_INST_COLS = ["stock_id", "date", "foreign_net", "trust_net", "dealer_net", "total_net"]
_MARGIN_COLS = ["stock_id", "date", "margin_balance", "margin_change", "short_balance", "short_change"]

_STOCK_BATCH = 300  # 每批處理的股票數（與 IndicatorEngine 一致），限制峰值記憶體


def _group_ids(
    session: Session, model, cols: list[str], ids: list[str], date_lo: date
) -> dict[str, pd.DataFrame]:
    """載入指定股票、date≥date_lo 的某表，依 stock_id 分組（升冪）。

    用欄位投影（select 欄位 → Row tuple），不走 ORM entity，避免列物件累積進 Session
    identity map 而讓分批失去意義（與 IndicatorEngine 同手法）。
    """
    rows = session.execute(
        select(*[getattr(model, c) for c in cols])
        .where(model.stock_id.in_(ids), model.date >= date_lo)
        .order_by(model.stock_id, model.date)
    ).all()
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=cols)
    return {sid: g.reset_index(drop=True) for sid, g in df.groupby("stock_id", sort=False)}


def _iter_stock_groups(
    session: Session, stock_ids: list[str], date_lo: date, batch: int = _STOCK_BATCH
) -> Iterator[
    tuple[str, pd.DataFrame, pd.DataFrame | None, pd.DataFrame | None, pd.DataFrame | None]
]:
    """依股票分批串流載入 price/indicator/法人/融資券，逐檔 yield (sid, prices, inds, inst, margin)。

    每批只把 batch 檔的窗內資料載進記憶體，避免一次把六年×全市場多表全載而 OOM。
    無價量資料的股票直接略過（inds/inst/margin 可能為 None）。
    """
    for i in range(0, len(stock_ids), batch):
        ids = stock_ids[i : i + batch]
        prices = _group_ids(session, models.DailyPrice, _PRICE_COLS, ids, date_lo)
        inds = _group_ids(session, models.Indicator, _IND_COLS, ids, date_lo)
        inst = _group_ids(session, models.Institutional, _INST_COLS, ids, date_lo)
        margin = _group_ids(session, models.Margin, _MARGIN_COLS, ids, date_lo)
        for sid in ids:
            pdf = prices.get(sid)
            if pdf is not None:
                yield sid, pdf, inds.get(sid), inst.get(sid), margin.get(sid)
