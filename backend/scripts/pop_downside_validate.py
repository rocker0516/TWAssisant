"""槓桿B 驗證：下檔因子能否提高『風險調整命中』(PIT walk-forward，研究用，不寫DB)。

承 pop_diagnostic_walkforward.py 的發現：會噴清單純摸+10%率37%、但風險調整命中(先摸+10%
再被−8%打到)僅27%、avg MAE−12%比MFE+11%深。本腳本驗『對的因子、錯的考題』假設——
把之前在「純MFE命中率」上看似沒用的下檔因子(區間位階pir、乖離bias_20、KD)，改在
**風險調整命中**這個新 target 上測，看它們是否控得住下檔。低值=有利(買低/貼月線/未過熱)。

  表A 控制波動邊際力：各因子『有利半 − 不利半』的 ra_hit 差 vs pure_hit 差，逐段。
      關鍵＝有利方向幫『風險調整命中』要明顯多於幫『純命中』(Δra > Δpure)→ 真控下檔。
  表B 可行動：在實際清單(前20%會噴)內，依因子(及綜合下檔分)切有利/不利半，比 ra_hit、
      純命中、avg MAE——看「清單再過下檔濾網」能否拉高真實可交易性、壓淺回撤。

用法：python scripts/pop_downside_validate.py [n_dates] [sample] [folds] [top_pct] [stop_pct]
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

# 下檔因子（低值=有利：買低、貼月線、未過熱）。combined 綜合下檔分用「有利百分位」等權。
_FEATS = {"pir": "區間位階(低=低接)", "bias20": "乖離月線(低=不延伸)", "kd_k": "KD(低=未過熱)"}


def _pct_rank(vals):
    return (pd.Series(vals, dtype=float).rank(pct=True) * 100).to_numpy()


def _split_diff(rows, fc, key):
    """按 fc 升冪切半，回 (有利半-不利半) 的 key 平均差；有利=低值半。樣本不足回 None。"""
    rs = [r for r in rows if r.get(fc) is not None and r.get(key) is not None]
    if len(rs) < 20:
        return None
    rs.sort(key=lambda r: r[fc])
    h = len(rs) // 2
    fav = np.mean([r[key] for r in rs[:h]])      # 低值半=有利
    unf = np.mean([r[key] for r in rs[-h:]])     # 高值半=不利
    return fav - unf


def main() -> None:
    n_dates = int(sys.argv[1]) if len(sys.argv) > 1 else 260
    sample = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    folds = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    top_pct = float(sys.argv[4]) if len(sys.argv) > 4 else 20.0
    stop_pct = float(sys.argv[5]) if len(sys.argv) > 5 else 8.0
    cutoff = 100.0 - top_pct
    stop = -stop_pct / 100.0
    session = SessionLocal()
    try:
        axis = session.execute(
            select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)
        ).scalars().all()
        targets = sorted(axis[: len(axis) - _H][::sample][-n_dates:])
        print(f"進場日 {len(targets)}：{targets[0]} → {targets[-1]}，切 {folds} 段；"
              f"清單=前{top_pct:.0f}%，停損−{stop_pct:.0f}%，下檔因子低值=有利\n")

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
            ind_by_date = {d: i for i, d in enumerate(ind_g["date"])}
            ma20_arr = ind_g["ma20"].to_numpy(dtype=float)
            bias60_arr = ind_g["bias_60"].to_numpy(dtype=float)
            bias20_arr = ind_g["bias_20"].to_numpy(dtype=float)
            kd_arr = ind_g["kd_k"].to_numpy(dtype=float)

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

                # 下檔因子（PIT，用 ≤T 的資料）
                hi20 = float(np.nanmax(highs[max(0, p - 19):p + 1]))
                lo20 = float(np.nanmin(lows[max(0, p - 19):p + 1]))
                pir = (cT - lo20) / (hi20 - lo20) if hi20 > lo20 else 0.5
                bias20 = None if pd.isna(bias20_arr[ii]) else float(bias20_arr[ii])
                kd_k = None if pd.isna(kd_arr[ii]) else float(kd_arr[ii])

                # 結果
                fhi = highs[p + 1: p + 1 + _H]
                flo = lows[p + 1: p + 1 + _H]
                m = ~(np.isnan(fhi) | np.isnan(flo))
                fhi, flo = fhi[m], flo[m]
                if len(fhi) == 0:
                    continue
                up, dn = fhi / c0 - 1.0, flo / c0 - 1.0
                mfe, mae = float(up.max()), float(dn.min())
                hit = 1 if mfe >= _POP_TARGET else 0
                tgt_day = next((k for k in range(len(up)) if up[k] >= _POP_TARGET), None)
                stp_day = next((k for k in range(len(dn)) if dn[k] <= stop), None)
                if tgt_day is None:
                    ra_hit = 0
                elif stp_day is None:
                    ra_hit = 1
                else:
                    ra_hit = 1 if tgt_day < stp_day else 0

                rows_by_date[T].append({
                    "atr_pct": atr_pct, "ma_align": ma_align, "passed_filter": passed_filter,
                    "pir": pir, "bias20": bias20, "kd_k": kd_k,
                    "hit": hit, "ra_hit": ra_hit, "mfe": mfe, "mae": mae,
                })

        # 逐日：會噴 rank → in_list；綜合下檔分（有利百分位等權，低值→高分）
        for T in targets:
            cands = rows_by_date[T]
            if not cands:
                continue
            pop = (2 * _pct_rank([c["atr_pct"] for c in cands])
                   + _pct_rank([c["ma_align"] for c in cands])) / 3.0
            favs = {f: 100.0 - _pct_rank([c.get(f) if c.get(f) is not None else np.nan for c in cands])
                    for f in _FEATS}
            for i, c in enumerate(cands):
                c["pop"] = float(pop[i])
                c["in_list"] = bool(c["passed_filter"] and pop[i] >= cutoff)
                vals = [favs[f][i] for f in _FEATS if not np.isnan(favs[f][i])]
                c["downside"] = float(np.mean(vals)) if vals else None  # 高=下檔風險低(有利)

        fold_dates = [targets[i * len(targets) // folds:(i + 1) * len(targets) // folds]
                      for i in range(folds)]

        # ── 表A：控制波動，各因子有利半−不利半，ra_hit 差 vs pure 差，逐段 ──
        print("=" * 100)
        print("【表A 控制波動：因子『有利半−不利半』命中差(pp)】Δra=風險調整命中差、Δpure=純命中差")
        print("  關鍵：真控下檔的因子 Δra 應 > 0 且明顯 > Δpure（幫可交易性多於幫純摸到）")
        print("=" * 100)
        print(f"{'因子':<22}" + "".join(f"{'段'+str(i+1)+' Δra/Δpure':>20}" for i in range(folds)))
        print("-" * 100)
        for fc in _FEATS:
            cells = []
            for fi in range(folds):
                dra, dpu = [], []
                for d in fold_dates[fi]:
                    rs = [r for r in rows_by_date[d] if r["passed_filter"] and r["ma_align"] >= 2]
                    # 控波動：atr 五分位內切
                    rs = [r for r in rs if r.get(fc) is not None]
                    if len(rs) < 50:
                        continue
                    rs.sort(key=lambda r: r["atr_pct"])
                    k = len(rs) // 5
                    for qi in range(5):
                        grp = rs[qi * k:(qi + 1) * k] if qi < 4 else rs[qi * k:]
                        a = _split_diff(grp, fc, "ra_hit")
                        b = _split_diff(grp, fc, "hit")
                        if a is not None:
                            dra.append(a)
                        if b is not None:
                            dpu.append(b)
                if dra:
                    cells.append(f"{np.mean(dra)*100:+.1f}/{np.mean(dpu)*100:+.1f}")
                else:
                    cells.append("—")
            print(f"{_FEATS[fc]:<20}" + "".join(f"{c:>20}" for c in cells))

        # ── 表B：清單內依因子/綜合下檔分切有利半，比 ra_hit / 純命中 / avg MAE ──
        print("\n" + "=" * 100)
        print("【表B 可行動：清單(前20%會噴)內切『有利半 vs 不利半』】看下檔濾網能否拉高可交易性")
        print("=" * 100)
        for fc in list(_FEATS) + ["downside"]:
            name = "綜合下檔分(高=低風險)" if fc == "downside" else _FEATS[fc]
            print(f"\n▌{name}")
            print(f"  {'段':<20}{'清單n':>7}{'有利ra':>9}{'不利ra':>9}{'Δra':>8}"
                  f"{'有利MAE':>10}{'不利MAE':>10}")
            for fi in list(range(folds)) + [None]:
                ds = targets if fi is None else fold_dates[fi]
                rs = [c for d in ds for c in rows_by_date[d] if c.get("in_list") and c.get(fc) is not None]
                if len(rs) < 20:
                    continue
                # downside 高=有利(反向)；其餘低=有利
                rs.sort(key=lambda c: (-c[fc]) if fc == "downside" else c[fc])
                h = len(rs) // 2
                fav, unf = rs[:h], rs[h:]
                fra, ura = np.mean([c["ra_hit"] for c in fav]), np.mean([c["ra_hit"] for c in unf])
                fma, uma = np.mean([c["mae"] for c in fav]), np.mean([c["mae"] for c in unf])
                label = "全期" if fi is None else f"段{fi+1}"
                print(f"  {label:<20}{len(rs):>7}{fra*100:>8.1f}%{ura*100:>8.1f}%"
                      f"{(fra-ura)*100:>+7.1f}{fma*100:>+9.1f}%{uma*100:>+9.1f}%")
        print("\n清單基準(全清單)風險調整命中≈27%。有利半 Δra 穩定為正、MAE 變淺 → 下檔因子可進排序/當警示底。")
    finally:
        session.close()


if __name__ == "__main__":
    main()
