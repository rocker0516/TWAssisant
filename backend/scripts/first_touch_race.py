"""先觸及分析（first-passage race）：停利 +10% 與各檔位停損，誰先被摸到。

回應核心質疑：交易結構是停利離場，30 日報酬無關；唯一相干的是
「進場後，+10% 停利與停損哪個先發生」。MFE/MAE 聚合值看不出順序，這裡
直接掃路徑：進場錨＝訊號日隔日最高（與判官/模擬倉同錨），之後逐日
先檢查停損（同日雙觸保守記停損）、再檢查停利，30 交易日封頂。

對每個訊號 × 停損檔位 {5,8,10,12,15,20%} 輸出：
  停利先到%（=真實可交易勝率）/ 停損先到% / 都沒到%（逾期，另計逾期時平均報酬）
  期望值 ≈ 10%×停利先到 − stop%×停損先到 ＋ 逾期平均報酬×逾期占比

用法：PYTHONIOENCODING=utf-8 python scripts/first_touch_race.py
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

_BASE = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0]
sys.path.insert(0, _BASE)
sys.path.insert(0, _BASE + "/scripts")

import mega_mine as mm  # noqa: E402

_TARGET = 0.10
_STOPS = (0.05, 0.08, 0.10, 0.12, 0.15, 0.20)
_H = 30


def build_signals(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """頂級訊號遮罩（門檻＝挖掘窗分位數，遮罩套全期）。"""
    mine = df[(df["date"].astype(str) >= mm._MINE_LO) & (df["date"].astype(str) <= mm._MINE_HI)]

    def q80(f: str) -> float:
        return float(np.nanquantile(mine[f].to_numpy(float), 0.80))

    clean = ~(df["att_notice5"].to_numpy(bool) | df["att_punish10"].to_numpy(bool))
    sigs = {
        "處置10日": df["att_punish10"].to_numpy(bool),
        "處置×類股熱區": df["att_punish10"].to_numpy(bool)
                     & (df["sec_att5"].to_numpy(float) > q80("sec_att5")),
        "乾淨×熱區×高位": clean
                      & (df["sec_att5"].to_numpy(float) > q80("sec_att5"))
                      & (df["pos_52w"].to_numpy(float) > q80("pos_52w")),
        "乾淨×高位×類股動能": clean
                       & (df["pos_52w"].to_numpy(float) > q80("pos_52w"))
                       & (df["sec_ret20"].to_numpy(float) > q80("sec_ret20")),
        "爆發×量增(對照)": (df["atr_pct"].to_numpy(float) > 0.05)
                      & (df["c_over_ma20"].to_numpy(float) > 0)
                      & df["ma20_up5"].fillna(False).to_numpy(bool)
                      & (df["vol_trend"].to_numpy(float) > 1.5),
        "全市場(基準)": np.ones(len(df), dtype=bool),
    }
    return sigs


def race(df: pd.DataFrame, mask: np.ndarray, seed: int = 7, cap: int = 60000) -> dict | None:
    """對遮罩選中的 (stock,date) 逐事件掃路徑。样本過大時隨機抽 cap 筆。"""
    sel = df.loc[np.flatnonzero(mask), ["stock_id", "date"]]
    if len(sel) < 200:
        return None
    if len(sel) > cap:
        sel = sel.sample(cap, random_state=seed)

    # 每檔股票的日期→(high, low) 序列
    px: dict[str, tuple[np.ndarray, np.ndarray, dict] ] = {}
    need = set(sel["stock_id"])
    import sqlite3
    con = sqlite3.connect(mm.judge._DB)
    q = pd.read_sql_query(
        "SELECT stock_id, date, high, low FROM daily_prices WHERE close IS NOT NULL ORDER BY stock_id, date", con)
    con.close()
    for sid, g in q.groupby("stock_id", sort=False):
        if sid not in need:
            continue
        px[sid] = (g["high"].to_numpy(float), g["low"].to_numpy(float),
                   {d: i for i, d in enumerate(g["date"])})

    n_stop = len(_STOPS)
    win = np.zeros(n_stop)      # 停利先到
    lose = np.zeros(n_stop)     # 停損先到
    to_ret = [[] for _ in range(n_stop)]  # 逾期者的 30 日相對進場報酬
    n_ev = 0
    for sid, d in zip(sel["stock_id"], sel["date"]):
        arr = px.get(sid)
        if arr is None:
            continue
        hi, lo, didx = arr
        i = didx.get(str(d)) if isinstance(d, str) else didx.get(d) or didx.get(str(d))
        if i is None or i + 2 >= len(hi):
            continue
        entry = hi[i + 1]
        if not np.isfinite(entry) or entry <= 0:
            continue
        n_ev += 1
        tgt = entry * (1 + _TARGET)
        end = min(i + 1 + _H, len(hi) - 1)
        done = [False] * n_stop
        for j in range(i + 2, end + 1):
            for k, stp in enumerate(_STOPS):
                if done[k]:
                    continue
                if lo[j] <= entry * (1 - stp):   # 同日雙觸保守記停損
                    lose[k] += 1
                    done[k] = True
                elif hi[j] >= tgt:
                    win[k] += 1
                    done[k] = True
            if all(done):
                break
        for k in range(n_stop):
            if not done[k]:
                to_ret[k].append(lo[end] / entry - 1)  # 逾期用保守的低點估
    if n_ev == 0:
        return None
    out = {"n": n_ev, "stops": {}}
    for k, stp in enumerate(_STOPS):
        w, l = win[k] / n_ev, lose[k] / n_ev
        t = 1 - w - l
        t_avg = float(np.mean(to_ret[k])) if to_ret[k] else 0.0
        ev_pct = _TARGET * 100 * w - stp * 100 * l + t_avg * 100 * t
        out["stops"][f"{int(stp*100)}%"] = {
            "win": round(w * 100, 1), "stop": round(l * 100, 1), "timeout": round(t * 100, 1),
            "ev": round(ev_pct, 2),
        }
    return out


def main() -> None:
    df = pd.read_pickle(_BASE + "/data/mega_mine_features.pkl")
    hold = df["date"].astype(str) >= mm._HOLD_LO
    sigs = build_signals(df)
    for period, pmask in (("全期 2021+", np.ones(len(df), bool)), ("holdout 2025+", hold.to_numpy())):
        print(f"\n{'='*20} {period} {'='*20}")
        for name, m in sigs.items():
            r = race(df, m & pmask)
            if r is None:
                print(f"{name}: 樣本不足")
                continue
            print(f"\n### {name}（n={r['n']:,}）")
            print(f"  {'停損':<6}{'停利先到':>8}{'停損先到':>9}{'逾期':>7}{'期望值/筆':>10}")
            for stp, v in r["stops"].items():
                print(f"  {stp:<6}{v['win']:>7}%{v['stop']:>8}%{v['timeout']:>6}%{v['ev']:>9}%")


if __name__ == "__main__":
    main()
