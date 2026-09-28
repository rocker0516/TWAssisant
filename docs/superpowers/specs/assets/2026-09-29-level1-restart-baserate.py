"""一次性：可交易 U_t 上「N 日內碰 +X% 停利、否則第 N 日收盤出場」的基率表。

Entry = O(t+1)（開盤漲停未成交排除，沿用 targets_v3）
hit   = max(H(t+1..t+N)) >= Entry*(1+X)   ——停利掛單成交價保守取 Entry*(1+X)
net   = X - cost（hit）或 C(t+N)/Entry - 1 - cost（未 hit）
"""
import sqlite3
import sys
from pathlib import Path

import sklearn  # noqa: F401  (Windows lightgbm/OpenMP 載入順序慣例，無害)
import numpy as np
import pandas as pd

BACKEND = Path(r"C:\Users\User\Desktop\python\TWAssisant\backend")
sys.path.insert(0, str(BACKEND))
from app.research.level1.universe import build_tradable_universe  # noqa: E402
from app.research.level1.prices import load_price_matrices  # noqa: E402
from app.research.level1.targets_v3 import entry_and_fill, COST_RT  # noqa: E402

con = sqlite3.connect(BACKEND / "data" / "twa.db")
close, uni = build_tradable_universe(con)
px = load_price_matrices(con, close.index, close.columns, cols=("open", "high"))
entry, fill = entry_and_fill(px["open"], close)
entry = entry.where(uni)

H = px["high"].to_numpy()
C = close.to_numpy()
E = entry.to_numpy()
T = len(close.index)
years = pd.Index(close.index.str[:4])

def fwd_max_high(n):
    # max(H[t+1..t+n])
    out = np.full_like(H, np.nan)
    for t in range(T - n):
        out[t] = np.nanmax(H[t + 1:t + n + 1], axis=0)
    return out

rows = []
for N in (5, 10, 20):
    mh = fwd_max_high(N)
    cN = np.vstack([C[N:], np.full((N, C.shape[1]), np.nan)])
    valid = np.isfinite(E) & np.isfinite(mh) & np.isfinite(cN)
    for X in (0.05, 0.08, 0.10, 0.15):
        hit = mh >= E * (1 + X)
        net = np.where(hit, X, cN / E - 1) - COST_RT
        for seg, sel in (("dev22-24", years.isin(["2022", "2023", "2024"])),
                         ("hold25-26", years.isin(["2025", "2026"]))):
            v = valid & np.asarray(sel)[:, None]
            h = hit[v]; r = net[v]
            # 逐年命中率離散
            yr = [hit[valid & np.asarray(years == y)[:, None]].mean()
                  for y in sorted(set(years[sel]))]
            rows.append(dict(N=N, X=int(X * 100), seg=seg, n=int(v.sum()),
                             hit=round(h.mean() * 100, 1),
                             hit_yr="/".join(f"{x*100:.0f}" for x in yr),
                             net_mean=round(r.mean() * 100, 2),
                             net_med=round(np.median(r) * 100, 2)))
df = pd.DataFrame(rows)
pd.set_option("display.width", 200)
print(df.to_string(index=False))
print("U_t median size:", int(uni.sum(axis=1).median()), "dates:", close.index[0], close.index[-1])
