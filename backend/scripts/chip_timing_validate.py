"""進場時機分數（合成）驗證 + 軟性次排序 λ 掃描（PIT，研究用，不寫DB）。

確立用 A 接法（獨立時機分數+軟性次排序）前的生死關：
  1. 合成「進場時機分數」(0-100)＝法人翻買近期性 + 外資投信共識 + 投信連買，等權。
     先確認合成分數本身在兩段樣本外仍維持增量（合成是新東西，三訊號相關、不保證疊加有效）。
  2. 軟性次排序：排序鍵 = 會噴分數 + λ·(時機-50)/50；逐 λ 掃「只用會噴 vs 會噴+時機」
     清單(每日取會噴前 top_pct%)的摸+10%率，兩段都要不變差，挑出最佳 λ。
會噴分數＝2·rank(atr)+rank(ma_align)（與線上同），池＝ma_align≥2 近似硬篩。

用法：python scripts/chip_timing_validate.py [n_dates] [sample] [top_pct]
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
_POP_TARGET = 0.10
_WARMUP = 160
_FLIP_HALF = 20      # 翻買近期性衰減窗（交易日）
_STREAK_CAP = 8      # 投信連買天數封頂


def _pct_rank(vals):
    s = pd.Series(vals, dtype=float)
    return (s.rank(pct=True) * 100).to_numpy()


def _timing_score(flip_recency, consensus, streak_norm):
    return 100.0 * (0.4 * flip_recency + 0.3 * consensus + 0.3 * streak_norm)


def main() -> None:
    n_dates = int(sys.argv[1]) if len(sys.argv) > 1 else 260
    sample = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    top_pct = float(sys.argv[3]) if len(sys.argv) > 3 else 15.0
    session = SessionLocal()
    try:
        axis = session.execute(
            select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)
        ).scalars().all()
        targets = sorted(axis[: len(axis) - _H][::sample][-n_dates:])
        print(f"進場日 {len(targets)}：{targets[0]} → {targets[-1]}；池=ma_align≥2，清單=會噴前 {top_pct:.0f}%\n")

        stocks = {s.id: s for s in session.execute(select(models.Stock)).scalars().all()}
        stock_ids = sorted(sid for sid in stocks if not sid.startswith("00"))
        date_lo = min(targets) - timedelta(days=_WARMUP)
        rows_by_date = {t: [] for t in targets}

        for sid, pdf, ind_g, inst_g, margin_g in _iter_stock_groups(session, stock_ids, date_lo):
            if ind_g is None or pdf is None:
                continue
            if inst_g is None:
                inst_g = pd.DataFrame(columns=_INST_COLS)
            pos = {d: i for i, d in enumerate(pdf["date"])}
            highs = pdf["high"].to_numpy(dtype=float)
            closes = pdf["close"].to_numpy(dtype=float)
            ind_by_date = {d: i for i, d in enumerate(ind_g["date"])}
            inst_sorted = inst_g.sort_values("date") if not inst_g.empty else inst_g
            # 預算整段法人 20 日累計 (raw ft) 供翻買近期性
            if not inst_sorted.empty:
                idates = inst_sorted["date"].to_numpy()
                ift = (inst_sorted["foreign_net"].fillna(0).to_numpy(dtype=float)
                       + inst_sorted["trust_net"].fillna(0).to_numpy(dtype=float))
                cum20 = np.convolve(ift, np.ones(20), "full")[:len(ift)]  # 近20日含當日累計
                for i in range(len(ift)):
                    lo = max(0, i - 19)
                    cum20[i] = ift[lo:i + 1].sum()
                flips = np.zeros(len(ift), dtype=bool)
                flips[1:] = (cum20[1:] > 0) & (cum20[:-1] <= 0)
                f_arr = inst_sorted["foreign_net"].fillna(0).to_numpy(dtype=float)
                t_arr = inst_sorted["trust_net"].fillna(0).to_numpy(dtype=float)
                date_to_i = {d: i for i, d in enumerate(idates)}
            else:
                date_to_i = {}

            for T in targets:
                p = pos.get(T)
                if p is None or p < _MIN_BARS - 1 or p + _H >= len(highs):
                    continue
                ii = ind_by_date.get(T)
                if ii is None:
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
                ma_align = int(ma[0] > ma[1]) + int(ma[1] > ma[2]) + int(ma[2] > ma[3])
                fhi = highs[p + 1: p + 1 + _H]
                fhi = fhi[~np.isnan(fhi)]
                if len(fhi) == 0:
                    continue
                hit = 1 if (float(fhi.max()) / c0 - 1.0) >= _POP_TARGET else 0

                # 時機三訊號（PIT：用 ≤T 的 inst index）
                recency = consensus = streak_norm = 0.0
                j = date_to_i.get(T)
                if j is None:  # 找 ≤T 最後一筆
                    le = [i for d, i in date_to_i.items() if d <= T]
                    j = max(le) if le else None
                if j is not None and j >= 20:
                    if cum20[j] > 0:
                        ks = np.where(flips[max(0, j - _FLIP_HALF):j + 1])[0]
                        if len(ks):
                            days_since = j - (max(0, j - _FLIP_HALF) + ks[-1])
                            recency = max(0.0, 1.0 - days_since / _FLIP_HALF)
                    f20, t20 = f_arr[j - 19:j + 1].sum(), t_arr[j - 19:j + 1].sum()
                    consensus = 1.0 if (f20 > 0 and t20 > 0) else (0.5 if (f20 > 0 or t20 > 0) else 0.0)
                    s = 0
                    for v in t_arr[:j + 1][::-1]:
                        if v > 0:
                            s += 1
                        else:
                            break
                    streak_norm = min(s, _STREAK_CAP) / _STREAK_CAP
                timing = _timing_score(recency, consensus, streak_norm)
                rows_by_date[T].append({
                    "atr_pct": atr_pct, "ma_align": ma_align,
                    "timing": timing, "recency": recency, "hit": hit,
                })

        fold = [targets[:len(targets) // 2], targets[len(targets) // 2:]]
        fold_name = ["段1(早)", "段2(晚/OOS)"]

        # 1) 合成時機分數：控制波動後增量，逐段
        print("=" * 78)
        print("【合成進場時機分數】控制波動後 高半-低半 命中差，逐段（確認合成仍站得住）")
        print("=" * 78)
        for fi, fd in enumerate(fold):
            diffs = []
            for t in fd:
                rs = [r for r in rows_by_date[t] if r["ma_align"] >= 2]
                if len(rs) < 50:
                    continue
                rs.sort(key=lambda r: r["atr_pct"])
                k = len(rs) // 5
                for qi in range(5):
                    grp = rs[qi * k:(qi + 1) * k] if qi < 4 else rs[qi * k:]
                    if len(grp) < 10:
                        continue
                    g2 = sorted(grp, key=lambda r: r["timing"])
                    h = len(g2) // 2
                    diffs.append(np.mean([r["hit"] for r in g2[-h:]]) - np.mean([r["hit"] for r in g2[:h]]))
            arr = np.array(diffs)
            t = arr.mean() / (arr.std(ddof=1) / np.sqrt(len(arr))) if len(arr) > 1 and arr.std(ddof=1) else float("nan")
            print(f"  {fold_name[fi]:<12} 增量 {arr.mean()*100:+.1f}pp (t={t:.1f})")

        # 2) 軟性次排序 λ 掃描：清單摸+10%率（會噴 vs 會噴+時機），逐段
        print("\n" + "=" * 78)
        print("【軟性次排序 λ 掃描】每日取會噴前 top% 清單的摸+10%率（兩段都要不變差）")
        print("=" * 78)
        lambdas = [0.0, 2.0, 4.0, 6.0, 8.0, 12.0]
        print(f"{'λ':>6}" + "".join(f"{fn:>16}" for fn in fold_name) + f"{'全期':>12}")
        for lam in lambdas:
            cells = []
            allhits = []
            for fd in fold + [targets]:
                hits = []
                for t in fd:
                    rs = [r for r in rows_by_date[t] if r["ma_align"] >= 2]
                    if len(rs) < 20:
                        continue
                    ar = _pct_rank([r["atr_pct"] for r in rs])
                    lr = _pct_rank([r["ma_align"] for r in rs])
                    pop = (2 * ar + lr) / 3.0
                    key = pop + lam * (np.array([r["timing"] for r in rs]) - 50.0) / 50.0
                    n = max(1, int(len(rs) * top_pct / 100))
                    top = np.argsort(-key)[:n]
                    hits.append(np.mean([rs[i]["hit"] for i in top]))
                cells.append(np.mean(hits) * 100 if hits else float("nan"))
            print(f"{lam:>6.0f}" + "".join(f"{c:>15.1f}%" for c in cells[:2]) + f"{cells[2]:>11.1f}%")
        print("\nλ=0 即現狀(只用會噴)。挑兩段都 ≥ λ=0、且晚段(OOS)有提升的最小 λ。")
    finally:
        session.close()


if __name__ == "__main__":
    main()
