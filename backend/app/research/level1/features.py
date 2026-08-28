"""Level 1 Feature Engineering v1（FRS §10）——只用價量，嚴格 Information ≤ T。

第一輪特徵刻意精簡（Simple Before Complex）：Price/Momentum、Volatility、
Volume/Liquidity 三族共 10 個，全部由 close/volume 矩陣以「含當日的回看視窗」
計算——預測時點是 T 收盤後，T 日收盤價量可用。

模型端一律先做**每日橫斷面 rank 轉換**（rank_transform）再進模型：
- 只用同日資訊，無時間洩漏；
- 對未還原權息造成的離群報酬穩健（與 percentile target 同哲學）；
- 各特徵量尺統一，線性模型不需再調 scale。

基本面特徵（月營收/季報）後續加入時，必須經 app/services/pit_fundamentals 的
avail 欄做 as-of 對齊，不得直接以所屬期間對齊。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def build_price_features(close: pd.DataFrame, volume: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """回傳 {特徵名: 矩陣}。所有視窗皆回看（含 T 日），無未來資訊。"""
    ret1 = close.pct_change(fill_method=None)
    feats: dict[str, pd.DataFrame] = {}

    # Momentum
    feats["ret1"] = ret1
    feats["ret5"] = close.pct_change(5, fill_method=None)
    feats["ret20"] = close.pct_change(20, fill_method=None)
    feats["ret60"] = close.pct_change(60, fill_method=None)
    # 經典動能（略過近 5 日的 20 日動能，避開短期反轉）
    feats["ret20_ex5"] = close.shift(5) / close.shift(20) - 1

    # Volatility
    feats["vol20"] = ret1.rolling(20, min_periods=10).std()
    feats["vol60"] = ret1.rolling(60, min_periods=30).std()

    # 位置 / 趨勢
    feats["pos240"] = close / close.rolling(240, min_periods=60).max() - 1
    feats["bias20"] = close / close.rolling(20, min_periods=10).mean() - 1

    # Volume / Liquidity
    v5 = volume.rolling(5, min_periods=3).mean()
    v60 = volume.rolling(60, min_periods=20).mean()
    feats["vr5_60"] = v5 / v60
    feats["dollar_vol20"] = np.log1p((close * volume).rolling(20, min_periods=10).mean())

    return feats


def rank_transform(feats: dict[str, pd.DataFrame], in_universe: pd.DataFrame,
                   ) -> dict[str, pd.DataFrame]:
    """每日橫斷面 rank → (0,1]，U_t 外設 NaN。缺值不補（由資料集組裝端決定）。"""
    return {k: v.where(in_universe).rank(axis=1, pct=True).astype("float32")
            for k, v in feats.items()}


def _pit_long_to_matrix(long_: pd.DataFrame, value_col: str,
                        index: pd.Index, columns: pd.Index) -> pd.DataFrame:
    """PIT 長表（stock_id, avail, value）→ 日矩陣：avail 日生效、其後 ffill。

    avail 可能落在非交易日（法定期限是日曆日），先聯集 reindex 再 ffill 取交易日。
    同股同 avail 多列（極少見）取「後一期」（長表已依期間升冪）。
    """
    mat = long_.pivot_table(index="avail", columns="stock_id", values=value_col,
                            aggfunc="last")
    mat.index = pd.to_datetime(mat.index).astype(str)
    union = mat.index.union(index)
    return mat.reindex(union).ffill().reindex(index=index, columns=columns)


def build_fundamental_features(session, index: pd.Index, columns: pd.Index,
                               ) -> dict[str, pd.DataFrame]:
    """基本面特徵（嚴格經 pit_fundamentals 的 avail 做 as-of 對齊）。

    月營收：rev_yoy／rev_yoy_chg（YoY 加速）／rev_yoy3（3 月均 YoY）
    季報：eps_yoy_d（單季 EPS 年增差額；差額比比率穩健，EPS 過零不炸）／gm_chg（毛利率 QoQ）
    """
    from app.services import pit_fundamentals as pit

    rev = pit.load_revenue_pit(session).sort_values(["stock_id", "year", "month"])
    g = rev.groupby("stock_id", sort=False)
    rev["rev_yoy"] = rev["yoy"]
    rev["rev_yoy_chg"] = g["yoy"].diff()
    rev["rev_yoy3"] = g["yoy"].transform(lambda s: s.rolling(3, min_periods=2).mean())

    fin = pit.load_financials_pit(session).sort_values(["stock_id", "year", "quarter"])
    gf = fin.groupby("stock_id", sort=False)
    fin["eps_yoy_d"] = gf["eps"].diff(4)
    fin["gm_chg"] = gf["gross_margin"].diff()

    out: dict[str, pd.DataFrame] = {}
    for col in ("rev_yoy", "rev_yoy_chg", "rev_yoy3"):
        out[col] = _pit_long_to_matrix(rev, col, index, columns)
    for col in ("eps_yoy_d", "gm_chg"):
        out[col] = _pit_long_to_matrix(fin, col, index, columns)
    return out


def build_regime_interactions(ranked: dict[str, pd.DataFrame], mkt_close: pd.Series,
                              keys: tuple[str, ...] = ("vol20", "ret5", "ret20", "bias20"),
                              ) -> dict[str, pd.DataFrame]:
    """市場 regime 交互項：0.5 + (rank − 0.5) × I(大盤20日報酬>0)。

    市場層特徵在橫斷面內是常數、rank 後無資訊，只能以交互方式進模型——
    讓線性模型的斜率能隨多空 regime 翻轉（Ridge v1 診斷出的防禦型反轉問題）。
    以 0.5 為中心：空頭日交互項=0.5（中性），與 assemble 的缺值補 0.5 語意一致。
    """
    ind = (mkt_close.pct_change(20) > 0).astype(float).reindex(
        next(iter(ranked.values())).index).ffill().fillna(0.0)
    out: dict[str, pd.DataFrame] = {}
    for k in keys:
        out[f"{k}_bull"] = (0.5 + ranked[k].sub(0.5).mul(ind, axis=0)).astype("float32")
    return out


def assemble_for_dates(ranked: dict[str, pd.DataFrame], in_universe: pd.DataFrame,
                       dates: pd.Index) -> tuple[np.ndarray, pd.DataFrame]:
    """預測用組裝：列 = 指定日期 U_t 內全部股票（不需 target 存在）。

    與 assemble_dataset 的差別：訓練列由「target 非 NaN」定義，預測列由
    「在 Universe 內」定義——當日 target 必然缺值，不能沿用訓練組裝。
    """
    names = list(ranked)
    mask_long = in_universe.loc[dates].stack(future_stack=True)
    idx = mask_long[mask_long.fillna(False).astype(bool)].index
    cols = [ranked[k].loc[dates].stack(future_stack=True).reindex(idx).to_numpy()
            for k in names]
    x = np.nan_to_num(np.column_stack(cols).astype(np.float32), nan=0.5)
    meta = idx.to_frame(index=False)
    meta.columns = ["date", "stock_id"]
    return x, meta


def assemble_dataset(ranked: dict[str, pd.DataFrame], target_pct: pd.DataFrame,
                     dates: pd.Index) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    """指定日期集合 → (X, y, index)。

    列 = (date, stock) 且 target 非 NaN；特徵缺值補 0.5（橫斷面中位，rank 空間的中性值）。
    回傳 index（date, stock_id 兩欄）供把預測攤回矩陣。
    """
    names = list(ranked)
    y_long = target_pct.loc[dates].stack(future_stack=True).dropna()  # 只留 target 非 NaN 列
    idx = y_long.index
    cols = []
    for k in names:
        cols.append(ranked[k].loc[dates].stack(future_stack=True).reindex(idx).to_numpy())
    x = np.column_stack(cols).astype(np.float32)
    x = np.nan_to_num(x, nan=0.5)
    meta = idx.to_frame(index=False)
    meta.columns = ["date", "stock_id"]
    return x, y_long.to_numpy(dtype=np.float32), meta
