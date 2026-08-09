"""機器暴搜(最後確認)：全因子 GBM 能否找到打贏 2因子分數的『隱藏組合』(PIT 時序OOS)。

紀律：所有因子餵 HistGradientBoosting(自動交互/缺值)，時序早⅔訓練、晚⅓完全沒碰當裁判，
跟現行 2因子會噴分數在同一測試集、選同樣檔數(每日前20%)比實際命中率。贏不過=天花板實錘。
另印特徵重要度 + 深度3可解釋樹(看機器找到的劇本)。target=摸+10%/30日(進場=收盤)。
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
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.tree import DecisionTreeClassifier, export_text
from sklearn.metrics import roc_auc_score
_H=30; _TGT=0.10; _WARMUP=200
FEATS=["atr_pct","ma_align","volr","near_high","amp","drift","inst_ratio","srr","bias20","kd","macd_hist","ret20","rs"]
def main():
    n=int(sys.argv[1]) if len(sys.argv)>1 else 420
    sample=int(sys.argv[2]) if len(sys.argv)>2 else 3
    s=SessionLocal()
    try:
        axis=s.execute(select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)).scalars().all()
        targets=sorted(axis[:len(axis)-_H][::sample][-n:])
        date_lo=min(targets)-timedelta(days=_WARMUP)
        stocks={st.id:st for st in s.execute(select(models.Stock)).scalars().all()}
        sids=sorted(sid for sid in stocks if not sid.startswith("00"))
        rows=[]
        for sid,pdf,ind_g,inst_g,margin_g in _iter_stock_groups(s,sids,date_lo):
            if ind_g is None or pdf is None: continue
            if inst_g is None: inst_g=pd.DataFrame(columns=_INST_COLS)
            pos={d:i for i,d in enumerate(pdf["date"])}
            hi=pdf["high"].to_numpy(float); lo=pdf["low"].to_numpy(float); cl=pdf["close"].to_numpy(float); vo=pdf["volume"].to_numpy(float)
            with np.errstate(divide="ignore",invalid="ignore"): rng=(hi-lo)/np.where(cl>0,cl,np.nan)
            ibd={d:i for i,d in enumerate(ind_g["date"])}
            m20a=ind_g["ma20"].to_numpy(float);b60a=ind_g["bias_60"].to_numpy(float);b20a=ind_g["bias_20"].to_numpy(float)
            kda=ind_g["kd_k"].to_numpy(float);vma=ind_g["vol_ma20"].to_numpy(float);mha=ind_g["macd_hist"].to_numpy(float)
            inst=inst_g.sort_values("date") if not inst_g.empty else inst_g
            if not inst.empty: idate=inst["date"].to_numpy(); ft=(inst["foreign_net"].fillna(0)+inst["trust_net"].fillna(0)).to_numpy(float)
            mg=margin_g.sort_values("date") if (margin_g is not None and not margin_g.empty) else None
            if mg is not None: mdate=mg["date"].to_numpy();mb=mg["margin_balance"].to_numpy(float);sb=mg["short_balance"].to_numpy(float)
            for T in targets:
                p=pos.get(T)
                if p is None or p<_MIN_BARS-1 or p+_H>=len(hi) or p<40: continue
                ii=ibd.get(T)
                if ii is None or ii<5: continue
                ind=ind_g.iloc[ii];atr=ind.get("atr14");ma=[ind.get(c) for c in ("ma5","ma10","ma20","ma60")]
                c0=cl[p]
                if (not c0 or c0<=0 or pd.isna(c0) or atr is None or pd.isna(atr) or any(m is None or pd.isna(m) for m in ma)): continue
                vm=vma[ii]
                if pd.isna(vm) or vm<500000: continue
                m20,m20p,b60=m20a[ii],m20a[ii-5],b60a[ii]
                ar=bool(c0>m20 and m20>m20p) if not(pd.isna(m20) or pd.isna(m20p)) else False
                n60=bool(abs(b60)<15) if not pd.isna(b60) else False
                if not(ar and n60): continue
                ma_align=int(ma[0]>ma[1])+int(ma[1]>ma[2])+int(ma[2]>ma[3])
                hi20p=np.nanmax(hi[p-19:p])
                netbuy=0.0
                if not inst.empty:
                    j=np.searchsorted(idate,T,side="right")
                    if j>=20: netbuy=float(ft[j-20:j].sum())
                srr=np.nan
                if mg is not None:
                    k=np.searchsorted(mdate,T,side="right")-1
                    if k>=0 and not pd.isna(mb[k]) and mb[k]>0 and not pd.isna(sb[k]): srr=float(sb[k]/mb[k]*100)
                fh=hi[p+1:p+1+_H]/c0-1.0; fh=fh[~np.isnan(fh)]
                if len(fh)==0: continue
                rows.append(dict(date=T,hit=1 if float(fh.max())>=_TGT else 0,
                    atr_pct=float(atr)/c0,ma_align=ma_align,volr=vo[p]/vm if vm>0 else np.nan,
                    near_high=(c0/hi20p if hi20p>0 else np.nan),
                    amp=(np.nanmean(rng[p-9:p+1])/np.nanmean(rng[p-39:p+1]) if np.nanmean(rng[p-39:p+1])>0 else np.nan),
                    drift=(abs(cl[p]/cl[p-20]-1) if cl[p-20]>0 else np.nan),
                    inst_ratio=netbuy/((vm/1000)*20) if vm>0 else np.nan, srr=srr,
                    bias20=(np.nan if pd.isna(b20a[ii]) else float(b20a[ii])),
                    kd=(np.nan if pd.isna(kda[ii]) else float(kda[ii])),
                    macd_hist=(np.nan if pd.isna(mha[ii]) else float(mha[ii])),
                    ret20=cl[p]/cl[p-20]-1 if cl[p-20]>0 else np.nan))
        df=pd.DataFrame(rows)
        df["rs"]=df["ret20"]-df.groupby("date")["ret20"].transform("median")
        udates=sorted(df["date"].unique()); cut=udates[int(len(udates)*2/3)]
        tr=df[df["date"]<cut].copy(); te=df[df["date"]>=cut].copy()
        print(f"全 {len(df):,} 列；訓練 {len(tr):,}(<{cut}) / 測試 {len(te):,}(≥{cut})；測試=完全沒碰過\n")
        Xtr,ytr=tr[FEATS],tr["hit"]; Xte,yte=te[FEATS],te["hit"]
        gb=HistGradientBoostingClassifier(max_depth=4,learning_rate=0.05,max_iter=300,
            l2_regularization=1.0,min_samples_leaf=200,random_state=0,validation_fraction=0.15)
        gb.fit(Xtr,ytr)
        te=te.assign(prob=gb.predict_proba(Xte)[:,1])
        # 2因子分數(每日橫截面rank)
        for d,g in te.groupby("date"):
            te.loc[g.index,"pop"]=(2*g["atr_pct"].rank(pct=True)+g["ma_align"].rank(pct=True))/3.0
        # 同檔數比較：每日各取前20%
        def sel_rate(col):
            hits=[]
            for d,g in te.groupby("date"):
                k=max(1,int(round(len(g)*0.2)))
                top=g.nlargest(k,col); hits.append(top["hit"].mean())
            return np.mean(hits)*100
        auc_gb=roc_auc_score(yte,te["prob"]); auc_atr=roc_auc_score(yte,te["atr_pct"])
        print("="*64)
        print("【OOS 測試集 同檔數(每日前20%)實際摸+10%/30日命中率】")
        print("="*64)
        print(f"  全測試集基準            {yte.mean()*100:>6.1f}%")
        print(f"  2因子會噴分數 前20%      {sel_rate('pop'):>6.1f}%   (AUC atr={auc_atr:.3f})")
        print(f"  GBM 機器暴搜 前20%       {sel_rate('prob'):>6.1f}%   (AUC gbm={auc_gb:.3f})")
        # 過配適檢查：訓練集自身命中
        tr2=tr.assign(prob=gb.predict_proba(Xtr)[:,1])
        trhits=[]
        for d,g in tr2.groupby("date"):
            k=max(1,int(round(len(g)*0.2))); trhits.append(g.nlargest(k,"prob")["hit"].mean())
        print(f"\n  (過配適檢查) GBM 在訓練集前20% 命中 {np.mean(trhits)*100:.1f}% vs 測試 {sel_rate('prob'):.1f}%")
        print("\n特徵重要度(permutation 略，用 GBM 內建 split-based 近似)：")
        # HistGB 無內建 importance → 用單調掃描近似：各特徵單獨 AUC
        for f in FEATS:
            try: print(f"  {f:<11} 單獨AUC {roc_auc_score(yte, te[f].fillna(te[f].median())):.3f}")
            except Exception: pass
        print("\n深度3可解釋樹(機器找到的『劇本』)：")
        dt=DecisionTreeClassifier(max_depth=3,min_samples_leaf=500,random_state=0)
        dt.fit(Xtr.fillna(Xtr.median()),ytr)
        print(export_text(dt,feature_names=FEATS,max_depth=3))
        print("判讀：GBM 前20% 命中 ≈ 或 ≤ 2因子分數 → 暴搜也找不到更好組合，天花板實錘。")
    finally:
        s.close()
if __name__=="__main__": main()
