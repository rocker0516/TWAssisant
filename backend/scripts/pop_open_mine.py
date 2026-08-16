"""開放式高精度挖掘：不從既有標籤出發，全特徵原子 → 二元/三元合取掃描。

目標同 pop_high_precision_mine.py：找「一推薦就 ≥70%」的角落（30日摸+10%，
隔日高錨），允許空手。紀律：
  - 2021~2024 挖掘窗、評選=分年地板（各年 n≥15、至少 3 個挖掘年有樣本）
  - 2025/2026 僅軟檢查（holdout 已燒）
  - 多重比較風險用三道閘緩解：樣本門檻、Jaccard 去重（避免同角落變體灌榜）、
    最終候選需人工做門檻單調性檢查才算數
用法：.venv/bin/python scripts/pop_open_mine.py
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

sys.path.insert(0, __file__.replace("\\", "/").rsplit("/scripts/", 1)[0])
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from pop_condition_judge import _CACHE  # noqa: E402

MINE = (0, 1, 2, 3)  # 2021~2024

_RANGE_CACHE: dict = {}


def range_n(m: pd.DataFrame, n: int) -> np.ndarray:
    """近 n 日 (最高-最低)/收盤 振幅；依 stock_id 分組滾動。"""
    key = f"range{n}"
    if key not in _RANGE_CACHE:
        g = m.sort_values(["stock_id", "date"]).groupby("stock_id", sort=False)
        hi = g["high"].rolling(n, min_periods=n).max().reset_index(0, drop=True)
        lo = g["low"].rolling(n, min_periods=n).min().reset_index(0, drop=True)
        r = ((hi - lo) / m["close"]).reindex(m.index)
        _RANGE_CACHE[key] = np.nan_to_num(r.to_numpy(dtype=float), nan=1e18)
    return _RANGE_CACHE[key]


def coil_ratio(m: pd.DataFrame) -> np.ndarray:
    """盤整收斂比：近10日振幅 / 近30日振幅，越小越收斂。"""
    if "coil" not in _RANGE_CACHE:
        r10, r30 = range_n(m, 10), range_n(m, 30)
        with np.errstate(divide="ignore", invalid="ignore"):
            c = np.where((r30 > 0) & (r30 < 1e17), r10 / r30, 1e18)
        _RANGE_CACHE["coil"] = c
    return _RANGE_CACHE["coil"]
PAIR_FLOOR, PAIR_N = 58.0, 300
TRI_FLOOR, TRI_N = 68.0, 250
JACCARD_MAX = 0.5


def main() -> None:
    m = pd.read_pickle(_CACHE)
    m = m[m["date"] >= "2021-01-01"].reset_index(drop=True)
    year = m["date"].astype(str).str[:4].astype(int) - 2021
    ycode = year.to_numpy()
    hit = m["hit"].to_numpy(dtype=float)
    ndays = m.groupby(year)["date"].nunique().to_dict()
    liq = (m["vol_ma20"].fillna(0) >= 500 * 1000).to_numpy()
    if "--nocrash" in sys.argv:
        # 排除深崩窗（大盤20日崩/深乖離），專挖「平常日子」的角落
        liq &= (m["mkt_ret20"].fillna(0) > -8).to_numpy() \
             & (m["mkt_bias60"].fillna(0) > -6).to_numpy()
        print("[nocrash 模式] 已排除深崩交易日\n")
    if "--sideways" in sys.argv:
        # 只留大盤盤整日：20日漲跌 ±4% 內且乖離季線 ±4% 內
        liq &= (m["mkt_ret20"].abs().fillna(99) < 4).to_numpy() \
             & (m["mkt_bias60"].abs().fillna(99) < 4).to_numpy()
        print("[sideways 模式] 只挖大盤盤整交易日\n")

    f = {c: m[c].to_numpy(dtype=float) for c in (
        "atr_pct", "c_over_ma20", "pos_52w", "dist_60d_high", "bias_20", "bias_60",
        "ret5", "ret20", "rel_ret20", "kd_k", "vol_ratio", "vol_trend", "pe", "pb",
        "inst_f5", "inst_streak", "sq_ratio", "short_chg5", "margin_chg5",
        "sec_breadth", "peer_surge5", "mkt_bias60", "mkt_ret20", "close",
        "ma60", "ma240", "ma_align")}

    def gt(x, v): return np.nan_to_num(f[x], nan=-1e18) > v
    def lt(x, v): return np.nan_to_num(f[x], nan=1e18) < v

    P: dict[str, np.ndarray] = {
        # atr_pct 為小數制（0.06=6%）
        "atr>6": gt("atr_pct", 0.06), "atr>8": gt("atr_pct", 0.08),
        "atr>10": gt("atr_pct", 0.10), "atr<3": lt("atr_pct", 0.03),
        "站上MA20": gt("c_over_ma20", 0), "強壓MA20+10%": gt("c_over_ma20", 0.10),
        "破MA20-10%": lt("c_over_ma20", -0.10),
        "MA20上揚": m["ma20_up5"].fillna(False).to_numpy(dtype=bool),
        "站上MA60": np.nan_to_num(f["close"], nan=-1e18) > np.nan_to_num(f["ma60"], nan=1e18),
        "破年線": np.nan_to_num(f["close"], nan=1e18) < np.nan_to_num(f["ma240"], nan=-1e18),
        "均線多排": gt("ma_align", 2.5), "均線空排": lt("ma_align", 0.5),
        "52w高位>0.8": gt("pos_52w", 0.8), "52w低位<0.15": lt("pos_52w", 0.15),
        "距60高<-20": lt("dist_60d_high", -20), "距60高<-30": lt("dist_60d_high", -30),
        "乖離20<-8": lt("bias_20", -8), "乖離20>8": gt("bias_20", 8),
        "乖離60>20": gt("bias_60", 20), "乖離60<-20": lt("bias_60", -20),
        "5日跌>8": lt("ret5", -8), "5日漲>8": gt("ret5", 8),
        "20日漲>20": gt("ret20", 20), "20日跌>20": lt("ret20", -20),
        "相對強20>15": gt("rel_ret20", 15), "相對弱20<-10": lt("rel_ret20", -10),
        "KD<20": lt("kd_k", 20), "KD>85": gt("kd_k", 85),
        "爆量>2.5": gt("vol_ratio", 2.5), "量枯<0.4": lt("vol_ratio", 0.4),
        "量勢升>1.5": gt("vol_trend", 1.5), "量勢縮<0.6": lt("vol_trend", 0.6),
        "PE>60": gt("pe", 60), "虧損無PE": np.isnan(f["pe"]),
        "PB>5": gt("pb", 5), "PB<1": lt("pb", 1),
        "外資5日買": gt("inst_f5", 0), "法人連買≥4": gt("inst_streak", 3.5),
        "券資比>10": gt("sq_ratio", 10), "券資比>25": gt("sq_ratio", 25),
        "券暴增>50": gt("short_chg5", 50), "資減>8": lt("margin_chg5", -8),
        "資增>10": gt("margin_chg5", 10),
        "類股寬度<20": lt("sec_breadth", 20), "類股寬度>75": gt("sec_breadth", 75),
        "同類群漲>12": gt("peer_surge5", 12),
        "盤整收斂<0.55": coil_ratio(m) < 0.55, "盤整極縮<0.4": coil_ratio(m) < 0.4,
        "10日振幅<8%": range_n(m, 10) < 0.08, "10日振幅>15%": range_n(m, 10) > 0.15,
        "大盤深崩≤-6": lt("mkt_bias60", -6.0 + 1e-12) | (np.nan_to_num(f["mkt_bias60"], nan=1e18) == -6.0),
        "大盤強≥4": gt("mkt_bias60", 4.0 - 1e-12),
        "大盤20日崩≤-8": lt("mkt_ret20", -8.0 + 1e-12),
    }
    if "--relative" in sys.argv:
        # 相對版原子：當日橫斷面百分位排名（自動適應年代水位）＋大盤相對自身歷史分位
        print("[relative 模式] 加入當日排名/歷史分位原子\n")
        for col, tag in (("atr_pct", "ATR"), ("ret20", "R20"), ("ret5", "R5"),
                         ("c_over_ma20", "壓MA20"), ("pb", "PB"), ("pe", "PE"),
                         ("dist_60d_high", "距60高"), ("rel_ret20", "相對強"),
                         ("vol_ratio", "量比"), ("sq_ratio", "券資")):
            r = m.groupby("date")[col].rank(pct=True).to_numpy()
            P[f"R:{tag}前10%"] = r >= 0.90
            P[f"R:{tag}前5%"] = r >= 0.95
            P[f"R:{tag}後10%"] = r <= 0.10
        # 大盤 20 日報酬相對過去 500 交易日的分位
        mk = m.drop_duplicates("date")[["date", "mkt_ret20"]].sort_values("date")
        mk["q"] = mk["mkt_ret20"].rolling(500, min_periods=250).rank(pct=True)
        qmap = dict(zip(mk["date"], mk["q"]))
        q = m["date"].map(qmap).to_numpy(dtype=float)
        P["R:大盤崩(歷史後5%)"] = np.nan_to_num(q, nan=1.0) <= 0.05
        P["R:大盤弱(歷史後15%)"] = np.nan_to_num(q, nan=1.0) <= 0.15
        P["R:大盤強(歷史前15%)"] = np.nan_to_num(q, nan=0.0) >= 0.85

    names = list(P)
    for k in names:
        P[k] = P[k] & liq

    def stats(mask):
        n_y = np.bincount(ycode[mask], minlength=6)
        h_y = np.bincount(ycode[mask], weights=hit[mask], minlength=6)
        yrs = [(h_y[y] / n_y[y] * 100) if n_y[y] >= 15 else None for y in range(6)]
        mine_ok = [yrs[y] for y in MINE if yrs[y] is not None]
        floor = min(mine_ok) if len(mine_ok) >= 3 else None
        mine_n = int(sum(n_y[y] for y in MINE))
        return floor, mine_n, yrs, n_y

    print("=== 單條件邊際（參考）===")
    for k in names:
        fl, mn, yrs, _ = stats(P[k])
        ys = " ".join(f"{21+i}:{v:3.0f}" if v is not None else f"{21+i}: --" for i, v in enumerate(yrs))
        fls = "  --" if fl is None else f"{fl:4.0f}"
        print(f"  {k:>12} 地板{fls}  {ys}  n挖={mn}")

    print("\n=== 二元合取掃描 ===")
    pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            mask = P[names[i]] & P[names[j]]
            fl, mn, yrs, _ = stats(mask)
            if fl is not None and fl >= PAIR_FLOOR and mn >= PAIR_N:
                pairs.append((fl, mn, (names[i], names[j]), mask, yrs))
    pairs.sort(key=lambda x: -x[0])
    print(f"floor≥{PAIR_FLOOR} 且 n挖≥{PAIR_N}：{len(pairs)} 組")

    print("\n=== 三元擴展 ===")
    tris = []
    seen = set()
    for fl0, mn0, pr, mask0, _ in pairs[:60]:
        for k in names:
            if k in pr:
                continue
            key = tuple(sorted(pr + (k,)))
            if key in seen:
                continue
            seen.add(key)
            mask = mask0 & P[k]
            fl, mn, yrs, n_y = stats(mask)
            if fl is not None and fl >= TRI_FLOOR and mn >= TRI_N:
                tris.append((fl, mn, key, mask, yrs, n_y))
    # 二元本身達標的也算候選
    for fl, mn, pr, mask, yrs in pairs:
        if fl >= TRI_FLOOR:
            _, _, _, n_y = stats(mask)
            tris.append((fl, mn, tuple(sorted(pr)), mask, yrs, n_y))
    tris.sort(key=lambda x: (-x[0], -x[1]))

    kept = []
    for cand in tris:
        mask = cand[3]
        dup = False
        for k2 in kept:
            inter = int((mask & k2[3]).sum())
            union = int((mask | k2[3]).sum())
            if union and inter / union > JACCARD_MAX:
                dup = True
                break
        if not dup:
            kept.append(cand)
        if len(kept) >= 25:
            break
    print(f"floor≥{TRI_FLOOR} 去重後 {len(kept)} 組：\n")
    for fl, mn, key, mask, yrs, n_y in kept:
        ys = " ".join(f"{21+i}:{v:3.0f}%({n_y[i]})" if v is not None else f"{21+i}: --" for i, v in enumerate(yrs))
        print(f"地板{fl:3.0f}%  n挖={mn:5}  {' & '.join(key)}")
        print(f"        {ys}")


if __name__ == "__main__":
    main()
