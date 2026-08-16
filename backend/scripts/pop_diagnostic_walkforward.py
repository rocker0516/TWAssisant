"""會噴清單 walk-forward 診斷（PIT，研究用，不寫DB）。

回答「會噴分數＋信心度這樣推薦可不可信」，並指出優化該打哪條槓桿。複刻線上會噴清單
（過硬篩：站上上揚月線＋距季線<15%；會噴分數＝(2·rank(atr_pct)+rank(ma_align))/3×100
取前 top_pct%），把時間軸按時序切 N 段逐段獨立量，跑三張表：

  表1 真實清單命中率：清單摸+10%率 vs 過硬篩宇宙基準 + lift，逐段（看 OOS 是否維持）。
  表2 命中率 vs 大盤 regime：以當日「站上上揚月線家數佔比」(breadth) 把進場日分三桶，
      看清單命中率怎麼隨大盤環境變——驗『大盤 regime 條件化』(槓桿A)。
  表3 命中 vs 回撤 + 風險調整命中：清單成員的 MFE/MAE 分布，及「先摸+10% 還是先被
      −stop% 打到」的風險調整命中率——驗『改 target 成風險調整』(槓桿B)，並量化純 MFE
      命中率高估了多少。

進場價＝進場日當天最高價（保守，與線上成效回測一致）；只看進場日之後 H 根。
用法：python scripts/pop_diagnostic_walkforward.py [n_dates] [sample] [folds] [top_pct] [stop_pct]
"""

from __future__ import annotations

import sys
from datetime import timedelta

import numpy as np
import pandas as pd
from sqlalchemy import distinct, select

sys.path.insert(0, __file__.replace("\\", "/").rsplit("/scripts/", 1)[0])

from app.engines.calibration import _INST_COLS, _MIN_BARS, _iter_stock_groups  # noqa: E402
from app.storage import models  # noqa: E402
from app.storage.database import SessionLocal  # noqa: E402

_H = 20             # 未來交易日（與會噴定義一致）
_POP_TARGET = 0.10  # 「會噴」門檻：持有期間摸到 +10%
_WARMUP = 200       # 往前多抓的日曆天（確保 n_bars≥60 且 ma20 有 5 列回看）


def _pct_rank(vals):
    s = pd.Series(vals, dtype=float)
    return (s.rank(pct=True) * 100).to_numpy()


def main() -> None:
    n_dates = int(sys.argv[1]) if len(sys.argv) > 1 else 260
    sample = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    folds = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    top_pct = float(sys.argv[4]) if len(sys.argv) > 4 else 20.0
    stop_pct = float(sys.argv[5]) if len(sys.argv) > 5 else 8.0
    cutoff = 100.0 - top_pct
    session = SessionLocal()
    try:
        axis = session.execute(
            select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)
        ).scalars().all()
        eligible = axis[: len(axis) - _H]
        targets = sorted(eligible[::sample][-n_dates:])
        print(f"進場日 {len(targets)} 個：{targets[0]} → {targets[-1]}，切 {folds} 段")
        print(f"清單＝過硬篩(站上上揚月線＋|乖離季線|<15%)＋會噴分數前 {top_pct:.0f}%；"
              f"停損門檻 −{stop_pct:.0f}%；進場價=當日最高價\n")

        stocks = {s.id: s for s in session.execute(select(models.Stock)).scalars().all()}
        stock_ids = sorted(sid for sid in stocks if not sid.startswith("00"))  # 排除 ETF
        date_lo = min(targets) - timedelta(days=_WARMUP)
        stop = -stop_pct / 100.0

        rows_by_date = {t: [] for t in targets}
        for sid, pdf, ind_g, inst_g, margin_g in _iter_stock_groups(session, stock_ids, date_lo):
            if ind_g is None or pdf is None:
                continue
            pos = {d: i for i, d in enumerate(pdf["date"])}
            highs = pdf["high"].to_numpy(dtype=float)
            lows = pdf["low"].to_numpy(dtype=float)
            closes = pdf["close"].to_numpy(dtype=float)
            ind_by_date = {d: i for i, d in enumerate(ind_g["date"])}
            ma20_arr = ind_g["ma20"].to_numpy(dtype=float)
            bias60_arr = ind_g["bias_60"].to_numpy(dtype=float)

            for T in targets:
                p = pos.get(T)
                if p is None or p < _MIN_BARS - 1 or p + _H >= len(highs):
                    continue
                ii = ind_by_date.get(T)
                if ii is None or ii < 5:
                    continue
                ind = ind_g.iloc[ii]
                atr14 = ind.get("atr14")
                ma = [ind.get(c) for c in ("ma5", "ma10", "ma20", "ma60")]
                c0 = highs[p]
                if (not c0 or c0 <= 0 or atr14 is None or pd.isna(atr14)
                        or any(m is None or pd.isna(m) for m in ma)):
                    continue
                cT = closes[p]
                atr_pct = float(atr14) / cT if cT else None
                if atr_pct is None:
                    continue
                ma_align = int(ma[0] > ma[1]) + int(ma[1] > ma[2]) + int(ma[2] > ma[3])

                # 硬篩（複刻 AboveRisingMa20 + NearMa60）
                ma20_now, ma20_prev = ma20_arr[ii], ma20_arr[ii - 5]
                bias60 = bias60_arr[ii]
                above_rising = bool(cT > ma20_now and ma20_now > ma20_prev) if not (
                    pd.isna(ma20_now) or pd.isna(ma20_prev)) else False
                near60 = bool(abs(bias60) < 15) if not pd.isna(bias60) else False
                passed_filter = above_rising and near60

                # 結果：MFE/MAE + 風險調整命中（先摸+10% 還是先被 stop 打到，同日視為先 stop）
                fhi = highs[p + 1: p + 1 + _H]
                flo = lows[p + 1: p + 1 + _H]
                m = ~(np.isnan(fhi) | np.isnan(flo))
                fhi, flo = fhi[m], flo[m]
                if len(fhi) == 0:
                    continue
                up = fhi / c0 - 1.0
                dn = flo / c0 - 1.0
                mfe = float(up.max())
                mae = float(dn.min())
                hit = 1 if mfe >= _POP_TARGET else 0
                tgt_day = next((k for k in range(len(up)) if up[k] >= _POP_TARGET), None)
                stp_day = next((k for k in range(len(dn)) if dn[k] <= stop), None)
                if tgt_day is None:
                    ra_hit = 0
                elif stp_day is None:
                    ra_hit = 1
                else:
                    ra_hit = 1 if tgt_day < stp_day else 0  # 同日(tgt==stp)算先停損→0

                rows_by_date[T].append({
                    "atr_pct": atr_pct, "ma_align": ma_align, "above_rising": above_rising,
                    "passed_filter": passed_filter, "hit": hit, "ra_hit": ra_hit,
                    "mfe": mfe, "mae": mae,
                })

        # 逐日算會噴分數(全市場橫截面 rank)+ regime breadth；標記是否進清單
        breadth_by_date = {}
        for T in targets:
            cands = rows_by_date[T]
            if not cands:
                continue
            pop = (2 * _pct_rank([c["atr_pct"] for c in cands])
                   + _pct_rank([c["ma_align"] for c in cands])) / 3.0
            for c, pv in zip(cands, pop):
                c["pop"] = float(pv)
                c["in_list"] = bool(c["passed_filter"] and pv >= cutoff)
            breadth_by_date[T] = float(np.mean([c["above_rising"] for c in cands]))

        fold_dates = [targets[i * len(targets) // folds:(i + 1) * len(targets) // folds]
                      for i in range(folds)]

        def agg(dates, pred):
            lst = [c for d in dates for c in rows_by_date[d] if pred(c)]
            return lst

        # ── 表1：真實清單命中率，逐段 ──
        print("=" * 88)
        print("【表1 真實清單命中率】清單摸+10%率 vs 過硬篩宇宙基準 + lift，逐段(看 OOS 是否維持)")
        print("=" * 88)
        print(f"{'段(期間)':<30}{'宇宙n':>8}{'基準':>9}{'清單n':>8}{'清單命中':>10}{'lift':>7}")
        print("-" * 88)
        for fi in range(folds):
            uni = agg(fold_dates[fi], lambda c: c["passed_filter"])
            lst = agg(fold_dates[fi], lambda c: c.get("in_list"))
            if not uni or not lst:
                continue
            pb = np.mean([c["hit"] for c in uni])
            pl = np.mean([c["hit"] for c in lst])
            span = f"{fold_dates[fi][0]}~{fold_dates[fi][-1]}"
            print(f"{('段'+str(fi+1)+' '+span):<30}{len(uni):>8}{pb*100:>8.1f}%"
                  f"{len(lst):>8}{pl*100:>9.1f}%{(pl/pb if pb else float('nan')):>7.2f}")
        uni_all = agg(targets, lambda c: c["passed_filter"])
        lst_all = agg(targets, lambda c: c.get("in_list"))
        pba, pla = np.mean([c["hit"] for c in uni_all]), np.mean([c["hit"] for c in lst_all])
        print("-" * 88)
        print(f"{'全期':<30}{len(uni_all):>8}{pba*100:>8.1f}%{len(lst_all):>8}"
              f"{pla*100:>9.1f}%{(pla/pba if pba else float('nan')):>7.2f}")

        # ── 表2：命中率 vs 大盤 regime（breadth 三分位）──
        print("\n" + "=" * 88)
        print("【表2 命中率 vs 大盤 regime】以當日『站上上揚月線家數佔比』分三桶(驗槓桿A)")
        print("=" * 88)
        bvals = sorted(breadth_by_date.values())
        if len(bvals) >= 6:
            q1, q2 = bvals[len(bvals) // 3], bvals[2 * len(bvals) // 3]
            buckets = {"risk-off(低breadth)": [], "中性": [], "risk-on(高breadth)": []}
            for T in targets:
                b = breadth_by_date.get(T)
                if b is None:
                    continue
                key = "risk-off(低breadth)" if b <= q1 else ("中性" if b <= q2 else "risk-on(高breadth)")
                buckets[key].append(T)
            print(f"breadth 三分位門檻：≤{q1*100:.0f}% / {q1*100:.0f}~{q2*100:.0f}% / >{q2*100:.0f}%\n")
            print(f"{'regime':<24}{'進場日':>7}{'清單n':>8}{'清單命中':>10}{'基準':>9}{'lift':>7}")
            print("-" * 88)
            for key, ds in buckets.items():
                uni = agg(ds, lambda c: c["passed_filter"])
                lst = agg(ds, lambda c: c.get("in_list"))
                if not uni or not lst:
                    print(f"{key:<24}{len(ds):>7}{'—':>8}")
                    continue
                pb = np.mean([c["hit"] for c in uni])
                pl = np.mean([c["hit"] for c in lst])
                print(f"{key:<22}{len(ds):>7}{len(lst):>8}{pl*100:>9.1f}%"
                      f"{pb*100:>8.1f}%{(pl/pb if pb else float('nan')):>7.2f}")
            print("\n若清單命中率在 risk-off 桶明顯坍縮/lift<1 → 大盤 regime 條件化(槓桿A)有料。")

        # ── 表3：命中 vs 回撤 + 風險調整命中 ──
        print("\n" + "=" * 88)
        print(f"【表3 命中 vs 回撤】清單成員 MFE/MAE 分布 + 風險調整命中(先摸+10% vs 先 −{stop_pct:.0f}%)")
        print("=" * 88)
        print(f"{'段(期間)':<30}{'清單n':>8}{'純摸+10%':>10}{'風險調整命中':>13}{'高估':>7}"
              f"{'avg MFE':>9}{'avg MAE':>9}")
        print("-" * 88)
        for fi in list(range(folds)) + [None]:
            ds = targets if fi is None else fold_dates[fi]
            lst = agg(ds, lambda c: c.get("in_list"))
            if not lst:
                continue
            ph = np.mean([c["hit"] for c in lst])
            pr = np.mean([c["ra_hit"] for c in lst])
            mfe = np.mean([c["mfe"] for c in lst])
            mae = np.mean([c["mae"] for c in lst])
            label = "全期" if fi is None else f"段{fi+1} {ds[0]}~{ds[-1]}"
            print(f"{label:<30}{len(lst):>8}{ph*100:>9.1f}%{pr*100:>12.1f}%"
                  f"{(ph-pr)*100:>+6.1f}pp{mfe*100:>+8.1f}%{mae*100:>+8.1f}%")
        print(f"\n『純摸+10%』= 持有期間曾摸到(線上現用)；『風險調整命中』= 在被 −{stop_pct:.0f}% 打到前先摸到。")
        print("兩者差(高估)= 純命中率把『先崩才噴/根本沒撐到』的單也算成功，高估了多少真實可交易性。")
    finally:
        session.close()


if __name__ == "__main__":
    main()
