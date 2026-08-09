"""會噴反推：命中股(未來20日摸+10%)在進場當天的條件分布(PIT walk-forward,研究用)。

正向：P(命中 | 條件) — 現行引擎邏輯(先過硬篩、再會噴分數前 20%)。
反向：P(條件 | 命中) — 命中股在進場當天長什麼樣？現行硬篩漏掉多少命中股？

輸出：
  表1 命中股 vs 非命中股 進場當天特徵分布 (median)
  表2 各特徵分桶 → 命中率 + lift + 佔命中股比例 (recall)
  表3 現行硬篩 & 會噴清單 的 precision / recall
  表4 漏掉的命中股(沒過硬篩)長什麼樣 + 缺哪條硬篩

用法：python scripts/pop_reverse_conditions.py [n_dates] [sample]
"""
from __future__ import annotations

import sys
from datetime import timedelta

import numpy as np
import pandas as pd
from sqlalchemy import distinct, select

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])

from app.engines.calibration import _INST_COLS, _MIN_BARS, _iter_stock_groups  # noqa: E402
from app.storage import models  # noqa: E402
from app.storage.database import SessionLocal  # noqa: E402

_H = 20
_TGT = 0.10
_WARMUP = 200


def _pct_rank(v):
    return (pd.Series(v, dtype=float).rank(pct=True) * 100).to_numpy()


def _tier_stats(rows, key, tiers, tier_labels, total_hits):
    vals = np.array([r.get(key) if r.get(key) is not None else np.nan for r in rows], dtype=float)
    hits = np.array([r["hit"] for r in rows])
    out = []
    for lo, hi, lbl in zip([-np.inf] + tiers, tiers + [np.inf], tier_labels):
        m = (vals > lo) & (vals <= hi) & ~np.isnan(vals)
        n = int(m.sum())
        h = int(hits[m].sum())
        if n < 50:
            continue
        out.append((lbl, n, 100.0 * h / n, 100.0 * h / total_hits))
    return out


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    sample = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    s = SessionLocal()
    try:
        axis = s.execute(
            select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)
        ).scalars().all()
        targets = sorted(axis[: len(axis) - _H][::sample][-n:])
        date_lo = min(targets) - timedelta(days=_WARMUP)
        stocks = {st.id: st for st in s.execute(select(models.Stock)).scalars().all()}
        sids = sorted(sid for sid in stocks if not sid.startswith("00"))
        print(f"進場日 {len(targets)}：{targets[0]} → {targets[-1]}，target 摸+10%/{_H}日")
        print("全宇宙(不過硬篩,排除 ETF,量能≥50 萬股/日)，命中股反推條件分布\n")

        rows = []
        for sid, pdf, ind_g, inst_g, margin_g in _iter_stock_groups(s, sids, date_lo):
            if ind_g is None or pdf is None:
                continue
            if inst_g is None:
                inst_g = pd.DataFrame(columns=_INST_COLS)
            pos_map = {d: i for i, d in enumerate(pdf["date"])}
            hi = pdf["high"].to_numpy(float)
            lo = pdf["low"].to_numpy(float)
            cl = pdf["close"].to_numpy(float)
            vo = pdf["volume"].to_numpy(float)
            with np.errstate(divide="ignore", invalid="ignore"):
                rng = (hi - lo) / np.where(cl > 0, cl, np.nan)
            ibd = {d: i for i, d in enumerate(ind_g["date"])}
            m20a = ind_g["ma20"].to_numpy(float)
            b60a = ind_g["bias_60"].to_numpy(float)
            b20a = ind_g["bias_20"].to_numpy(float)
            kda = ind_g["kd_k"].to_numpy(float)
            vma = ind_g["vol_ma20"].to_numpy(float)
            atra = ind_g["atr14"].to_numpy(float)
            ma5a = ind_g["ma5"].to_numpy(float)
            ma10a = ind_g["ma10"].to_numpy(float)
            ma60a = ind_g["ma60"].to_numpy(float)

            inst = inst_g.sort_values("date") if not inst_g.empty else inst_g
            if not inst.empty:
                idate = inst["date"].to_numpy()
                ft = (inst["foreign_net"].fillna(0) + inst["trust_net"].fillna(0)).to_numpy(float)
            mg = margin_g.sort_values("date") if (margin_g is not None and not margin_g.empty) else None
            if mg is not None:
                mdate = mg["date"].to_numpy()
                mb = mg["margin_balance"].to_numpy(float)
                sb = mg["short_balance"].to_numpy(float)

            for T in targets:
                p = pos_map.get(T)
                if p is None or p < _MIN_BARS - 1 or p + _H >= len(hi) or p < 40:
                    continue
                ii = ibd.get(T)
                if ii is None or ii < 5:
                    continue
                c0 = cl[p]
                atr14 = atra[ii]
                m20, m5, m10, m60 = m20a[ii], ma5a[ii], ma10a[ii], ma60a[ii]
                if (not c0 or c0 <= 0 or pd.isna(c0) or pd.isna(atr14)
                        or any(pd.isna(x) for x in (m5, m10, m20, m60))):
                    continue
                vm = vma[ii]
                if pd.isna(vm) or vm < 500000:
                    continue
                atr_pct = float(atr14) / c0
                ma_align = int(m5 > m10) + int(m10 > m20) + int(m20 > m60)
                m20p, b60, b20, kd = m20a[ii - 5], b60a[ii], b20a[ii], kda[ii]
                above_rising = bool(c0 > m20 and m20 > m20p) if not (pd.isna(m20) or pd.isna(m20p)) else False
                near60 = bool(abs(b60) < 15) if not pd.isna(b60) else False
                passed_hard = above_rising and near60

                volr = vo[p] / vm if vm > 0 else 0
                hi20p = np.nanmax(hi[p - 19:p]) if p >= 19 else np.nan
                amp10 = np.nanmean(rng[p - 9:p + 1])
                amp40 = np.nanmean(rng[p - 39:p + 1])
                amp = amp10 / amp40 if amp40 and amp40 > 0 else 1
                drift = abs(cl[p] / cl[p - 20] - 1) if cl[p - 20] > 0 else 1
                hi20 = np.nanmax(hi[p - 19:p + 1])
                lo20 = np.nanmin(lo[p - 19:p + 1])
                pir = (c0 - lo20) / (hi20 - lo20) if hi20 > lo20 else 0.5

                netbuy = 0.0
                if not inst.empty:
                    j = int(np.searchsorted(idate, T, side="right"))
                    if j >= 20:
                        netbuy = float(ft[j - 20:j].sum())
                inst_ratio = netbuy / ((vm / 1000) * 20) if vm > 0 else 0

                srr = 0.0
                if mg is not None:
                    k = int(np.searchsorted(mdate, T, side="right")) - 1
                    if k >= 0 and not pd.isna(mb[k]) and mb[k] > 0 and not pd.isna(sb[k]):
                        srr = float(sb[k] / mb[k] * 100)

                fh = hi[p + 1:p + 1 + _H] / c0 - 1.0
                fh = fh[~np.isnan(fh)]
                if len(fh) == 0:
                    continue
                mfe = float(fh.max())
                hit = 1 if mfe >= _TGT else 0

                rows.append(dict(
                    T=T, sid=sid, atr_pct=atr_pct, ma_align=ma_align,
                    passed_hard=passed_hard, above_rising=above_rising, near60=near60,
                    volr=volr, amp=amp, drift=drift, pir=pir,
                    b20=(None if pd.isna(b20) else float(b20)),
                    kd=(None if pd.isna(kd) else float(kd)),
                    inst_ratio=inst_ratio, srr=srr, hit=hit, mfe=mfe,
                ))

        by_date: dict = {}
        for r in rows:
            by_date.setdefault(r["T"], []).append(r)
        for T, cands in by_date.items():
            pop = (2 * _pct_rank([c["atr_pct"] for c in cands])
                   + _pct_rank([c["ma_align"] for c in cands])) / 3.0
            for c, pv in zip(cands, pop):
                c["pop"] = float(pv)
                c["in_list"] = bool(c["passed_hard"] and pv >= 80.0)

        df = pd.DataFrame(rows)
        n_all = len(df)
        n_hit = int(df["hit"].sum())
        base_rate = n_hit / n_all * 100 if n_all else 0
        print(f"樣本: {n_all:,} 筆(股·日), 命中 {n_hit:,} 筆, 全宇宙基準命中率 {base_rate:.1f}%\n")

        # 表1 特徵分布
        print("=" * 78)
        print("【表1 命中股 vs 非命中股 進場當天特徵中位】")
        print("=" * 78)
        cols = [("atr_pct", "日均波幅%", 100),
                ("ma_align", "均線多排數 0~3", 1),
                ("volr", "量能 vs 20日均量", 1),
                ("pir", "20日區間位階 0~1", 1),
                ("b20", "乖離月線%", 1),
                ("kd", "KD_K", 1),
                ("amp", "近10日振幅/近40日", 1),
                ("drift", "20日絕對漂移%", 100),
                ("inst_ratio", "法人20日淨買/量%", 100),
                ("srr", "券資比%", 1)]
        hit_df = df[df["hit"] == 1]
        miss_df = df[df["hit"] == 0]
        print(f"{'特徵':<24}{'命中中位':>10}{'非命中中位':>12}{'差':>9}")
        print("-" * 78)
        for col, name, mult in cols:
            h = hit_df[col].median()
            m = miss_df[col].median()
            if pd.isna(h) or pd.isna(m):
                continue
            print(f"{name:<22}{h * mult:>10.2f}{m * mult:>12.2f}{(h - m) * mult:>+9.2f}")

        # 表2 條件分桶
        print("\n" + "=" * 78)
        print("【表2 條件分桶：命中率 + lift + 佔命中股比例(recall)】")
        print("=" * 78)

        def report(title, key, tiers, tier_labels):
            print(f"\n[{title}]")
            print(f"  {'桶':<20}{'n':>8}{'命中率':>9}{'lift':>7}{'佔命中股':>10}")
            for lbl, n_, hr, rc in _tier_stats(rows, key, tiers, tier_labels, n_hit):
                print(f"  {lbl:<20}{n_:>8,}{hr:>8.1f}%{hr / base_rate:>6.2f}x{rc:>9.1f}%")

        report("波動 atr_pct(日均波幅%)", "atr_pct",
               [0.02, 0.035, 0.05, 0.07],
               ["<2%(低)", "2~3.5%", "3.5~5%", "5~7%", ">7%(極高)"])
        report("均線多排 ma_align", "ma_align",
               [0.5, 1.5, 2.5],
               ["0(全空)", "1", "2", "3(全多)"])
        report("20日區間位階 pir", "pir",
               [0.3, 0.5, 0.7],
               ["<0.3(低)", "0.3~0.5", "0.5~0.7", ">0.7(高)"])
        report("量能倍數(vs 20日均量)", "volr",
               [0.7, 1.0, 1.5, 2.5],
               ["<0.7(縮)", "0.7~1", "1~1.5", "1.5~2.5", ">2.5(爆)"])
        report("KD_K", "kd",
               [30, 50, 70, 85],
               ["<30(超賣)", "30~50", "50~70", "70~85", ">85(超買)"])
        report("乖離月線%", "b20",
               [-3, 3, 8, 15],
               ["<-3(超跌)", "-3~3(貼)", "3~8", "8~15", ">15(過熱)"])
        report("法人20日淨買/量%", "inst_ratio",
               [-0.02, 0, 0.02, 0.05],
               ["<-2(賣)", "-2~0", "0~2", "2~5", ">5(強買)"])

        # 表3 現行硬篩 / 清單 recall
        print("\n" + "=" * 78)
        print("【表3 現行硬篩 & 會噴清單：precision(命中率) / recall(佔命中股)】")
        print("=" * 78)
        for name, mask in [
            ("僅站上上揚月線", df["above_rising"]),
            ("僅|季線乖離|<15%", df["near60"]),
            ("兩者皆過(現行硬篩)", df["passed_hard"]),
            ("會噴前20%(硬篩+分數)", df["in_list"]),
        ]:
            sub = df[mask]
            n_ = len(sub)
            h_ = int(sub["hit"].sum())
            hr_ = h_ / n_ * 100 if n_ else 0
            rc_ = h_ / n_hit * 100 if n_hit else 0
            print(f"  {name:<24}n={n_:>8,}  命中率{hr_:>5.1f}%  "
                  f"lift{hr_ / base_rate:>4.2f}x  recall{rc_:>5.1f}%")

        # 表4 漏掉的命中股
        missed = df[(df["hit"] == 1) & (~df["passed_hard"])]
        caught = df[(df["hit"] == 1) & (df["passed_hard"])]
        print("\n" + "=" * 78)
        print(f"【表4 硬篩漏掉的命中股 {len(missed):,} 筆(佔命中 {len(missed)/n_hit*100:.0f}%) "
              f"vs 硬篩抓到的 {len(caught):,} 筆】")
        print("=" * 78)
        print(f"{'特徵':<24}{'漏掉中位':>10}{'抓到中位':>12}")
        print("-" * 78)
        for col, name, mult in cols:
            h = missed[col].median()
            m = caught[col].median()
            if pd.isna(h) or pd.isna(m):
                continue
            print(f"{name:<22}{h * mult:>10.2f}{m * mult:>12.2f}")

        n_m = len(missed)
        if n_m:
            print("\n漏掉的命中股缺哪條硬篩：")
            for name, mask in [
                ("僅缺 above_rising(月線不揚/跌破)", missed[missed["near60"] & ~missed["above_rising"]]),
                ("僅缺 near60(離季線 ≥15%)", missed[missed["above_rising"] & ~missed["near60"]]),
                ("兩者都缺", missed[~missed["above_rising"] & ~missed["near60"]]),
            ]:
                print(f"  {name:<36}: {len(mask):>6,} 筆 ({len(mask)/n_m*100:.0f}%)")
    finally:
        s.close()


if __name__ == "__main__":
    main()
