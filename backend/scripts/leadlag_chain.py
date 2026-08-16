"""產業鏈上下游 lead-lag：上游走勢能不能領先下游未來 10 日？

為什麼這個版本值得測，而類股版不值得
------------------------------------
leadlag_sector.py 證實「A 類股領先 B 類股」不成立：1,190 組配對 0 組過檢定、
前後半期同號率 40~60%（擲硬幣）。但那是**任意配對**——沒有先驗機制，等於在
1,190 個假設裡撈，撈到的都是雜訊。

產業鏈不同：`industry_chain_members` 有 `stream`（上游/中游/下游）欄，上游接單
領先下游出貨是真的因果鏈。假設空間也小得多——11 條產業鏈（三段各 ≥8 檔）
× 6 個有向配對 × 6 個 lag = 396 個檢定，Bonferroni 門檻遠比類股版寬鬆。

關鍵設計：反向對照組
--------------------
只看「上→下顯著」不夠——若整條鏈只是共同波動，正反向都會顯著。因此同時測
反向（下→上、中→上、下→中）當對照：**唯有正向顯著而反向不顯著，才是真的方向性**。

沿用 leadlag_sector 的三個防護
------------------------------
1) 市場中性化（減去當日全市場等權報酬），否則測到的只是大盤自相關的投影
2) walk-forward 估計窗退後一個 horizon（fwd[s] 涵蓋 [s+1,s+10]，窗尾貼著 t 會前視）
3) 有效樣本 = 名目/10（10 日報酬每日重疊），顯著門檻據此放大

用法：PYTHONIOENCODING=utf-8 python scripts/leadlag_chain.py
輸出：data/leadlag_chain.json + stdout
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
sys.path.insert(0, _BASE + "/scripts")

from leadlag_sector import _roll_sum, _zs  # noqa: E402

_DB = _BASE + "/data/twa.db"
_OUT = _BASE + "/data/leadlag_chain.json"

_MIN_STOCKS = 8
_LAGS = [1, 2, 3, 5, 10]
_WINDOWS = [20, 40, 60, 120, 250]
_SIG_H, _FWD_H, _STEP = 5, 10, 10
_STREAMS = ["上游", "中游", "下游"]
# 正向＝機制方向（上游領先下游）；反向＝對照組，應該不顯著
_FORWARD = [("上游", "中游"), ("上游", "下游"), ("中游", "下游")]
_REVERSE = [("中游", "上游"), ("下游", "上游"), ("下游", "中游")]


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def build_chain_streams() -> pd.DataFrame:
    """[date × (chain_name, stream)] 的市場中性化日報酬。

    一檔股票平均屬於 2.8 個節點，同一條鏈裡可能橫跨多個 stream；那種股票會讓
    「上游 vs 下游」變成部分自我相關，一律剔除。
    """
    con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    mem = pd.read_sql_query(
        "SELECT DISTINCT stock_id, chain_name, stream FROM industry_chain_members "
        "WHERE stream IS NOT NULL", con)
    px = pd.read_sql_query(
        "SELECT stock_id, date, close FROM daily_prices WHERE close IS NOT NULL "
        "ORDER BY stock_id, date", con)
    con.close()

    n0 = len(mem)
    amb = mem.groupby(["chain_name", "stock_id"])["stream"].nunique()
    amb = set(amb[amb > 1].index)
    mem = mem[~mem.set_index(["chain_name", "stock_id"]).index.isin(amb)]
    _log(f"產業鏈成員 {n0:,} → 剔除同鏈跨段的 {n0-len(mem):,} 筆後 {len(mem):,}")

    px["r"] = (px["close"] / px.groupby("stock_id", sort=False)["close"].shift(1) - 1.0) * 100
    px = px.dropna(subset=["r"])
    mkt = px.groupby("date")["r"].mean()

    j = mem.merge(px[["stock_id", "date", "r"]], on="stock_id", how="inner")
    cnt = j.groupby(["chain_name", "stream"])["stock_id"].nunique()
    ok = cnt[cnt >= _MIN_STOCKS].index
    j = j[j.set_index(["chain_name", "stream"]).index.isin(set(ok))]

    g = j.groupby(["date", "chain_name", "stream"])["r"].mean().unstack([1, 2])
    g = g.sub(mkt, axis=0)
    full = [c for c in {c[0] for c in g.columns}
            if all((c, s) in g.columns for s in _STREAMS)]
    g = g[[(c, s) for c in sorted(full) for s in _STREAMS]]
    _log(f"三段齊全的產業鏈 {len(full)} 條 × {len(g):,} 交易日（已剔大盤）")
    return g


def _pair_corr(sig: np.ndarray, fwd: np.ndarray, li: int, fi: int,
               lag: int, rows: np.ndarray) -> float:
    x = np.concatenate([np.full(lag, np.nan), sig[:-lag, li]]) if lag else sig[:, li]
    y = fwd[:, fi]
    r = rows & ~np.isnan(x) & ~np.isnan(y)
    if r.sum() < 20:
        return np.nan
    a, b = x[r], y[r]
    if a.std() < 1e-9 or b.std() < 1e-9:
        return np.nan
    return float(np.corrcoef(a, b)[0, 1])


def main() -> None:
    t0 = time.time()
    g = build_chain_streams()
    A = g.to_numpy(dtype=float)
    cols = list(g.columns)
    cidx = {c: i for i, c in enumerate(cols)}
    chains = sorted({c[0] for c in cols})
    dates = g.index.astype(str).to_numpy()

    sig = _roll_sum(A, _SIG_H)
    fwd_raw = _roll_sum(A, _FWD_H)
    fwd = np.vstack([fwd_raw[_FWD_H:], np.full((_FWD_H, A.shape[1]), np.nan)])

    half1 = (dates >= "2020") & (dates <= "2023")
    half2 = dates >= "2024"
    allr = np.ones(len(A), dtype=bool)

    n_tests = len(chains) * (len(_FORWARD) + len(_REVERSE)) * len(_LAGS)
    n_eff = len(A) / _FWD_H
    crit = float(stats.norm.isf(0.025 / n_tests)) / np.sqrt(n_eff)
    _log(f"{len(chains)} 鏈 × 6 有向配對 × {len(_LAGS)} lag = {n_tests} 檢定；"
         f"有效樣本 ≈ {n_eff:.0f}；Bonferroni 門檻 |r| ≥ {crit:.3f}")

    # ── 正向 vs 反向：全期強度與穩定性 ──
    rows = []
    for ch in chains:
        for tag, pairs in (("正向", _FORWARD), ("反向", _REVERSE)):
            for (ls, fs) in pairs:
                li, fi = cidx[(ch, ls)], cidx[(ch, fs)]
                for lag in _LAGS:
                    r_all = _pair_corr(sig, fwd, li, fi, lag, allr)
                    r1 = _pair_corr(sig, fwd, li, fi, lag, half1)
                    r2 = _pair_corr(sig, fwd, li, fi, lag, half2)
                    rows.append({"chain": ch, "dir": tag, "pair": f"{ls}→{fs}",
                                 "lag": lag, "r": round(r_all, 3),
                                 "r_h1": round(r1, 3), "r_h2": round(r2, 3),
                                 "same_sign": bool(np.sign(r1) == np.sign(r2))
                                 if not (np.isnan(r1) or np.isnan(r2)) else None,
                                 "pass": bool(abs(r_all) >= crit)})

    df = pd.DataFrame(rows)
    print(f"\n=== 正向（機制）vs 反向（對照）===")
    print(f"{'方向':<6}{'檢定數':>7}{'均|r|':>9}{'最大|r|':>9}{'過門檻':>8}{'前後半同號率':>13}")
    for tag in ("正向", "反向"):
        s = df[df["dir"] == tag]
        ss = s["same_sign"].dropna()
        print(f"{tag:<6}{len(s):>7}{s['r'].abs().mean():>9.3f}{s['r'].abs().max():>9.3f}"
              f"{int(s['pass'].sum()):>8}{ss.mean()*100:>12.1f}%")

    print(f"\n=== 各產業鏈正向最強（|r| 前 12）===")
    fwd_df = df[df["dir"] == "正向"].reindex(
        df[df["dir"] == "正向"]["r"].abs().sort_values(ascending=False).index)
    print(f"{'產業鏈':<10}{'配對':<12}{'lag':>4}{'全期r':>9}{'20-23':>9}{'24-26':>9}  過門檻")
    for _, r in fwd_df.head(12).iterrows():
        print(f"{r['chain']:<10}{r['pair']:<12}{r['lag']:>4}{r['r']:>9.3f}"
              f"{r['r_h1']:>9.3f}{r['r_h2']:>9.3f}  {'✓' if r['pass'] else ''}")

    # ── walk-forward：正向配對是否真的可用 ──
    print(f"\n=== walk-forward IC（正向配對，估計窗已退後 {_FWD_H} 日避免前視）===")
    print(f"{'W':>6}" + "".join(f"{'lag'+str(l):>10}" for l in _LAGS))
    scan = {}
    for W in _WINDOWS:
        line, cells = "", {}
        for lag in _LAGS:
            ics = []
            start = max(W + _SIG_H + lag + _FWD_H, 60)
            for ti in range(start, len(A) - _FWD_H, _STEP):
                win = np.zeros(len(A), dtype=bool)
                win[ti - W - _FWD_H:ti - _FWD_H] = True
                pred, act = [], []
                for ch in chains:
                    for (ls, fs) in _FORWARD:
                        li, fi = cidx[(ch, ls)], cidx[(ch, fs)]
                        b = _pair_corr(sig, fwd, li, fi, lag, win)
                        s_t = sig[ti - lag, li]
                        a_t = fwd[ti, fi]
                        if np.isnan(b) or np.isnan(s_t) or np.isnan(a_t):
                            continue
                        pred.append(b * s_t)
                        act.append(a_t)
                if len(pred) >= 10:
                    ics.append(stats.spearmanr(pred, act).statistic)
            ic = float(np.nanmean(ics)) if ics else np.nan
            t = (ic / (np.nanstd(ics) / np.sqrt(len(ics)))
                 if len(ics) > 5 and np.nanstd(ics) > 0 else np.nan)
            cells[lag] = {"ic": round(ic, 4), "t": round(t, 2), "n": len(ics)}
            line += f"{ic:>+9.3f}" + ("*" if abs(t) >= 2 else " ")
        scan[W] = cells
        print(f"{W:>6}{line}")
    print("  （* = |t|≥2）")

    with open(_OUT, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M"),
                   "chains": chains, "crit": round(crit, 3),
                   "pairs": rows,
                   "walk_forward": {str(k): {str(l): v for l, v in c.items()}
                                    for k, c in scan.items()}},
                  fh, ensure_ascii=False, indent=1)
    _log(f"完成（{time.time()-t0:.0f}s）→ {_OUT}")


if __name__ == "__main__":
    main()
