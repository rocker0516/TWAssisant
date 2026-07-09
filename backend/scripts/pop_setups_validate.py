"""劇本探勘：會噴是否來自多種「條件組合(setup)」而非單因子(PIT walk-forward，研究用)。

單因子平均測不到交互/異質劇本。本腳本在硬篩宇宙裡測 5 種人話劇本(明確 AND 條件組合)的
+10%/30天命中率，對比基準(全宇宙)與現行清單(會噴前20%)，三段 OOS。
低自由度、可解釋、不過配適。某劇本命中率明顯>現行清單且三段穩 → 「組合有料」、值得做劇本模型。
用法：python scripts/pop_setups_validate.py [n_dates] [sample] [folds]
"""
from __future__ import annotations
import sys
from datetime import timedelta
import numpy as np, pandas as pd
from sqlalchemy import select, distinct
sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])
from app.engines.calibration import _MIN_BARS, _INST_COLS, _iter_stock_groups
from app.storage import models
from app.storage.database import SessionLocal
_H=30; _TGT=0.10; _WARMUP=200
def _pr(v): return (pd.Series(v,dtype=float).rank(pct=True)*100).to_numpy()
def main():
    n=int(sys.argv[1]) if len(sys.argv)>1 else 240
    sample=int(sys.argv[2]) if len(sys.argv)>2 else 5
    folds=int(sys.argv[3]) if len(sys.argv)>3 else 3
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
            if inst_g is None: inst_g=pd.DataFrame(columns=_INST_COLS)
            pos={d:i for i,d in enumerate(pdf["date"])}
            hi=pdf["high"].to_numpy(float); lo=pdf["low"].to_numpy(float); cl=pdf["close"].to_numpy(float); vo=pdf["volume"].to_numpy(float)
            with np.errstate(divide="ignore",invalid="ignore"):
                rng=(hi-lo)/np.where(cl>0,cl,np.nan)
            ibd={d:i for i,d in enumerate(ind_g["date"])}
            m20a=ind_g["ma20"].to_numpy(float); b60a=ind_g["bias_60"].to_numpy(float); b20a=ind_g["bias_20"].to_numpy(float)
            kda=ind_g["kd_k"].to_numpy(float); vma=ind_g["vol_ma20"].to_numpy(float)
            inst=inst_g.sort_values("date") if not inst_g.empty else inst_g
            if not inst.empty:
                idate=inst["date"].to_numpy(); ft=(inst["foreign_net"].fillna(0)+inst["trust_net"].fillna(0)).to_numpy(float)
            mg=margin_g.sort_values("date") if (margin_g is not None and not margin_g.empty) else None
            if mg is not None:
                mdate=mg["date"].to_numpy(); mb=mg["margin_balance"].to_numpy(float); sb=mg["short_balance"].to_numpy(float)
            for T in targets:
                p=pos.get(T)
                if p is None or p<_MIN_BARS-1 or p+_H>=len(hi) or p<40: continue
                ii=ibd.get(T)
                if ii is None or ii<5: continue
                ind=ind_g.iloc[ii]; atr=ind.get("atr14"); ma=[ind.get(c) for c in ("ma5","ma10","ma20","ma60")]
                c0=cl[p]
                if (not c0 or c0<=0 or pd.isna(c0) or atr is None or pd.isna(atr) or any(m is None or pd.isna(m) for m in ma)): continue
                vm=vma[ii]
                if pd.isna(vm) or vm<500000: continue   # 流動性硬篩
                ma_align=int(ma[0]>ma[1])+int(ma[1]>ma[2])+int(ma[2]>ma[3])
                m20,m20p,b60,b20,kd=m20a[ii],m20a[ii-5],b60a[ii],b20a[ii],kda[ii]
                ar=bool(c0>m20 and m20>m20p) if not(pd.isna(m20) or pd.isna(m20p)) else False
                n60=bool(abs(b60)<15) if not pd.isna(b60) else False
                if not(ar and n60): continue   # 過會噴硬篩
                # 條件輸入
                volr=vo[p]/vm if vm>0 else 0
                hi20p=np.nanmax(hi[p-19:p])  # 不含當日的前20高
                near_high= c0>=0.98*hi20p if hi20p>0 else False
                amp=np.nanmean(rng[p-9:p+1])/np.nanmean(rng[p-39:p+1]) if np.nanmean(rng[p-39:p+1])>0 else 1
                drift=abs(cl[p]/cl[p-20]-1) if cl[p-20]>0 else 1
                # 法人20日淨買 (張)
                netbuy=0.0
                if not inst.empty:
                    j=np.searchsorted(idate,T,side="right")
                    if j>=20: netbuy=float(ft[j-20:j].sum())
                inst_ratio=netbuy/((vm/1000)*20) if vm>0 else 0
                # 券資比
                srr=0.0
                if mg is not None:
                    k=np.searchsorted(mdate,T,side="right")-1
                    if k>=0 and not pd.isna(mb[k]) and mb[k]>0 and not pd.isna(sb[k]): srr=float(sb[k]/mb[k]*100)
                fh=hi[p+1:p+1+_H]/c0-1.0; fh=fh[~np.isnan(fh)]
                if len(fh)==0: continue
                hit=1 if float(fh.max())>=_TGT else 0
                atr_pct=float(atr)/c0
                rbd[T].append(dict(atr_pct=atr_pct,ma_align=ma_align,volr=volr,near_high=near_high,
                    amp=amp,drift=drift,inst_ratio=inst_ratio,srr=srr,
                    b20=(None if pd.isna(b20) else float(b20)),kd=(None if pd.isna(kd) else float(kd)),hit=hit))
        for T in targets:
            c=rbd[T]
            if not c: continue
            pop=(2*_pr([x["atr_pct"] for x in c])+_pr([x["ma_align"] for x in c]))/3.0
            for i,x in enumerate(c): x["list"]=pop[i]>=80.0   # 現行清單=前20%
        # 劇本定義（AND 條件組合）
        def breakout(x): return x["near_high"] and x["volr"]>=1.5 and x["ma_align"]>=2
        def coil(x): return x["amp"]<0.8 and x["drift"]<0.08 and x["volr"]>=1.2
        def accum(x): return x["inst_ratio"]>0.05 and x["ma_align"]>=2
        def squeeze(x): return x["srr"]>=10 and x["ma_align"]>=2 and x["volr"]>=1.2
        def pullback(x): return x["b20"] is not None and -3<=x["b20"]<=4 and x["kd"] is not None and x["kd"]<50 and x["ma_align"]>=2
        setups={"基準(全硬篩宇宙)":lambda x:True,"現行清單(會噴前20%)":lambda x:x["list"],
                "突破型(創高+量增+多排)":breakout,"盤整彈簧(收斂+橫盤+量起)":coil,
                "籌碼吸貨(法人買超+多排)":accum,"軋空(高券資比+多排+量)":squeeze,"回踩均線(貼月線+KD低)":pullback}
        fold_dates=[targets[i*len(targets)//folds:(i+1)*len(targets)//folds] for i in range(folds)]
        print(f"進場日 {len(targets)}：{targets[0]}→{targets[-1]}，target 摸+10%/{_H}日，三段 OOS\n")
        print(f"{'劇本':<26}{'總n':>8}{'命中率':>8}   三段OOS命中")
        print("-"*70)
        allr=[x for d in targets for x in rbd[d]]
        for nm,fn in setups.items():
            m=[x for x in allr if fn(x)]
            if not m: continue
            allhit=np.mean([x["hit"] for x in m])*100
            fc=[]
            for fd in fold_dates:
                fm=[x for d in fd for x in rbd[d] if fn(x)]
                fc.append(f"{np.mean([x['hit'] for x in fm])*100:.0f}%" if fm else "—")
            print(f"{nm:<24}{len(m):>8}{allhit:>7.1f}%   {' / '.join(fc)}")
        print("\n判讀：某劇本命中率明顯 > 現行清單、三段都穩、n 夠大 → 會噴有可利用的『組合劇本』；")
        print("若全部 ≈ 現行清單或更低 → 組合也沒額外料，vol+trend 確為天花板。")
    finally:
        s.close()
if __name__=="__main__": main()
