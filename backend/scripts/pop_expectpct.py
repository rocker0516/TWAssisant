"""未來期望% 剖面：每個會噴分數級距的 E[MFE]/E[淨20日]/E[MAE]/P(+10%)，walk-forward OOS。

回答「推薦改按未來期望% 排」該顯示什麼、跟現行(按摸+10%機率)差多少。進場價=當日收盤(買在收盤)。
"""
from __future__ import annotations
import sys
from datetime import timedelta
import numpy as np, pandas as pd
from sqlalchemy import select, distinct
sys.path.insert(0, __file__.replace("\\", "/").rsplit("/scripts/", 1)[0])
from app.engines.calibration import _MIN_BARS, _iter_stock_groups
from app.storage import models
from app.storage.database import SessionLocal
_H=20; _WARMUP=200
def _pr(v): return (pd.Series(v,dtype=float).rank(pct=True)*100).to_numpy()
def main():
    n=int(sys.argv[1]) if len(sys.argv)>1 else 240
    sample=int(sys.argv[2]) if len(sys.argv)>2 else 5
    s=SessionLocal()
    try:
        axis=s.execute(select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)).scalars().all()
        targets=sorted(axis[:len(axis)-_H][::sample][-n:])
        date_lo=min(targets)-timedelta(days=_WARMUP)
        stocks={st.id:st for st in s.execute(select(models.Stock)).scalars().all()}
        sids=sorted(sid for sid in stocks if not sid.startswith("00"))
        rbd={t:[] for t in targets}
        for sid,pdf,ind_g,inst_g,margin_g in _iter_stock_groups(s,sids,date_lo):
            if ind_g is None or pdf is None: continue
            pos={d:i for i,d in enumerate(pdf["date"])}
            hi=pdf["high"].to_numpy(float); lo=pdf["low"].to_numpy(float); cl=pdf["close"].to_numpy(float)
            ibd={d:i for i,d in enumerate(ind_g["date"])}
            m20a=ind_g["ma20"].to_numpy(float); b60a=ind_g["bias_60"].to_numpy(float)
            for T in targets:
                p=pos.get(T)
                if p is None or p<_MIN_BARS-1 or p+_H>=len(hi): continue
                ii=ibd.get(T)
                if ii is None or ii<5: continue
                ind=ind_g.iloc[ii]; atr=ind.get("atr14")
                ma=[ind.get(c) for c in ("ma5","ma10","ma20","ma60")]
                c0=cl[p]  # 進場=當日收盤
                if (not c0 or c0<=0 or pd.isna(c0) or atr is None or pd.isna(atr) or any(m is None or pd.isna(m) for m in ma)): continue
                ap=float(atr)/c0
                mal=int(ma[0]>ma[1])+int(ma[1]>ma[2])+int(ma[2]>ma[3])
                m20,m20p,b60=m20a[ii],m20a[ii-5],b60a[ii]
                ar=bool(c0>m20 and m20>m20p) if not (pd.isna(m20) or pd.isna(m20p)) else False
                n60=bool(abs(b60)<15) if not pd.isna(b60) else False
                if not(ar and n60): continue
                fh=hi[p+1:p+1+_H]/c0-1.0; fl=lo[p+1:p+1+_H]/c0-1.0
                cN=cl[p+_H]
                vh=fh[~np.isnan(fh)]; vl=fl[~np.isnan(fl)]
                if len(vh)==0 or len(vl)==0 or pd.isna(cN): continue
                mfe=float(vh.max()); mae=float(vl.min()); net=cN/c0-1.0
                rbd[T].append({"ap":ap,"mal":mal,"mfe":mfe,"mae":mae,"net":net,
                               "hit":1 if mfe>=0.10 else 0})
        for T in targets:
            c=rbd[T]
            if not c: continue
            pop=(2*_pr([x["ap"] for x in c])+_pr([x["mal"] for x in c]))/3.0
            for i,x in enumerate(c): x["pop"]=float(pop[i])
        allr=[x for d in targets for x in rbd[d]]
        def stat(lo,hi):
            b=[x for x in allr if lo<=x["pop"]<hi]
            if not b: return None
            return (len(b),np.mean([x["mfe"] for x in b])*100,np.mean([x["net"] for x in b])*100,
                    np.mean([x["mae"] for x in b])*100,np.mean([x["hit"] for x in b])*100)
        print(f"進場日 {len(targets)}：{targets[0]}→{targets[-1]}，進場=當日收盤，持有{_H}日\n")
        print(f"{'分數級距':>9}{'n':>8}{'E[MFE]能噴':>11}{'E[淨報酬]':>10}{'E[MAE]回撤':>11}{'P(+10%)':>9}")
        for lo in range(0,100,10):
            r=stat(lo,lo+10 if lo<90 else 100.1)
            if r: print(f"{str(lo)+'-'+str(lo+10):>9}{r[0]:>8}{r[1]:>+10.1f}%{r[2]:>+9.1f}%{r[3]:>+10.1f}%{r[4]:>8.0f}%")
        print("\n看點：E[MFE](能噴)是否隨分數單調升＝按期望漲幅排≈現行；E[淨報酬]是否也升＝真有方向alpha還是只是波動。")
    finally:
        s.close()
if __name__=="__main__": main()
