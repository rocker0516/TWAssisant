"""槓桿B 候選①：券資比/軋空 對『風險調整命中』(PIT walk-forward，研究用，不寫DB)。

承 pop_downside_validate.py：靜態位階/過熱因子在風險調整命中(先摸+10% 再被−8%打到)上
OOS 皆死。本腳本單獨驗使用者指定、且為[[project-chip-poppability-ic]]裡『埋著的最強訊號』
的券資比/軋空——理論先驗：高券資比＝空單擁擠，須回補→天生下檔地板＋上檔催化，可能控得住
那 −12% 的下檔。守反過度配適紀律：只驗這一條、三段 OOS、不穩就認賠。

券資比 = 融券餘額 / 融資餘額 × 100（PIT 取 ≤T 最新，與 context.short_margin_ratio 同義）。
高券資比＝有利(假設)。因券資比右偏(大量近零)，除中位切半外，另做『高尾(top%) vs 其餘』
事件切法應對非線性。

  表A 控制波動：高半−低半 的 Δra(風險調整命中差) vs Δpure(純命中差)，逐段。
  表B 清單內：高券資比半 vs 低半，比 ra_hit / 純命中 / avg MAE。
  表C 事件(非線性)：券資比 top% 尾段 vs 其餘，比 ra_hit / 純命中 / MAE，逐段 + 含 z。

用法：python scripts/pop_squeeze_validate.py [n_dates] [sample] [folds] [top_pct] [stop_pct] [srr_top%]
"""

from __future__ import annotations

import sys
from datetime import timedelta

import numpy as np
import pandas as pd
from sqlalchemy import distinct, select

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])

from app.engines.calibration import _MIN_BARS, _iter_stock_groups  # noqa: E402
from app.storage import models  # noqa: E402
from app.storage.database import SessionLocal  # noqa: E402

_H = 20
_POP_TARGET = 0.10
_WARMUP = 200


def _pct_rank(vals):
    return (pd.Series(vals, dtype=float).rank(pct=True) * 100).to_numpy()


def _half_diff(rows, key):
    """券資比高半−低半 的 key 平均差。樣本不足回 None。高=有利方向。"""
    rs = [r for r in rows if r.get("srr") is not None and r.get(key) is not None]
    if len(rs) < 20:
        return None
    rs.sort(key=lambda r: r["srr"])
    h = len(rs) // 2
    return np.mean([r[key] for r in rs[-h:]]) - np.mean([r[key] for r in rs[:h]])


def main() -> None:
    n_dates = int(sys.argv[1]) if len(sys.argv) > 1 else 260
    sample = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    folds = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    top_pct = float(sys.argv[4]) if len(sys.argv) > 4 else 20.0
    stop_pct = float(sys.argv[5]) if len(sys.argv) > 5 else 8.0
    srr_top = float(sys.argv[6]) if len(sys.argv) > 6 else 20.0
    cutoff = 100.0 - top_pct
    stop = -stop_pct / 100.0
    session = SessionLocal()
    try:
        axis = session.execute(
            select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)
        ).scalars().all()
        targets = sorted(axis[: len(axis) - _H][::sample][-n_dates:])
        print(f"進場日 {len(targets)}：{targets[0]} → {targets[-1]}，切 {folds} 段；"
              f"清單=前{top_pct:.0f}%，停損−{stop_pct:.0f}%，券資比高=有利、事件尾段=top{srr_top:.0f}%\n")

        stocks = {s.id: s for s in session.execute(select(models.Stock)).scalars().all()}
        stock_ids = sorted(sid for sid in stocks if not sid.startswith("00"))
        date_lo = min(targets) - timedelta(days=_WARMUP)

        rows_by_date = {t: [] for t in targets}
        n_with_srr = n_total = 0
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
            mg = margin_g.sort_values("date") if (margin_g is not None and not margin_g.empty) else None
            if mg is not None:
                mdates = mg["date"].to_numpy()
                mb_arr = mg["margin_balance"].to_numpy(dtype=float)
                sb_arr = mg["short_balance"].to_numpy(dtype=float)

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

                # 券資比（PIT：≤T 最新一筆 short/margin）
                srr = None
                if mg is not None:
                    k = np.searchsorted(mdates, T, side="right") - 1
                    if k >= 0 and not pd.isna(mb_arr[k]) and mb_arr[k] > 0 and not pd.isna(sb_arr[k]):
                        srr = float(sb_arr[k] / mb_arr[k] * 100.0)

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
                if srr is not None:
                    n_with_srr += 1
                rows_by_date[T].append({
                    "atr_pct": atr_pct, "ma_align": ma_align, "passed_filter": passed_filter,
                    "srr": srr, "hit": hit, "ra_hit": ra_hit, "mfe": mfe, "mae": mae,
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

        cov = n_with_srr / n_total * 100 if n_total else 0
        srr_all = [c["srr"] for d in targets for c in rows_by_date[d] if c.get("srr") is not None]
        print(f"券資比覆蓋率 {cov:.0f}%（{n_with_srr:,}/{n_total:,}）；"
              f"全樣本券資比 中位 {np.median(srr_all):.1f}% / p90 {np.percentile(srr_all,90):.1f}% / "
              f"p99 {np.percentile(srr_all,99):.1f}%")

        fold_dates = [targets[i * len(targets) // folds:(i + 1) * len(targets) // folds]
                      for i in range(folds)]

        def agg(dates, pred):
            return [c for d in dates for c in rows_by_date[d] if pred(c)]

        # ── 表A：控制波動，券資比高半−低半，Δra vs Δpure ──
        print("\n" + "=" * 92)
        print("【表A 控制波動：券資比 高半−低半 命中差(pp)】Δra=風險調整、Δpure=純摸到；高券資比=有利")
        print("=" * 92)
        print(f"{'':<10}" + "".join(f"{'段'+str(i+1)+' Δra/Δpure':>22}" for i in range(folds)))
        cells = []
        for fi in range(folds):
            dra, dpu = [], []
            for d in fold_dates[fi]:
                rs = [r for r in rows_by_date[d] if r["passed_filter"] and r["ma_align"] >= 2 and r.get("srr") is not None]
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
        print(f"{'券資比':<8}" + "".join(f"{c:>22}" for c in cells))

        # ── 表B：清單內 券資比高半 vs 低半 ──
        print("\n" + "=" * 92)
        print("【表B 清單(前20%會噴)內：券資比高半 vs 低半】")
        print("=" * 92)
        print(f"  {'段':<18}{'n':>7}{'高券ra':>9}{'低券ra':>9}{'Δra':>8}{'高券純':>9}{'高券MAE':>10}{'低券MAE':>10}")
        for fi in list(range(folds)) + [None]:
            ds = targets if fi is None else fold_dates[fi]
            rs = agg(ds, lambda c: c.get("in_list") and c.get("srr") is not None)
            if len(rs) < 20:
                continue
            rs.sort(key=lambda c: c["srr"])
            h = len(rs) // 2
            lo, hi = rs[:h], rs[-h:]
            label = "全期" if fi is None else f"段{fi+1}"
            print(f"  {label:<18}{len(rs):>7}{np.mean([c['ra_hit'] for c in hi])*100:>8.1f}%"
                  f"{np.mean([c['ra_hit'] for c in lo])*100:>8.1f}%"
                  f"{(np.mean([c['ra_hit'] for c in hi])-np.mean([c['ra_hit'] for c in lo]))*100:>+7.1f}"
                  f"{np.mean([c['hit'] for c in hi])*100:>8.1f}%"
                  f"{np.mean([c['mae'] for c in hi])*100:>+9.1f}%{np.mean([c['mae'] for c in lo])*100:>+9.1f}%")

        # ── 表C：事件(非線性)：券資比 top% 尾段 vs 其餘（清單內），含 z ──
        print("\n" + "=" * 92)
        print(f"【表C 事件：清單內券資比 top{srr_top:.0f}% 尾段 vs 其餘】應對非線性(軋空只在空單擁擠時有料)")
        print("=" * 92)
        print(f"  {'段':<18}{'尾段n':>7}{'尾段ra':>9}{'其餘ra':>9}{'Δra':>8}{'z':>7}{'尾段MAE':>10}{'門檻券資比':>11}")
        for fi in list(range(folds)) + [None]:
            ds = targets if fi is None else fold_dates[fi]
            rs = agg(ds, lambda c: c.get("in_list") and c.get("srr") is not None)
            if len(rs) < 30:
                continue
            thr = np.percentile([c["srr"] for c in rs], 100 - srr_top)
            tail = [c for c in rs if c["srr"] >= thr]
            rest = [c for c in rs if c["srr"] < thr]
            if len(tail) < 10 or len(rest) < 10:
                continue
            pt, pr_ = np.mean([c["ra_hit"] for c in tail]), np.mean([c["ra_hit"] for c in rest])
            z = (pt - pr_) / np.sqrt(pr_ * (1 - pr_) / len(tail)) if pr_ not in (0, 1) else float("nan")
            label = "全期" if fi is None else f"段{fi+1}"
            print(f"  {label:<18}{len(tail):>7}{pt*100:>8.1f}%{pr_*100:>8.1f}%{(pt-pr_)*100:>+7.1f}"
                  f"{z:>7.2f}{np.mean([c['mae'] for c in tail])*100:>+9.1f}%{thr:>10.1f}%")
        print("\n判生死：Δra 三段一致為正、且尾段 z>1.96 → 軋空真控下檔，可進排序/當警示底；"
              "若僅某段強(如round2假贏家)或 Δra 翻負 → 認賠。")
    finally:
        session.close()


if __name__ == "__main__":
    main()
