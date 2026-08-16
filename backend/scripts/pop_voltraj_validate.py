"""槓桿B 候選③：波動軌跡(收斂 vs 擴張) 對『風險調整命中』(PIT walk-forward，研究用，不寫DB)。

承券資比/真支撐皆認賠(見[[project-poppability-walkforward-diagnostic]])。波動軌跡與前者不同
——不是「買低/貼支撐」那一面，而是問進場當下波動在**收斂(彈簧)**還是**已爆開(追晚)**。與會噴
主因子 atr_pct(波動**水位**)正交，故全程在「控制波動水位」下測，把軌跡與水位分開。

不預設方向(兩個對立假設都可能)：
  彈簧說 → 收斂後突破=乾淨噴、MAE 淺；動能說 → 已擴張=續噴。讓資料講。
特徵 amp_ratio = 近10日振幅 / 近40日振幅(沿用 wave.ConsolidationScore._ratio 定義)；<1=收斂。
表中 Δra/Δpure = 收斂半(低ratio) − 擴張半(高ratio)：正=收斂有利、負=擴張有利。

  表A 控制波動水位：收斂半−擴張半 的 Δra vs Δpure，逐段。
  表B 清單內：收斂半 vs 擴張半，比 ra_hit / 純命中 / avg MAE。
  表C 事件：清單內最收斂(ratio bottom 20%) vs 其餘，含 z。

用法：python scripts/pop_voltraj_validate.py [n_dates] [sample] [folds] [top_pct] [stop_pct] [tail%]
"""

from __future__ import annotations

import sys
from datetime import timedelta

import numpy as np
import pandas as pd
from sqlalchemy import distinct, select

sys.path.insert(0, __file__.replace("\\", "/").rsplit("/scripts/", 1)[0])

from app.engines.calibration import _MIN_BARS, _iter_stock_groups  # noqa: E402
from app.storage import models  # noqa: E402
from app.storage.database import SessionLocal  # noqa: E402

_H = 20
_POP_TARGET = 0.10
_WARMUP = 200
_SHORT = 10
_LONG = 40


def _pct_rank(vals):
    return (pd.Series(vals, dtype=float).rank(pct=True) * 100).to_numpy()


def _half_diff(rows, key):
    """收斂半(低ratio)−擴張半(高ratio) 的 key 平均差。樣本不足回 None。"""
    rs = [r for r in rows if r.get("amp") is not None and r.get(key) is not None]
    if len(rs) < 20:
        return None
    rs.sort(key=lambda r: r["amp"])  # 升冪：前半=收斂(低)
    h = len(rs) // 2
    return np.mean([r[key] for r in rs[:h]]) - np.mean([r[key] for r in rs[-h:]])


def main() -> None:
    n_dates = int(sys.argv[1]) if len(sys.argv) > 1 else 260
    sample = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    folds = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    top_pct = float(sys.argv[4]) if len(sys.argv) > 4 else 20.0
    stop_pct = float(sys.argv[5]) if len(sys.argv) > 5 else 8.0
    tail_pct = float(sys.argv[6]) if len(sys.argv) > 6 else 20.0
    cutoff = 100.0 - top_pct
    stop = -stop_pct / 100.0
    session = SessionLocal()
    try:
        axis = session.execute(
            select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)
        ).scalars().all()
        targets = sorted(axis[: len(axis) - _H][::sample][-n_dates:])
        print(f"進場日 {len(targets)}：{targets[0]} → {targets[-1]}，切 {folds} 段；"
              f"清單=前{top_pct:.0f}%，停損−{stop_pct:.0f}%，amp=近{_SHORT}/近{_LONG}振幅(<1收斂)、"
              f"Δ=收斂半−擴張半、事件=最收斂bottom{tail_pct:.0f}%\n")

        stocks = {s.id: s for s in session.execute(select(models.Stock)).scalars().all()}
        stock_ids = sorted(sid for sid in stocks if not sid.startswith("00"))
        date_lo = min(targets) - timedelta(days=_WARMUP)

        rows_by_date = {t: [] for t in targets}
        for sid, pdf, ind_g, inst_g, margin_g in _iter_stock_groups(session, stock_ids, date_lo):
            if ind_g is None or pdf is None:
                continue
            pos = {d: i for i, d in enumerate(pdf["date"])}
            highs = pdf["high"].to_numpy(dtype=float)
            lows = pdf["low"].to_numpy(dtype=float)
            closes = pdf["close"].to_numpy(dtype=float)
            with np.errstate(divide="ignore", invalid="ignore"):
                rng = (highs - lows) / np.where(closes > 0, closes, np.nan)  # 逐日振幅(高低/收)
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
                ma20_now, ma20_prev = ma20_arr[ii], ma20_arr[ii - 5]
                bias60 = bias60_arr[ii]
                above_rising = bool(cT > ma20_now and ma20_now > ma20_prev) if not (
                    pd.isna(ma20_now) or pd.isna(ma20_prev)) else False
                near60 = bool(abs(bias60) < 15) if not pd.isna(bias60) else False
                passed_filter = above_rising and near60

                # 波動軌跡（PIT：≤T 的近10/近40日振幅比）
                amp = None
                if p >= _LONG - 1:
                    short = np.nanmean(rng[p - _SHORT + 1: p + 1])
                    long = np.nanmean(rng[p - _LONG + 1: p + 1])
                    if long and long == long and short == short and long > 0:
                        amp = float(short / long)

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

                rows_by_date[T].append({
                    "atr_pct": atr_pct, "ma_align": ma_align, "passed_filter": passed_filter,
                    "amp": amp, "hit": hit, "ra_hit": ra_hit, "mfe": mfe, "mae": mae,
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

        amp_all = [c["amp"] for d in targets for c in rows_by_date[d] if c.get("amp") is not None]
        print(f"amp 覆蓋 {len(amp_all):,}；振幅比 中位 {np.median(amp_all):.2f} / "
              f"p10 {np.percentile(amp_all,10):.2f} / p90 {np.percentile(amp_all,90):.2f}（<1=收斂）")

        fold_dates = [targets[i * len(targets) // folds:(i + 1) * len(targets) // folds]
                      for i in range(folds)]

        def agg(dates, pred):
            return [c for d in dates for c in rows_by_date[d] if pred(c)]

        # ── 表A：控制波動水位，收斂半−擴張半，Δra vs Δpure ──
        print("\n" + "=" * 92)
        print("【表A 控制波動水位：收斂半−擴張半 命中差(pp)】Δra=風險調整、Δpure=純摸到；正=收斂有利")
        print("=" * 92)
        print(f"{'':<10}" + "".join(f"{'段'+str(i+1)+' Δra/Δpure':>22}" for i in range(folds)))
        cells = []
        for fi in range(folds):
            dra, dpu = [], []
            for d in fold_dates[fi]:
                rs = [r for r in rows_by_date[d] if r["passed_filter"] and r["ma_align"] >= 2 and r.get("amp") is not None]
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
        print(f"{'波動軌跡':<7}" + "".join(f"{c:>22}" for c in cells))

        # ── 表B：清單內 收斂半 vs 擴張半 ──
        print("\n" + "=" * 92)
        print("【表B 清單(前20%會噴)內：收斂半 vs 擴張半】關鍵看 Δra≠0 哪邊贏 + MAE 哪邊淺")
        print("=" * 92)
        print(f"  {'段':<18}{'n':>7}{'收斂ra':>9}{'擴張ra':>9}{'Δra':>8}{'收斂純':>9}{'收斂MAE':>10}{'擴張MAE':>10}")
        for fi in list(range(folds)) + [None]:
            ds = targets if fi is None else fold_dates[fi]
            rs = agg(ds, lambda c: c.get("in_list") and c.get("amp") is not None)
            if len(rs) < 20:
                continue
            rs.sort(key=lambda c: c["amp"])
            h = len(rs) // 2
            con, exp = rs[:h], rs[-h:]
            label = "全期" if fi is None else f"段{fi+1}"
            print(f"  {label:<18}{len(rs):>7}{np.mean([c['ra_hit'] for c in con])*100:>8.1f}%"
                  f"{np.mean([c['ra_hit'] for c in exp])*100:>8.1f}%"
                  f"{(np.mean([c['ra_hit'] for c in con])-np.mean([c['ra_hit'] for c in exp]))*100:>+7.1f}"
                  f"{np.mean([c['hit'] for c in con])*100:>8.1f}%"
                  f"{np.mean([c['mae'] for c in con])*100:>+9.1f}%{np.mean([c['mae'] for c in exp])*100:>+9.1f}%")

        # ── 表C：事件：清單內最收斂(bottom tail%) vs 其餘，含 z ──
        print("\n" + "=" * 92)
        print(f"【表C 事件：清單內最收斂 bottom{tail_pct:.0f}% vs 其餘】")
        print("=" * 92)
        print(f"  {'段':<18}{'收斂n':>7}{'收斂ra':>9}{'其餘ra':>9}{'Δra':>8}{'z':>7}{'收斂MAE':>10}{'門檻ratio':>10}")
        for fi in list(range(folds)) + [None]:
            ds = targets if fi is None else fold_dates[fi]
            rs = agg(ds, lambda c: c.get("in_list") and c.get("amp") is not None)
            if len(rs) < 30:
                continue
            thr = np.percentile([c["amp"] for c in rs], tail_pct)
            con = [c for c in rs if c["amp"] <= thr]
            rest = [c for c in rs if c["amp"] > thr]
            if len(con) < 10 or len(rest) < 10:
                continue
            pc, prr = np.mean([c["ra_hit"] for c in con]), np.mean([c["ra_hit"] for c in rest])
            z = (pc - prr) / np.sqrt(prr * (1 - prr) / len(con)) if prr not in (0, 1) else float("nan")
            label = "全期" if fi is None else f"段{fi+1}"
            print(f"  {label:<18}{len(con):>7}{pc*100:>8.1f}%{prr*100:>8.1f}%{(pc-prr)*100:>+7.1f}"
                  f"{z:>7.2f}{np.mean([c['mae'] for c in con])*100:>+9.1f}%{thr:>9.2f}")
        print("\n判生死：Δra 三段一致同號、夠大、且(若控下檔說)MAE 變淺、尾段 z>1.96 → 真訊號；否則認賠。")
    finally:
        session.close()


if __name__ == "__main__":
    main()
