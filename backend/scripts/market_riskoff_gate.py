"""崩盤啟動偵測 → 大盤進場閘門 驗證(PIT walk-forward，研究用，不寫DB)。

個股層級「隔天不適合就警示」做不出(6候選皆敗)；改測市場層級閘門：崩盤啟動日(當天廣度
崩潰，收盤即可判)的隨後數日進場，清單摸+10%/風險調整命中是否顯著更差——差才值得做閘門。

崩盤啟動日 risk_off(D)：當日「跌逾3%家數佔比 ≥ dn3_thr%」(崩盤最乾淨的簽名，如歷史 6/8)。
對每個(週頻)進場日 T，看其前 N 交易日內是否有 risk_off → 比較清單前向結果。

用法：python scripts/market_riskoff_gate.py [n_dates] [sample] [folds] [top_pct] [stop_pct] [dn3_thr]
"""
from __future__ import annotations
import sys
from datetime import timedelta
import numpy as np, pandas as pd
from sqlalchemy import select, distinct
sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])
from app.engines.calibration import _MIN_BARS, _iter_stock_groups  # noqa: E402
from app.storage import models  # noqa: E402
from app.storage.database import SessionLocal  # noqa: E402

_H = 20; _POP_TARGET = 0.10; _WARMUP = 200

def _pct_rank(vals):
    return (pd.Series(vals, dtype=float).rank(pct=True) * 100).to_numpy()

def main() -> None:
    n_dates = int(sys.argv[1]) if len(sys.argv) > 1 else 1300  # 用日頻(sample=1)抓滿崩盤窗
    sample = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    folds = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    top_pct = float(sys.argv[4]) if len(sys.argv) > 4 else 20.0
    stop_pct = float(sys.argv[5]) if len(sys.argv) > 5 else 8.0
    dn3_thr = float(sys.argv[6]) if len(sys.argv) > 6 else 20.0
    cutoff = 100.0 - top_pct; stop = -stop_pct / 100.0
    s = SessionLocal()
    try:
        axis = s.execute(select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)).scalars().all()
        targets = sorted(axis[: len(axis) - _H][::sample][-n_dates:])
        date_lo = min(targets) - timedelta(days=_WARMUP)

        # ── 市場狀態：每日 跌>3%家數佔比 → risk_off ──
        pr = pd.DataFrame(s.execute(select(models.DailyPrice.stock_id, models.DailyPrice.date, models.DailyPrice.close)
                                    .where(models.DailyPrice.date >= date_lo)).all(), columns=["sid","date","close"])
        pr["close"] = pr["close"].astype(float)
        pr = pr.sort_values(["sid","date"])
        pr["ret"] = pr.groupby("sid")["close"].pct_change() * 100
        mkt = pr.groupby("date").agg(n=("ret","size"),
                                     med=("ret","median"),
                                     up=("ret", lambda x: (x>0).mean()*100),
                                     dn3=("ret", lambda x: (x<=-3).mean()*100)).reset_index()
        mkt["risk_off"] = mkt["dn3"] >= dn3_thr
        ro_days = sorted(mkt.loc[mkt["risk_off"], "date"].tolist())
        all_axis = mkt.sort_values("date")["date"].tolist()
        ro_set = set(ro_days)
        print(f"進場日 {len(targets)}：{targets[0]} → {targets[-1]}；崩盤啟動門檻=跌>3%家數≥{dn3_thr:.0f}%")
        print(f"全期偵測到崩盤啟動日 {len(ro_days)} 天（佔 {len(ro_days)/len(all_axis)*100:.1f}%）；最近幾次：",
              ", ".join(f"{d}(dn3={mkt.loc[mkt.date==d,'dn3'].iloc[0]:.0f}%)" for d in ro_days[-6:]))

        def days_since_ro(T):
            prev = [d for d in ro_days if d < T]
            if not prev: return 999
            # 交易日差
            i_t = all_axis.index(T); i_r = all_axis.index(prev[-1])
            return i_t - i_r

        # ── 清單前向結果（沿用 harness）──
        stocks = {st.id: st for st in s.execute(select(models.Stock)).scalars().all()}
        stock_ids = sorted(sid for sid in stocks if not sid.startswith("00"))
        rows_by_date = {t: [] for t in targets}
        for sid, pdf, ind_g, inst_g, margin_g in _iter_stock_groups(s, stock_ids, date_lo):
            if ind_g is None or pdf is None: continue
            pos = {d: i for i, d in enumerate(pdf["date"])}
            highs = pdf["high"].to_numpy(dtype=float); lows = pdf["low"].to_numpy(dtype=float)
            closes = pdf["close"].to_numpy(dtype=float)
            ind_by_date = {d: i for i, d in enumerate(ind_g["date"])}
            ma20_arr = ind_g["ma20"].to_numpy(dtype=float); bias60_arr = ind_g["bias_60"].to_numpy(dtype=float)
            for T in targets:
                p = pos.get(T)
                if p is None or p < _MIN_BARS - 1 or p + _H >= len(highs): continue
                ii = ind_by_date.get(T)
                if ii is None or ii < 5: continue
                ind = ind_g.iloc[ii]; atr14 = ind.get("atr14")
                ma = [ind.get(c) for c in ("ma5","ma10","ma20","ma60")]
                c0 = highs[p]
                if (not c0 or c0 <= 0 or atr14 is None or pd.isna(atr14) or any(m is None or pd.isna(m) for m in ma)): continue
                cT = closes[p]; atr_pct = float(atr14)/cT if cT else None
                if atr_pct is None: continue
                ma_align = int(ma[0]>ma[1])+int(ma[1]>ma[2])+int(ma[2]>ma[3])
                ma20_now, ma20_prev = ma20_arr[ii], ma20_arr[ii-5]; bias60 = bias60_arr[ii]
                above_rising = bool(cT>ma20_now and ma20_now>ma20_prev) if not (pd.isna(ma20_now) or pd.isna(ma20_prev)) else False
                near60 = bool(abs(bias60)<15) if not pd.isna(bias60) else False
                passed_filter = above_rising and near60
                fhi = highs[p+1:p+1+_H]; flo = lows[p+1:p+1+_H]
                m = ~(np.isnan(fhi)|np.isnan(flo)); fhi, flo = fhi[m], flo[m]
                if len(fhi)==0: continue
                up, dn = fhi/c0-1.0, flo/c0-1.0
                mfe, mae = float(up.max()), float(dn.min())
                hit = 1 if mfe>=_POP_TARGET else 0
                tgt = next((k for k in range(len(up)) if up[k]>=_POP_TARGET), None)
                stp = next((k for k in range(len(dn)) if dn[k]<=stop), None)
                ra = 0 if tgt is None else (1 if stp is None else (1 if tgt<stp else 0))
                rows_by_date[T].append({"atr_pct":atr_pct,"ma_align":ma_align,"passed_filter":passed_filter,
                                        "hit":hit,"ra":ra,"mae":mae})
        for T in targets:
            c = rows_by_date[T]
            if not c: continue
            pop = (2*_pct_rank([x["atr_pct"] for x in c])+_pct_rank([x["ma_align"] for x in c]))/3.0
            for i,x in enumerate(c):
                x["in_list"] = bool(x["passed_filter"] and pop[i]>=cutoff)

        # 每個進場日標記 days_since_ro
        dsince = {T: days_since_ro(T) for T in targets}
        fold_dates = [targets[i*len(targets)//folds:(i+1)*len(targets)//folds] for i in range(folds)]

        def stats(dates, lohi):
            lo, hi = lohi
            lst = [x for d in dates for x in rows_by_date[d] if x.get("in_list") and lo<=dsince[d]<=hi]
            if not lst: return None
            return (len(lst), np.mean([x["hit"] for x in lst])*100, np.mean([x["ra"] for x in lst])*100,
                    np.mean([x["mae"] for x in lst])*100)

        buckets = [("崩後1日內(隔天)",(1,1)),("崩後2-3日",(2,3)),("崩後4-5日",(4,5)),("平靜(>10日無崩)",(11,998))]
        print("\n"+"="*94)
        print("【表1 崩盤啟動後 N 日進場 vs 平靜期：清單摸+10% / 風險調整命中 / MAE】全期")
        print("="*94)
        print(f"{'窗口':<22}{'清單n':>8}{'摸+10%':>9}{'風險調整':>10}{'avg MAE':>10}")
        for name,win in buckets:
            r = stats(targets, win)
            if r: print(f"{name:<20}{r[0]:>8}{r[1]:>8.1f}%{r[2]:>9.1f}%{r[3]:>+9.1f}%")
        print("\n"+"="*94)
        print("【表2 逐段 OOS：崩後1日內(隔天) vs 平靜期 摸+10%】看閘門是否三段一致有效")
        print("="*94)
        print(f"{'段':<10}{'隔天n':>7}{'隔天摸+10':>11}{'隔天風險調整':>13}{'平靜摸+10':>11}{'平靜風險調整':>13}")
        for fi in range(folds):
            a = stats(fold_dates[fi], (1,1)); b = stats(fold_dates[fi], (11,998))
            if a and b:
                print(f"段{fi+1:<8}{a[0]:>7}{a[1]:>10.1f}%{a[2]:>12.1f}%{b[1]:>10.1f}%{b[2]:>12.1f}%")
        print("\n判讀：若『崩後隔天』摸+10%/風險調整命中三段都明顯低於平靜期 → 大盤閘門有料(崩後該停手)。")
    finally:
        s.close()

if __name__ == "__main__":
    main()
