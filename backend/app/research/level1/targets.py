"""Level 1 Target Generator（FRS §5–7、附錄 A）。

R(i,t,N) = P(i,t+N) / P(i,t) − 1          （Close-to-Close，交易日計）
Y(i,t,N) = Percentile(R(i,t,N) | U_t)      （同日 Universe 內橫斷面百分位, (0,1]）

- t+N 是「交易日」位移：在全市場交易日曆（close 矩陣的 index 聯集）上 shift(-N)。
  個股停牌日該股不在 U_t，但日曆仍前進。
- P(i,t+N) 缺值（觀測窗未到、下市、停牌）→ fwd/pct 為 NaN，不進百分位分母。
  下市股其 t+N 已無價的預測日因此被排除——「無還原終值」的資料現實，
  缺值列數由建置報告監控。
- 百分位用 rank(pct=True, method='average')，同值同分。
- 輸出為矩陣制（index=date, columns=stock_id, float32）：IC / 分位分析直接可用，
  體積也比 2 千萬列長表小一個量級。

排除事項（§6）由構造保證：不用未來 High/Low、不做 0/1 簡化、不用組合結果。

已知限制（文件化）：
- close 為原始價（未還原權息）。全市場股利資料不可得（dividends 表僅自選股），
  除息跳空會壓低該股當期 fwd_ret；台股除息集中 7–9 月，屬系統性雜訊。
  日後若回補還原價，只需換 close 來源，本模組不動。
"""

from __future__ import annotations

import pandas as pd

HORIZONS = (1, 3, 5, 10, 20, 60)


def forward_returns(close: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """N 交易日 Close-to-Close 報酬矩陣（index=t, columns=stock_id）。"""
    return close.shift(-horizon) / close - 1


def cross_sectional_pct(fwd: pd.DataFrame, in_universe: pd.DataFrame) -> pd.DataFrame:
    """逐日把 U_t 內、fwd 非 NaN 的股票轉橫斷面百分位 (0,1]。"""
    return fwd.where(in_universe).rank(axis=1, pct=True, method="average")


def build_targets(close: pd.DataFrame, in_universe: pd.DataFrame,
                  horizons: tuple[int, ...] = HORIZONS,
                  ) -> dict[int, dict[str, pd.DataFrame]]:
    """每個 horizon 產 {"fwd": 報酬矩陣, "pct": 百分位矩陣}（皆已套 U_t 遮罩, float32）。"""
    out: dict[int, dict[str, pd.DataFrame]] = {}
    for n in horizons:
        fwd = forward_returns(close, n)
        pct = cross_sectional_pct(fwd, in_universe)
        out[n] = {
            "fwd": fwd.where(in_universe).astype("float32"),
            "pct": pct.astype("float32"),
        }
    return out


def to_long(built: dict[int, dict[str, pd.DataFrame]]) -> pd.DataFrame:
    """矩陣 → 長表（date, stock_id, horizon, fwd_ret, pct）。小範圍檢視/測試用。"""
    frames = []
    for n, m in built.items():
        f = m["fwd"].stack().rename("fwd_ret")
        p = m["pct"].stack().rename("pct")
        df = pd.concat([f, p], axis=1).reset_index()
        df.columns = ["date", "stock_id", "fwd_ret", "pct"]
        df["horizon"] = n
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    return out[["date", "stock_id", "horizon", "fwd_ret", "pct"]]
