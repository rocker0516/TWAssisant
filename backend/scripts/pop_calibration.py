"""校準曲線：會噴分數 → 實際摸+10%(20日)機率，walk-forward OOS。輸出 JSON。

把清單平均(36%)拆成「這一檔自己的機率」：分數分桶看每桶實際摸+10%/風險調整率，
確認單調(分數越高機率越高)且三段樣本外穩。維持 +10%/20日/前20% 線上定義。
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

_H=20; _TGT=0.10; _STOP=-0.08; _WARMUP=200
def _pr(v): return (pd.Series(v,dtype=float).rank(pct=True)*100).to_numpy()

def main():
    global _H, _TGT
    n_dates=int(sys.argv[1]) if len(sys.argv)>1 else 240
    sample=int(sys.argv[2]) if len(sys.argv)>2 else 5
    _H=int(sys.argv[3]) if len(sys.argv)>3 else _H
    _TGT=float(sys.argv[4])/100.0 if len(sys.argv)>4 else _TGT
    folds=3
    print(f"# 校準：target 摸+{_TGT*100:.0f}%、持有 {_H} 日")
    s=SessionLocal()
    try:
        axis=s.execute(select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)).scalars().all()
        targets=sorted(axis[:len(axis)-_H][::sample][-n_dates:])
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
                c0=hi[p]
                if (not c0 or c0<=0 or atr is None or pd.isna(atr) or any(m is None or pd.isna(m) for m in ma)): continue
                cT=cl[p]; ap=float(atr)/cT if cT else None
                if ap is None: continue
                mal=int(ma[0]>ma[1])+int(ma[1]>ma[2])+int(ma[2]>ma[3])
                m20,m20p,b60=m20a[ii],m20a[ii-5],b60a[ii]
                ar=bool(cT>m20 and m20>m20p) if not (pd.isna(m20) or pd.isna(m20p)) else False
                n60=bool(abs(b60)<15) if not pd.isna(b60) else False
                pf=ar and n60
                fh=hi[p+1:p+1+_H]/c0-1.0; fl=lo[p+1:p+1+_H]/c0-1.0
                tgt=next((k for k in range(len(fh)) if not np.isnan(fh[k]) and fh[k]>=_TGT),None)
                stp=next((k for k in range(len(fl)) if not np.isnan(fl[k]) and fl[k]<=_STOP),None)
                pure=1 if tgt is not None else 0
                ra=0 if tgt is None else (1 if stp is None else (1 if tgt<stp else 0))
                rbd[T].append({"ap":ap,"mal":mal,"pf":pf,"pure":pure,"ra":ra})
        for T in targets:
            c=rbd[T]
            if not c: continue
            pop=(2*_pr([x["ap"] for x in c])+_pr([x["mal"] for x in c]))/3.0
            for i,x in enumerate(c): x["pop"]=float(pop[i])
        fold_dates=[targets[i*len(targets)//folds:(i+1)*len(targets)//folds] for i in range(folds)]
        # 分桶：全宇宙十分位(看單調) + 清單內(分數≥80)細桶
        def bucket(rows,lo,hi_):
            return [x for x in rows if x["pf"] and lo<=x["pop"]<hi_]
        allrows=[x for d in targets for x in rbd[d]]
        out={"deciles":[], "list_fine":[]}
        for lo_ in range(0,100,10):
            b=bucket(allrows,lo_,lo_+10 if lo_<90 else 100.1)
            if not b: continue
            out["deciles"].append({"lo":lo_,"hi":lo_+10,"n":len(b),
                "pure":round(np.mean([x["pure"] for x in b])*100,1),
                "ra":round(np.mean([x["ra"] for x in b])*100,1)})
        for lo_,hi_ in [(80,85),(85,90),(90,95),(95,100.1)]:
            fold_pure=[]
            for fd in fold_dates:
                fb=bucket([x for d in fd for x in rbd[d]],lo_,hi_)
                fold_pure.append(round(np.mean([x["pure"] for x in fb])*100,1) if fb else None)
            b=bucket(allrows,lo_,hi_)
            if not b: continue
            out["list_fine"].append({"lo":lo_,"hi":min(hi_,100),"n":len(b),
                "pure":round(np.mean([x["pure"] for x in b])*100,1),
                "ra":round(np.mean([x["ra"] for x in b])*100,1),"folds":fold_pure})
        out["window"]={"from":str(targets[0]),"to":str(targets[-1])}
        print(json.dumps(out,ensure_ascii=False))
    finally:
        s.close()

if __name__=="__main__": main()
