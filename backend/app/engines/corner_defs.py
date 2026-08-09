"""高確信角落：原子條件定義（研究腳本與線上引擎共用，杜絕定義漂移）。

原子 = 對特徵 DataFrame 的向量化布林條件。特徵欄位名與研究快取
（scripts/pop_condition_judge.py 的 _build_cache）完全一致；線上引擎
（corners.py）負責把當日特徵算成同名欄位。

spec 格式：
  {"op": "gt"|"lt"|"le"|"truthy"|"col_gt"|"col_lt"|"rank_ge"|"rank_le",
   "f": 特徵欄, "v": 門檻, "g": 對照欄(col_*), "q": 分位(rank_*)}
rank_* 為「當日橫斷面百分位」——年代漂移軸（估值/強弱/券資）用相對量尺，
機制軸（ATR/大盤水位）用絕對量尺（挖掘定論：混合量尺）。

角落清單本身在 data/corners.json（pop_vol_corners.py --export 產出、凍結的
挖掘產物），這裡只放原子與求值器。
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

# 流動性底濾（與挖掘一致）：20 日均量 ≥ 500 張
LIQ_MIN_VOL = 500 * 1000

# 名稱 → spec；與 scripts/pop_vol_corners.py 的原子一一對應
ATOM_SPECS: dict[str, dict] = {
    # 錨（燃料軸，絕對）
    "atr>6": {"op": "gt", "f": "atr_pct", "v": 0.06},
    "atr>8": {"op": "gt", "f": "atr_pct", "v": 0.08},
    "atr>10": {"op": "gt", "f": "atr_pct", "v": 0.10},
    # 均線/位置結構
    "站上MA20": {"op": "gt", "f": "c_over_ma20", "v": 0.0},
    "破MA20-8%": {"op": "lt", "f": "c_over_ma20", "v": -0.08},
    "破MA20-12%": {"op": "lt", "f": "c_over_ma20", "v": -0.12},
    "MA20上揚": {"op": "truthy", "f": "ma20_up5"},
    "站上MA60": {"op": "col_gt", "f": "close", "g": "ma60"},
    "破年線": {"op": "col_lt", "f": "close", "g": "ma240"},
    "均線多排": {"op": "gt", "f": "ma_align", "v": 2.5},
    "均線空排": {"op": "lt", "f": "ma_align", "v": 0.5},
    "52w高位>0.8": {"op": "gt", "f": "pos_52w", "v": 0.8},
    "52w低位<0.15": {"op": "lt", "f": "pos_52w", "v": 0.15},
    "距60高<-20": {"op": "lt", "f": "dist_60d_high", "v": -20.0},
    "距60高<-30": {"op": "lt", "f": "dist_60d_high", "v": -30.0},
    "乖離20<-8": {"op": "lt", "f": "bias_20", "v": -8.0},
    "乖離60<-20": {"op": "lt", "f": "bias_60", "v": -20.0},
    "乖離60>20": {"op": "gt", "f": "bias_60", "v": 20.0},
    # 動能
    "5日跌>8": {"op": "lt", "f": "ret5", "v": -8.0},
    "5日漲>8": {"op": "gt", "f": "ret5", "v": 8.0},
    "20日跌>20": {"op": "lt", "f": "ret20", "v": -20.0},
    "20日漲>20": {"op": "gt", "f": "ret20", "v": 20.0},
    "相對弱20<-10": {"op": "lt", "f": "rel_ret20", "v": -10.0},
    "相對強20>15": {"op": "gt", "f": "rel_ret20", "v": 15.0},
    "KD<20": {"op": "lt", "f": "kd_k", "v": 20.0},
    "KD>85": {"op": "gt", "f": "kd_k", "v": 85.0},
    # 量能
    "爆量>2.5": {"op": "gt", "f": "vol_ratio", "v": 2.5},
    "量枯<0.4": {"op": "lt", "f": "vol_ratio", "v": 0.4},
    "量勢升>1.5": {"op": "gt", "f": "vol_trend", "v": 1.5},
    "量勢縮<0.6": {"op": "lt", "f": "vol_trend", "v": 0.6},
    # 籌碼
    "外資5日買": {"op": "gt", "f": "inst_f5", "v": 0.0},
    "法人連買≥4": {"op": "gt", "f": "inst_streak", "v": 3.5},
    "券資比>10": {"op": "gt", "f": "sq_ratio", "v": 10.0},
    "券暴增>50": {"op": "gt", "f": "short_chg5", "v": 50.0},
    "資減>8": {"op": "lt", "f": "margin_chg5", "v": -8.0},
    "資增>10": {"op": "gt", "f": "margin_chg5", "v": 10.0},
    # 類股
    "類股寬度<20": {"op": "lt", "f": "sec_breadth", "v": 20.0},
    "同類群漲>12": {"op": "gt", "f": "peer_surge5", "v": 12.0},
    # 大盤情境（恐慌軸，絕對）
    "大盤20日崩≤-8": {"op": "le", "f": "mkt_ret20", "v": -8.0},
    "大盤深崩≤-6": {"op": "le", "f": "mkt_bias60", "v": -6.0},
    "大盤20日跌≤-4": {"op": "lt", "f": "mkt_ret20", "v": -4.0},
    "大盤平穩>-4": {"op": "gt", "f": "mkt_ret20", "v": -4.0},
    # 相對原子（年代漂移軸，當日排名）
    "R:PB前10%": {"op": "rank_ge", "f": "pb", "q": 0.90},
    "R:PB前5%": {"op": "rank_ge", "f": "pb", "q": 0.95},
    "R:PB後10%": {"op": "rank_le", "f": "pb", "q": 0.10},
    "R:PE前10%": {"op": "rank_ge", "f": "pe", "q": 0.90},
    "R:PE前5%": {"op": "rank_ge", "f": "pe", "q": 0.95},
    "R:PE後10%": {"op": "rank_le", "f": "pe", "q": 0.10},
    "R:相對強前10%": {"op": "rank_ge", "f": "rel_ret20", "q": 0.90},
    "R:相對強前5%": {"op": "rank_ge", "f": "rel_ret20", "q": 0.95},
    "R:相對強後10%": {"op": "rank_le", "f": "rel_ret20", "q": 0.10},
    "R:券資前10%": {"op": "rank_ge", "f": "sq_ratio", "q": 0.90},
    "R:券資前5%": {"op": "rank_ge", "f": "sq_ratio", "q": 0.95},
    "R:券資後10%": {"op": "rank_le", "f": "sq_ratio", "q": 0.10},
    "R:距60高前10%": {"op": "rank_ge", "f": "dist_60d_high", "q": 0.90},
    "R:距60高前5%": {"op": "rank_ge", "f": "dist_60d_high", "q": 0.95},
    "R:距60高後10%": {"op": "rank_le", "f": "dist_60d_high", "q": 0.10},
}


def eval_atom(df: pd.DataFrame, name: str,
              ranks: dict[str, pd.Series] | None = None) -> np.ndarray:
    """在特徵 DataFrame 上求單一原子的布林向量。NaN 一律視為不通過。

    ranks：rank_* 原子用的「當日橫斷面百分位」快取（多日資料時呼叫端先
    groupby("date").rank(pct=True) 算好傳入；單日資料可省略、就地計算）。
    """
    s = ATOM_SPECS[name]
    op = s["op"]
    if op in ("rank_ge", "rank_le"):
        if ranks is not None and s["f"] in ranks:
            r = ranks[s["f"]]
        elif "date" in df.columns and df["date"].nunique() > 1:
            r = df.groupby("date")[s["f"]].rank(pct=True)
        else:
            r = df[s["f"]].rank(pct=True)
        arr = r.to_numpy(dtype=float)
        return (arr >= s["q"]) if op == "rank_ge" else \
            np.where(np.isnan(arr), False, arr <= s["q"])
    if op == "truthy":
        return df[s["f"]].fillna(False).to_numpy(dtype=bool)
    if op == "col_gt":
        a = df[s["f"]].to_numpy(dtype=float)
        b = df[s["g"]].to_numpy(dtype=float)
        return np.nan_to_num(a, nan=-1e18) > np.nan_to_num(b, nan=1e18)
    if op == "col_lt":
        a = df[s["f"]].to_numpy(dtype=float)
        b = df[s["g"]].to_numpy(dtype=float)
        return np.nan_to_num(a, nan=1e18) < np.nan_to_num(b, nan=-1e18)
    x = df[s["f"]].to_numpy(dtype=float)
    if op == "gt":
        return np.nan_to_num(x, nan=-1e18) > s["v"]
    if op == "lt":
        return np.nan_to_num(x, nan=1e18) < s["v"]
    if op == "le":
        return np.nan_to_num(x, nan=1e18) <= s["v"]
    raise ValueError(f"unknown op: {op}")


def eval_corner(df: pd.DataFrame, atoms: list[str],
                ranks: dict[str, pd.Series] | None = None) -> np.ndarray:
    """角落 = 原子合取 ∧ 流動性底濾。"""
    mask = (df["vol_ma20"].fillna(0) >= LIQ_MIN_VOL).to_numpy()
    for a in atoms:
        mask &= eval_atom(df, a, ranks)
    return mask


_CORNERS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "corners.json")


def load_corners() -> list[dict]:
    """讀凍結的角落清單（挖掘產物）。檔案不存在回空清單（影子軌未啟用）。"""
    if not os.path.exists(_CORNERS_PATH):
        return []
    with open(_CORNERS_PATH, encoding="utf-8") as fh:
        return json.load(fh)["corners"]
