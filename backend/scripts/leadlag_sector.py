"""類股 lead-lag 研究：A 類股近期走勢能不能領先 B 類股未來 10 日？

動機
----
market_base_gate 的結論是大盤層閘門推不動穩定的 70% —— 大盤是全市場平均，領先資訊
在平均過程中被抵銷。輪動資訊應該在**類股之間的相對強弱**裡，而不是在大盤水位裡。

三個必須先處理的統計陷阱
------------------------
1) 共同因子：台股類股彼此相關普遍 >0.7，全被大盤帶著走。不剔除市場因子，
   任何 lead-lag 相關都只是「大盤自相關」的投影。本腳本一律用市場中性化報酬
   （類股報酬 − 當日全市場等權報酬）。
2) 重疊窗：用每日 rebalance 測 10 日前瞻，相鄰觀測共用 9 天資料，有效樣本只有
   名目的 1/10，t 值會虛胖。評估一律用 10 日不重疊 rebalance。
3) 多重檢定：51 類股 × 51 × 6 個 lag ≈ 15,600 組關係。穩定性檢定（前後半期是否
   同號同序）比單組顯著性更有意義，因為 lead-lag 關係出了名的不穩。

三個研究
--------
A 全期 lead-lag 強度：交叉領先（非對角）是否強過自身動能（對角）
B 穩定性：2020-2023 估的矩陣，2024-2026 還成不成立（矩陣相關 + top-N 配對重疊）
C「近期」是多久：估計窗 W ∈ {10,20,40,60,120,250} walk-forward，
  用 W 窗估的矩陣預測未來 10 日類股報酬，比較跨截面 IC —— 這才是「近期代表什麼」的答案

訊號定義：領先方取「t−x 日為止的 5 日報酬」，跟隨方取「t+1~t+10 的 10 日報酬」，
x ∈ {0,1,2,3,5,10}。x=0 表示同期（對照組，不算領先）。

用法：PYTHONIOENCODING=utf-8 python scripts/leadlag_sector.py
輸出：data/leadlag_sector.json + stdout
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time

import numpy as np
import pandas as pd
from scipy import stats

_BASE = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0]
sys.path.insert(0, _BASE)

_DB = _BASE + "/data/twa.db"
_OUT = _BASE + "/data/leadlag_sector.json"

_MIN_STOCKS = 8          # 類股最少成分股（太小的類股日報酬雜訊過大）
_EXCLUDE = {"ETF", "ETN", "Index", "上櫃ETF", "上櫃指數股票型基金(ETF)"}
_LAGS = [0, 1, 2, 3, 5, 10]
_WINDOWS = [10, 20, 40, 60, 120, 250]
_SIG_H = 5               # 領先方訊號：5 日報酬
_FWD_H = 10              # 跟隨方目標：未來 10 日報酬（對齊定版口徑）
_STEP = 10               # 不重疊 rebalance 間隔


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ─────────────────────────── 類股日報酬 ───────────────────────────


def build_sector_returns() -> tuple[pd.DataFrame, pd.Series]:
    """回傳（市場中性化的類股日報酬 [date × sector]，全市場等權日報酬）。"""
    con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    sec = pd.read_sql_query("SELECT id, name FROM sectors", con)
    keep = sec[~sec["name"].isin(_EXCLUDE)]
    px = pd.read_sql_query(
        "SELECT p.stock_id, p.date, p.close, s.sector_id "
        "FROM daily_prices p JOIN stocks s ON s.id = p.stock_id "
        "WHERE p.close IS NOT NULL AND s.sector_id IS NOT NULL "
        "ORDER BY p.stock_id, p.date", con)
    con.close()

    px = px[px["sector_id"].isin(keep["id"])]
    px["ret"] = (px["close"] / px.groupby("stock_id", sort=False)["close"].shift(1) - 1.0) * 100
    px = px.dropna(subset=["ret"])

    cnt = px.groupby("sector_id")["stock_id"].nunique()
    ok = cnt[cnt >= _MIN_STOCKS].index
    px = px[px["sector_id"].isin(ok)]

    mkt = px.groupby("date")["ret"].mean()                       # 全市場等權
    sr = px.groupby(["date", "sector_id"])["ret"].mean().unstack()
    sr = sr.sub(mkt, axis=0)                                     # ← 市場中性化
    sr = sr.dropna(axis=1, thresh=int(len(sr) * 0.9))            # 覆蓋不足的類股剔除
    names = dict(zip(keep["id"], keep["name"]))
    sr.columns = [names.get(c, str(c)) for c in sr.columns]
    _log(f"類股 {sr.shape[1]} 個 × {sr.shape[0]:,} 交易日（已剔除大盤共同因子）")
    return sr, mkt


def _roll_sum(a: np.ndarray, h: int) -> np.ndarray:
    """沿 axis=0 的 h 日移動總和（前 h-1 列為 NaN）。"""
    c = np.cumsum(np.nan_to_num(a), axis=0)
    out = np.full_like(a, np.nan, dtype=float)
    out[h - 1:] = c[h - 1:] - np.vstack([np.zeros((1, a.shape[1])), c[:-h]])
    return out


def _zs(a: np.ndarray) -> np.ndarray:
    """逐欄標準化（忽略 NaN）。"""
    mu = np.nanmean(a, axis=0)
    sd = np.nanstd(a, axis=0)
    sd = np.where(sd > 1e-9, sd, np.nan)
    return (a - mu) / sd


# ─────────────────────────── 研究 A/B ───────────────────────────


def leadlag_matrix(sig: np.ndarray, fwd: np.ndarray, lag: int,
                   rows: np.ndarray) -> np.ndarray:
    """M[L,F] = corr(領先方 L 於 t−lag 的訊號, 跟隨方 F 於 t 的前瞻報酬)。"""
    if lag > 0:
        x = np.vstack([np.full((lag, sig.shape[1]), np.nan), sig[:-lag]])
    else:
        x = sig
    r = rows & ~np.isnan(x).any(axis=1) & ~np.isnan(fwd).any(axis=1)
    if r.sum() < 10:
        return np.full((sig.shape[1], sig.shape[1]), np.nan)
    X, Y = _zs(x[r]), _zs(fwd[r])
    return (X.T @ Y) / len(X)


def main() -> None:
    t0 = time.time()
    sr, _ = build_sector_returns()
    dates = sr.index.to_numpy()
    A = sr.to_numpy(dtype=float)
    cols = list(sr.columns)
    n_sec = len(cols)

    sig = _roll_sum(A, _SIG_H)                       # 領先方：截至 t 的 5 日報酬
    fwd_raw = _roll_sum(A, _FWD_H)
    fwd = np.vstack([fwd_raw[_FWD_H:], np.full((_FWD_H, n_sec), np.nan)])   # t+1~t+10

    yrs = pd.Series(dates).astype(str).str[:4].to_numpy()
    half1 = (yrs >= "2020") & (yrs <= "2023")
    half2 = yrs >= "2024"

    # ── A 全期強度：交叉領先 vs 自身動能 ──
    # 10 日前瞻報酬每日重疊，有效樣本≈名目/10；門檻須據此放大，否則會把雜訊當訊號
    n_pairs = n_sec * (n_sec - 1)
    n_eff = len(A) / _FWD_H
    crit = float(stats.norm.isf(0.025 / n_pairs)) / np.sqrt(n_eff)
    print("\n=== A. lead-lag 強度：交叉領先（非對角）vs 自身動能（對角）===")
    print(f"有效樣本 ≈ {n_eff:.0f}（10 日報酬每日重疊）／{n_pairs} 組配對"
          f"／Bonferroni 顯著門檻 |r| ≥ {crit:.3f}")
    print(f"{'lag':>5}{'對角均|r|':>12}{'非對角均|r|':>13}{'非對角最大|r|':>14}"
          f"{'過門檻組數':>12}")
    allrows = np.ones(len(A), dtype=bool)
    for lag in _LAGS:
        M = leadlag_matrix(sig, fwd, lag, allrows)
        dia = np.abs(np.diag(M))
        off = np.abs(M[~np.eye(n_sec, dtype=bool)])
        print(f"{lag:>5}{np.nanmean(dia):>12.3f}{np.nanmean(off):>13.3f}"
              f"{np.nanmax(off):>14.3f}{int(np.nansum(off > crit)):>12}")

    # ── B 穩定性：前半期估的關係，後半期還成立嗎 ──
    print("\n=== B. 穩定性：2020-2023 估的矩陣 vs 2024-2026 ===")
    print(f"{'lag':>5}{'矩陣相關':>10}{'前50強同號率':>13}{'前50強後期均r':>15}")
    stab = {}
    for lag in _LAGS:
        M1 = leadlag_matrix(sig, fwd, lag, half1)
        M2 = leadlag_matrix(sig, fwd, lag, half2)
        off = ~np.eye(n_sec, dtype=bool)
        a, b = M1[off], M2[off]
        ok = ~np.isnan(a) & ~np.isnan(b)
        rho = stats.spearmanr(a[ok], b[ok]).statistic if ok.sum() > 50 else np.nan
        top = np.argsort(-np.abs(np.where(ok, a, 0)))[:50]
        same = float(np.mean(np.sign(a[top]) == np.sign(b[top])))
        stab[lag] = {"matrix_rho": round(float(rho), 3),
                     "top50_same_sign": round(same, 3),
                     "top50_next_r": round(float(np.nanmean(np.abs(b[top]))), 3)}
        print(f"{lag:>5}{rho:>10.3f}{same*100:>12.1f}%{np.nanmean(np.abs(b[top])):>15.3f}")

    # ── C「近期」是多久：walk-forward 估計窗掃描 ──
    print(f"\n=== C. 估計窗 W 掃描（walk-forward，每 {_STEP} 日不重疊 rebalance）===")
    print("用 W 窗估的 lead-lag 矩陣預測未來 10 日類股報酬，跨截面 Spearman IC")
    print(f"{'W':>6}" + "".join(f"{'lag'+str(l):>10}" for l in _LAGS))
    scan = {}
    for W in _WINDOWS:
        row, cells = "", {}
        for lag in _LAGS:
            ics = []
            start = max(W + _SIG_H + lag + _FWD_H, 60)
            for ti in range(start, len(A) - _FWD_H, _STEP):
                win = np.zeros(len(A), dtype=bool)
                # 估計窗必須退後 _FWD_H 日：fwd[s] 涵蓋 [s+1, s+10]，窗尾若貼著 ti，
                # 其前瞻報酬會延伸到 ti+9 —— 那正是要預測的區段，會造成前視偏誤。
                win[ti - W - _FWD_H:ti - _FWD_H] = True
                M = leadlag_matrix(sig, fwd, lag, win)
                if np.isnan(M).all():
                    continue
                s_t = sig[ti - lag] if ti - lag >= 0 else None
                if s_t is None or np.isnan(s_t).any():
                    continue
                pred = np.nan_to_num(M).T @ ((s_t - np.nanmean(s_t)) /
                                             (np.nanstd(s_t) + 1e-9))
                act = fwd[ti]
                o = ~np.isnan(act)
                if o.sum() < 10:
                    continue
                ics.append(stats.spearmanr(pred[o], act[o]).statistic)
            ic = float(np.nanmean(ics)) if ics else np.nan
            t = (float(np.nanmean(ics)) / (np.nanstd(ics) / np.sqrt(len(ics)))
                 if len(ics) > 5 and np.nanstd(ics) > 0 else np.nan)
            cells[lag] = {"ic": round(ic, 4), "t": round(t, 2), "n": len(ics)}
            row += f"{ic:>+9.3f}" + ("*" if abs(t) >= 2 else " ")
        scan[W] = cells
        print(f"{W:>6}{row}")
    print("  （* = |t|≥2；lag0 是同期對照，不算領先）")

    # 最佳組合
    best = max(((W, l, c[l]) for W, c in scan.items() for l in _LAGS if l > 0),
               key=lambda x: abs(x[2]["ic"]) if not np.isnan(x[2]["ic"]) else -1)
    print(f"\n最佳領先組合：W={best[0]}、lag={best[1]}"
          f"，IC={best[2]['ic']:+.4f}（t={best[2]['t']}, n={best[2]['n']}）")

    with open(_OUT, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M"),
                   "sectors": cols, "stability": stab,
                   "window_scan": {str(k): {str(l): v for l, v in c.items()}
                                   for k, c in scan.items()},
                   "best": {"W": best[0], "lag": best[1], **best[2]}},
                  fh, ensure_ascii=False, indent=1)
    _log(f"完成（{time.time()-t0:.0f}s）→ {_OUT}")


if __name__ == "__main__":
    main()
