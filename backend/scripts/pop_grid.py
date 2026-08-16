"""可信度敏感度網格：P(撞上 +X% within H 日) × 嚴選K%，walk-forward OOS。輸出 JSON。

回答「要 ≥60% 字面可信度」該選哪個 漲幅/時間/嚴選 組合。撞上=持有期間摸到(MFE)，
即使用者問的「真的撞上+X%的機率」；另附風險調整(先摸+X%再被-8%打到)。
"""
from __future__ import annotations
import json, sys
from datetime import timedelta
import numpy as np, pandas as pd
from sqlalchemy import select, distinct
sys.path.insert(0, __file__.replace("\\", "/").rsplit("/scripts/", 1)[0])
from app.engines.calibration import _MIN_BARS, _iter_stock_groups
from app.storage import models
from app.storage.database import SessionLocal

_HMAX = 60; _WARMUP = 200; _STOP = -0.08
_XS = [5, 7, 10]; _HS = [20, 40, 60]; _KS = [10, 20]

def _pr(vals): return (pd.Series(vals, dtype=float).rank(pct=True) * 100).to_numpy()

def main():
    n_dates = int(sys.argv[1]) if len(sys.argv) > 1 else 240
    sample = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    folds = 3
    s = SessionLocal()
    try:
        axis = s.execute(select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)).scalars().all()
        targets = sorted(axis[: len(axis) - _HMAX][::sample][-n_dates:])
        date_lo = min(targets) - timedelta(days=_WARMUP)
        stocks = {st.id: st for st in s.execute(select(models.Stock)).scalars().all()}
        stock_ids = sorted(sid for sid in stocks if not sid.startswith("00"))
        rows_by_date = {t: [] for t in targets}
        for sid, pdf, ind_g, inst_g, margin_g in _iter_stock_groups(s, stock_ids, date_lo):
            if ind_g is None or pdf is None: continue
            pos = {d: i for i, d in enumerate(pdf["date"])}
            highs = pdf["high"].to_numpy(float); lows = pdf["low"].to_numpy(float); closes = pdf["close"].to_numpy(float)
            ibd = {d: i for i, d in enumerate(ind_g["date"])}
            ma20a = ind_g["ma20"].to_numpy(float); b60a = ind_g["bias_60"].to_numpy(float)
            for T in targets:
                p = pos.get(T)
                if p is None or p < _MIN_BARS-1 or p+_HMAX >= len(highs): continue
                ii = ibd.get(T)
                if ii is None or ii < 5: continue
                ind = ind_g.iloc[ii]; atr14 = ind.get("atr14")
                ma = [ind.get(c) for c in ("ma5","ma10","ma20","ma60")]
                c0 = highs[p]
                if (not c0 or c0<=0 or atr14 is None or pd.isna(atr14) or any(m is None or pd.isna(m) for m in ma)): continue
                cT = closes[p]; atr_pct = float(atr14)/cT if cT else None
                if atr_pct is None: continue
                ma_align = int(ma[0]>ma[1])+int(ma[1]>ma[2])+int(ma[2]>ma[3])
                m20,m20p,b60 = ma20a[ii],ma20a[ii-5],b60a[ii]
                ar = bool(cT>m20 and m20>m20p) if not (pd.isna(m20) or pd.isna(m20p)) else False
                n60 = bool(abs(b60)<15) if not pd.isna(b60) else False
                pf = ar and n60
                fh = highs[p+1:p+1+_HMAX]; fl = lows[p+1:p+1+_HMAX]
                # 各 (X,H) 純摸到 + 風險調整
                res = {}
                for H in _HS:
                    hh = fh[:H]; ll = fl[:H]
                    mh = ~np.isnan(hh); ml = ~np.isnan(ll)
                    up = (hh[mh]/c0-1.0); dn = (ll[ml]/c0-1.0)
                    if len(up)==0: continue
                    # 第一次觸及 -8% 的日(風險調整用)；用未過濾索引近似(同長度)
                    upm = fh[:H]/c0-1.0; dnm = fl[:H]/c0-1.0
                    stp = next((k for k in range(H) if not np.isnan(dnm[k]) and dnm[k]<=_STOP), None)
                    for X in _XS:
                        tgt = next((k for k in range(H) if not np.isnan(upm[k]) and upm[k]>=X/100.0), None)
                        pure = 1 if tgt is not None else 0
                        ra = 0 if tgt is None else (1 if stp is None else (1 if tgt<stp else 0))
                        res[(X,H)] = (pure, ra)
                if not res: continue
                rows_by_date[T].append({"atr_pct":atr_pct,"ma_align":ma_align,"pf":pf,"res":res})
        for T in targets:
            c = rows_by_date[T]
            if not c: continue
            pop = (2*_pr([x["atr_pct"] for x in c])+_pr([x["ma_align"] for x in c]))/3.0
            for i,x in enumerate(c): x["pop"]=float(pop[i])
        fold_dates = [targets[i*len(targets)//folds:(i+1)*len(targets)//folds] for i in range(folds)]
        def members(dates, K):
            cut = 100.0-K
            return [x for d in dates for x in rows_by_date[d] if x["pf"] and x["pop"]>=cut]
        grid = []
        for K in _KS:
            for X in _XS:
                for H in _HS:
                    allm = members(targets, K)
                    lst = [m for m in allm if (X,H) in m["res"]]
                    if not lst: continue
                    pure = np.mean([m["res"][(X,H)][0] for m in lst])*100
                    ra = np.mean([m["res"][(X,H)][1] for m in lst])*100
                    fold_pure = []
                    for fd in fold_dates:
                        fm = [m for m in members(fd,K) if (X,H) in m["res"]]
                        fold_pure.append(round(np.mean([m["res"][(X,H)][0] for m in fm])*100,1) if fm else None)
                    grid.append({"K":K,"X":X,"H":H,"n":len(lst),
                                 "pure":round(pure,1),"ra":round(ra,1),"folds":fold_pure})
        out = {"window":{"from":str(targets[0]),"to":str(targets[-1]),"entry_dates":len(targets)},
               "stop":_STOP,"grid":grid}
        print(json.dumps(out, ensure_ascii=False))
    finally:
        s.close()

if __name__ == "__main__":
    main()
