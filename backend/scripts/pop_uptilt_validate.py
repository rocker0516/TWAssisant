"""上漲動能收斂：維持 +10% target，比較三種選股法的「方向品質」(PIT walk-forward)。

使用者要：target 一樣摸+10%，但清單收斂成「只推會上漲的」，濾掉靠對稱波動瞎甩到+10%的。
比較三種選股分數各取前 top% 清單：
  A 現行會噴   = 2·rank(atr) + 1·rank(ma_align)        （⅔ 對稱波動）
  B 上漲動能傾 = 1·rank(atr) + 2·rank(動能)             （降波動、加動能）
  C 純上漲動能 = rank(動能)
動能複合 = ret20 + 相對強度RS(ret20−大盤中位) + ma_align + macd_hist 的橫截面百分位等權。
看誰在「保住摸+10%」同時「風險調整命中更高、MAE更淺」=真會漲非亂噴。三段 OOS。
用法：python scripts/pop_uptilt_validate.py [n_dates] [sample] [folds] [top_pct] [stop_pct]
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
_H=20; _TGT=0.10; _STOP=-0.08; _WARMUP=200
def _pr(v): return (pd.Series(v,dtype=float).rank(pct=True)*100).to_numpy()
def main():
    n_dates=int(sys.argv[1]) if len(sys.argv)>1 else 240
    sample=int(sys.argv[2]) if len(sys.argv)>2 else 5
    folds=int(sys.argv[3]) if len(sys.argv)>3 else 3
    top_pct=float(sys.argv[4]) if len(sys.argv)>4 else 20.0
    stop_pct=float(sys.argv[5]) if len(sys.argv)>5 else 8.0
    cut=100.0-top_pct; stop=-stop_pct/100.0
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
            m20a=ind_g["ma20"].to_numpy(float); b60a=ind_g["bias_60"].to_numpy(float); mha=ind_g["macd_hist"].to_numpy(float)
            for T in targets:
                p=pos.get(T)
                if p is None or p<_MIN_BARS-1 or p+_H>=len(hi) or p<21: continue
                ii=ibd.get(T)
                if ii is None or ii<5: continue
                ind=ind_g.iloc[ii]; atr=ind.get("atr14")
                ma=[ind.get(c) for c in ("ma5","ma10","ma20","ma60")]
                c0=hi[p]
                if (not c0 or c0<=0 or atr is None or pd.isna(atr) or any(m is None or pd.isna(m) for m in ma)): continue
                cT=cl[p]; ap=float(atr)/cT if cT else None
                if ap is None or pd.isna(cT) or pd.isna(cl[p-20]) or cl[p-20]<=0: continue
                mal=int(ma[0]>ma[1])+int(ma[1]>ma[2])+int(ma[2]>ma[3])
                ret20=cT/cl[p-20]-1.0
                mh=mha[ii] if not pd.isna(mha[ii]) else 0.0
                m20,m20p,b60=m20a[ii],m20a[ii-5],b60a[ii]
                ar=bool(cT>m20 and m20>m20p) if not (pd.isna(m20) or pd.isna(m20p)) else False
                n60=bool(abs(b60)<15) if not pd.isna(b60) else False
                pf=ar and n60
                fh=hi[p+1:p+1+_H]/c0-1.0; fl=lo[p+1:p+1+_H]/c0-1.0
                tgt=next((k for k in range(len(fh)) if not np.isnan(fh[k]) and fh[k]>=_TGT),None)
                stp=next((k for k in range(len(fl)) if not np.isnan(fl[k]) and fl[k]<=stop),None)
                pure=1 if tgt is not None else 0
                ra=0 if tgt is None else (1 if stp is None else (1 if tgt<stp else 0))
                vl=fl[~np.isnan(fl)]
                mae=float(vl.min()) if len(vl) else 0.0
                rbd[T].append({"ap":ap,"mal":mal,"ret20":ret20,"mh":mh,"pf":pf,"pure":pure,"ra":ra,"mae":mae})
        # 每日：算 RS、動能複合、三種選股分數、各自 in_list
        for T in targets:
            c=rbd[T]
            if not c: continue
            medret=np.nanmedian([x["ret20"] for x in c])
            rs=np.array([x["ret20"]-medret for x in c])
            mom=( _pr([x["ret20"] for x in c]) + _pr(rs) + _pr([x["mal"] for x in c]) + _pr([x["mh"] for x in c]) )/4.0
            ra_atr=_pr([x["ap"] for x in c]); ra_al=_pr([x["mal"] for x in c]); ra_mom=_pr(mom)
            scores={"A":(2*ra_atr+ra_al)/3.0,"B":(ra_atr+2*ra_mom)/3.0,"C":ra_mom}
            for x in c: x["A"]=x["B"]=x["C"]=False
            pf_idx=[i for i,x in enumerate(c) if x["pf"]]
            k=max(1,int(round(len(pf_idx)*top_pct/100.0)))  # 每天各取相同檔數(前top%) 公平比
            for key,sc in scores.items():
                top=sorted(pf_idx,key=lambda i:-sc[i])[:k]
                for i in top: c[i][key]=True
        fold_dates=[targets[i*len(targets)//folds:(i+1)*len(targets)//folds] for i in range(folds)]
        def line(name,key,dates):
            lst=[x for d in dates for x in rbd[d] if x.get(key)]
            if not lst: return None
            return (len(lst),np.mean([x["pure"] for x in lst])*100,np.mean([x["ra"] for x in lst])*100,
                    np.mean([x["mae"] for x in lst])*100)
        print(f"進場日 {len(targets)}：{targets[0]}→{targets[-1]}，前{top_pct:.0f}%，target 摸+{_TGT*100:.0f}%、停損−{stop_pct:.0f}%\n")
        names={"A":"現行會噴(2atr+align)","B":"上漲動能傾(atr+2動能)","C":"純上漲動能"}
        print("="*86)
        print("【全期】三種選股法 前20%清單：摸+10% / 風險調整 / avg MAE（n相近才可比）")
        print("="*86)
        print(f"{'選股法':<24}{'n':>8}{'摸+10%':>9}{'風險調整':>10}{'avg MAE':>10}")
        for k in "ABC":
            r=line(names[k],k,targets)
            if r: print(f"{names[k]:<22}{r[0]:>8}{r[1]:>8.1f}%{r[2]:>9.1f}%{r[3]:>+9.1f}%")
        print("\n"+"="*86)
        print("【逐段 OOS】摸+10% ｜ 風險調整（看上漲動能傾/純動能 是否真的方向更乾淨且穩）")
        print("="*86)
        print(f"{'選股法':<24}"+"".join(f"{'段'+str(i+1):>16}" for i in range(folds)))
        for k in "ABC":
            cells=[]
            for fi in range(folds):
                r=line(names[k],k,fold_dates[fi])
                cells.append(f"{r[1]:.0f}%/{r[2]:.0f}%" if r else "—")
            print(f"{names[k]:<22}"+"".join(f"{c:>16}" for c in cells))
        print("\n判讀：要的是 B/C 在『摸+10%不明顯掉』下『風險調整更高、MAE更淺』且三段穩 → 收斂成「會漲」成立。")
    finally:
        s.close()
if __name__=="__main__": main()
