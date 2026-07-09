"""槓桿B 候選②：真支撐距離 對『風險調整命中』(PIT walk-forward，研究用，不寫DB)。

承券資比(pop_squeeze_validate.py)認賠——它過不了OOS一致性、且**不壓下檔**(高券MAE沒變淺)。
支撐距離是三候選裡唯一**直接針對縮小那 −12% 回撤**設計：離客觀支撐越近，續跌空間越小，理論上
ra_hit 更高且 MAE 更淺。也最貼近使用者最早問的「不適合進場/警示」(離支撐遠=下檔風險大)。

支撐＝複刻線上 `support.detect_levels`(均線群+分形前低+量價套牢區，PIT 餵 ≤T 近500根量價+當日
均線；ma120/240 calibration未載故為None，長均支撐稍弱)。特徵＝**最近支撐距離%**(低=貼支撐=有利)。
守反過度配適紀律：只驗這一條、三段OOS、Δra三段一致為正且MAE變淺才算過關，否則認賠。

  表A 控制波動：支撐距離 近半−遠半 的 Δra vs Δpure，逐段。
  表B 清單內：貼支撐半 vs 遠離半，比 ra_hit / 純命中 / avg MAE。
  表C 事件：清單內最貼支撐(dist bottom 20%) vs 其餘，含 z。

用法：python scripts/pop_support_validate.py [n_dates] [sample] [folds] [top_pct] [stop_pct] [near%]
"""

from __future__ import annotations

import sys
from datetime import timedelta

import numpy as np
import pandas as pd
from sqlalchemy import distinct, select

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])

from app.engines.calibration import _MIN_BARS, _iter_stock_groups  # noqa: E402
from app.engines.support import detect_levels  # noqa: E402
from app.storage import models  # noqa: E402
from app.storage.database import SessionLocal  # noqa: E402

_H = 20
_POP_TARGET = 0.10
_WARMUP = 760        # ~500 交易日，讓 detect_levels 對最早的進場日也有足夠歷史
_LVL_WIN = 500       # 與線上 support._LEVELS_WINDOW 一致


def _pct_rank(vals):
    return (pd.Series(vals, dtype=float).rank(pct=True) * 100).to_numpy()


def _half_diff(rows, key, favor_low=True):
    """近半−遠半 的 key 平均差(有利方向)。favor_low=支撐距離低=有利。樣本不足回 None。"""
    rs = [r for r in rows if r.get("sdist") is not None and r.get(key) is not None]
    if len(rs) < 20:
        return None
    rs.sort(key=lambda r: r["sdist"])  # 升冪：前半=近支撐=有利
    h = len(rs) // 2
    fav = np.mean([r[key] for r in rs[:h]])
    unf = np.mean([r[key] for r in rs[-h:]])
    return fav - unf


def main() -> None:
    n_dates = int(sys.argv[1]) if len(sys.argv) > 1 else 260
    sample = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    folds = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    top_pct = float(sys.argv[4]) if len(sys.argv) > 4 else 20.0
    stop_pct = float(sys.argv[5]) if len(sys.argv) > 5 else 8.0
    near_pct = float(sys.argv[6]) if len(sys.argv) > 6 else 20.0
    cutoff = 100.0 - top_pct
    stop = -stop_pct / 100.0
    session = SessionLocal()
    try:
        axis = session.execute(
            select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)
        ).scalars().all()
        targets = sorted(axis[: len(axis) - _H][::sample][-n_dates:])
        print(f"進場日 {len(targets)}：{targets[0]} → {targets[-1]}，切 {folds} 段；"
              f"清單=前{top_pct:.0f}%，停損−{stop_pct:.0f}%，支撐距離低=有利、事件near=bottom{near_pct:.0f}%\n")

        stocks = {s.id: s for s in session.execute(select(models.Stock)).scalars().all()}
        stock_ids = sorted(sid for sid in stocks if not sid.startswith("00"))
        date_lo = min(targets) - timedelta(days=_WARMUP)

        rows_by_date = {t: [] for t in targets}
        n_with = n_total = 0
        for sid, pdf, ind_g, inst_g, margin_g in _iter_stock_groups(session, stock_ids, date_lo):
            if ind_g is None or pdf is None:
                continue
            pos = {d: i for i, d in enumerate(pdf["date"])}
            highs = pdf["high"].to_numpy(dtype=float)
            lows = pdf["low"].to_numpy(dtype=float)
            closes = pdf["close"].to_numpy(dtype=float)
            ind_by_date = {d: i for i, d in enumerate(ind_g["date"])}
            ma20_arr = ind_g["ma20"].to_numpy(dtype=float)
            ma60_arr = ind_g["ma60"].to_numpy(dtype=float)
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
                ma20_now, ma20_prev = ma20_arr[ii], ma20_arr[ii - 5]
                bias60 = bias60_arr[ii]
                above_rising = bool(cT > ma20_now and ma20_now > ma20_prev) if not (
                    pd.isna(ma20_now) or pd.isna(ma20_prev)) else False
                near60 = bool(abs(bias60) < 15) if not pd.isna(bias60) else False
                passed_filter = above_rising and near60

                # 最近支撐距離（PIT：≤T 近 _LVL_WIN 根 + 當日均線）
                sdist = None
                win = pdf.iloc[max(0, p - _LVL_WIN + 1): p + 1]
                mas = {"ma20": float(ma20_arr[ii]) if not pd.isna(ma20_arr[ii]) else None,
                       "ma60": float(ma60_arr[ii]) if not pd.isna(ma60_arr[ii]) else None,
                       "ma120": None, "ma240": None}
                levels = detect_levels(win, cT, mas)
                sups = [l for l in levels if l.kind == "support"]
                if sups:
                    sdist = min(abs(l.distance_pct) for l in sups)  # 最近支撐距離(%)

                fhi = highs[p + 1: p + 1 + _H]
                flo = lows[p + 1: p + 1 + _H]
                m = ~(np.isnan(fhi) | np.isnan(flo))
                fhi, flo = fhi[m], flo[m]
                if len(fhi) == 0:
                    continue
                up, dn = fhi / c0 - 1.0, flo / c0 - 1.0
                mfe, mae = float(up.max()), float(dn.min())
                hit = 1 if mfe >= _POP_TARGET else 0
                tgt_day = next((kk for kk in range(len(up)) if up[kk] >= _POP_TARGET), None)
                stp_day = next((kk for kk in range(len(dn)) if dn[kk] <= stop), None)
                ra_hit = 0 if tgt_day is None else (1 if stp_day is None else (1 if tgt_day < stp_day else 0))

                n_total += 1
                if sdist is not None:
                    n_with += 1
                rows_by_date[T].append({
                    "atr_pct": atr_pct, "ma_align": ma_align, "passed_filter": passed_filter,
                    "sdist": sdist, "hit": hit, "ra_hit": ra_hit, "mfe": mfe, "mae": mae,
                })

        for T in targets:
            cands = rows_by_date[T]
            if not cands:
                continue
            pop = (2 * _pct_rank([c["atr_pct"] for c in cands])
                   + _pct_rank([c["ma_align"] for c in cands])) / 3.0
            for i, c in enumerate(cands):
                c["pop"] = float(pop[i])
                c["in_list"] = bool(c["passed_filter"] and pop[i] >= cutoff)

        cov = n_with / n_total * 100 if n_total else 0
        sd_all = [c["sdist"] for d in targets for c in rows_by_date[d] if c.get("sdist") is not None]
        print(f"支撐覆蓋率 {cov:.0f}%（{n_with:,}/{n_total:,}）；最近支撐距離 中位 {np.median(sd_all):.1f}% / "
              f"p10 {np.percentile(sd_all,10):.1f}% / p90 {np.percentile(sd_all,90):.1f}%")

        fold_dates = [targets[i * len(targets) // folds:(i + 1) * len(targets) // folds]
                      for i in range(folds)]

        def agg(dates, pred):
            return [c for d in dates for c in rows_by_date[d] if pred(c)]

        # ── 表A：控制波動，支撐距離 近半−遠半，Δra vs Δpure ──
        print("\n" + "=" * 92)
        print("【表A 控制波動：支撐距離 近半−遠半 命中差(pp)】Δra=風險調整、Δpure=純摸到；近支撐=有利")
        print("=" * 92)
        print(f"{'':<10}" + "".join(f"{'段'+str(i+1)+' Δra/Δpure':>22}" for i in range(folds)))
        cells = []
        for fi in range(folds):
            dra, dpu = [], []
            for d in fold_dates[fi]:
                rs = [r for r in rows_by_date[d] if r["passed_filter"] and r["ma_align"] >= 2 and r.get("sdist") is not None]
                if len(rs) < 50:
                    continue
                rs.sort(key=lambda r: r["atr_pct"])
                k = len(rs) // 5
                for qi in range(5):
                    grp = rs[qi * k:(qi + 1) * k] if qi < 4 else rs[qi * k:]
                    a, b = _half_diff(grp, "ra_hit"), _half_diff(grp, "hit")
                    if a is not None:
                        dra.append(a)
                    if b is not None:
                        dpu.append(b)
            cells.append(f"{np.mean(dra)*100:+.1f}/{np.mean(dpu)*100:+.1f}" if dra else "—")
        print(f"{'支撐距離':<7}" + "".join(f"{c:>22}" for c in cells))

        # ── 表B：清單內 貼支撐半 vs 遠離半 ──
        print("\n" + "=" * 92)
        print("【表B 清單(前20%會噴)內：貼支撐半 vs 遠離半】關鍵看 Δra>0 且 貼支撐MAE 變淺")
        print("=" * 92)
        print(f"  {'段':<18}{'n':>7}{'貼支ra':>9}{'遠離ra':>9}{'Δra':>8}{'貼支純':>9}{'貼支MAE':>10}{'遠離MAE':>10}")
        for fi in list(range(folds)) + [None]:
            ds = targets if fi is None else fold_dates[fi]
            rs = agg(ds, lambda c: c.get("in_list") and c.get("sdist") is not None)
            if len(rs) < 20:
                continue
            rs.sort(key=lambda c: c["sdist"])
            h = len(rs) // 2
            near, far = rs[:h], rs[-h:]
            label = "全期" if fi is None else f"段{fi+1}"
            print(f"  {label:<18}{len(rs):>7}{np.mean([c['ra_hit'] for c in near])*100:>8.1f}%"
                  f"{np.mean([c['ra_hit'] for c in far])*100:>8.1f}%"
                  f"{(np.mean([c['ra_hit'] for c in near])-np.mean([c['ra_hit'] for c in far]))*100:>+7.1f}"
                  f"{np.mean([c['hit'] for c in near])*100:>8.1f}%"
                  f"{np.mean([c['mae'] for c in near])*100:>+9.1f}%{np.mean([c['mae'] for c in far])*100:>+9.1f}%")

        # ── 表C：事件：清單內最貼支撐(bottom near%) vs 其餘，含 z ──
        print("\n" + "=" * 92)
        print(f"【表C 事件：清單內最貼支撐 bottom{near_pct:.0f}% vs 其餘】")
        print("=" * 92)
        print(f"  {'段':<18}{'貼支n':>7}{'貼支ra':>9}{'其餘ra':>9}{'Δra':>8}{'z':>7}{'貼支MAE':>10}{'門檻距離':>10}")
        for fi in list(range(folds)) + [None]:
            ds = targets if fi is None else fold_dates[fi]
            rs = agg(ds, lambda c: c.get("in_list") and c.get("sdist") is not None)
            if len(rs) < 30:
                continue
            thr = np.percentile([c["sdist"] for c in rs], near_pct)
            near = [c for c in rs if c["sdist"] <= thr]
            rest = [c for c in rs if c["sdist"] > thr]
            if len(near) < 10 or len(rest) < 10:
                continue
            pn, prr = np.mean([c["ra_hit"] for c in near]), np.mean([c["ra_hit"] for c in rest])
            z = (pn - prr) / np.sqrt(prr * (1 - prr) / len(near)) if prr not in (0, 1) else float("nan")
            label = "全期" if fi is None else f"段{fi+1}"
            print(f"  {label:<18}{len(near):>7}{pn*100:>8.1f}%{prr*100:>8.1f}%{(pn-prr)*100:>+7.1f}"
                  f"{z:>7.2f}{np.mean([c['mae'] for c in near])*100:>+9.1f}%{thr:>9.1f}%")
        print("\n判生死：Δra 三段一致為正、貼支撐 MAE 明顯變淺、尾段 z>1.96 → 真控下檔，可進排序/當警示底；"
              "否則(僅某段強/翻負/MAE沒淺) → 認賠。")
    finally:
        session.close()


if __name__ == "__main__":
    main()
