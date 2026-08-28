"""Level 1 Prediction Ledger 純函式層（FRS §15）。

排名與成熟回填的計算邏輯放這裡（可單測），DB 讀寫留在 scripts.level1_predict。
"""

from __future__ import annotations

import pandas as pd


def rank_scores(scores: pd.Series) -> pd.DataFrame:
    """單日單 horizon 的分數 → rank（1=最強）與 pct_rank（1.0=最強）。

    index=stock_id。同分以股號序穩定切割（method='first'），確保可重現。
    """
    s = scores.dropna().sort_index()
    rank = s.rank(ascending=False, method="first").astype(int)
    pct = 1.0 - (rank - 1) / len(s)
    return pd.DataFrame({"score": s, "rank": rank, "pct_rank": pct,
                         "universe_size": len(s)})


def compute_actuals(preds: pd.DataFrame, close: pd.DataFrame,
                    horizon: int) -> pd.DataFrame:
    """成熟回填：對單一 horizon 的未成熟 ledger 列計算 actual_*。

    preds 欄位需含 prediction_date(str), stock_id, pct_rank。
    只處理 t+N 已在 close 日曆內的預測日；actual_pct 在「同日同 horizon 有實現
    報酬的 ledger 股票」內取百分位（與 targets.cross_sectional_pct 同語意——
    U_t 內 fwd 非 NaN 者為分母）。回傳含 actual_return / actual_pct / rank_error
    的列（未成熟或無終值的列不回傳）。
    """
    dates = close.index
    pos = {d: i for i, d in enumerate(dates)}
    out = []
    for d, g in preds.groupby("prediction_date"):
        i = pos.get(str(d))
        if i is None or i + horizon >= len(dates):
            continue  # 觀測窗未到
        p0 = close.iloc[i]
        p1 = close.iloc[i + horizon]
        ret = (p1 / p0 - 1).reindex(g["stock_id"])
        g = g.assign(actual_return=ret.to_numpy()).dropna(subset=["actual_return"])
        if g.empty:
            continue
        g["actual_pct"] = g["actual_return"].rank(pct=True, method="average")
        g["rank_error"] = (g["pct_rank"] - g["actual_pct"]).abs()
        out.append(g)
    if not out:
        return preds.iloc[0:0].assign(actual_return=pd.NA, actual_pct=pd.NA,
                                      rank_error=pd.NA)
    return pd.concat(out, ignore_index=True)
