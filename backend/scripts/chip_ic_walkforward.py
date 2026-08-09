"""籌碼用法 walk-forward 樣本外驗證（PIT，研究用，不寫DB）。

round2 在 2023-26 同段(in-sample)發現換用法後增量翻倍、法人「翻買擇時」+6pp 最強。
但一口氣試了 7 種重構→最好的恐過度配適/多重檢定運氣。本腳本把時間軸按時序切 N 段，
逐段獨立量每個用法的邊際：若後段(out-of-sample)仍維持、且贏家排名穩定 → 真訊號；
若 OOS 坍縮或排名重洗 → 樣本內運氣。窗拉長到 ~5 年讓每段有足夠樣本。

決勝指標：
  - 「翻買擇時」事件命中率 vs 同段全池基準（含 z 檢定）
  - 各重構「控制波動後 高半-低半 命中差」
用法：python scripts/chip_ic_walkforward.py [n_dates] [sample] [folds]
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


def _ctrl_vol_diff(rows, fc):
    """控制波動：按 atr 切5組，組內 fc 高半-低半 hit 差，回傳各組差的 list。"""
    out = []
    rs = [r for r in rows if r["ma_align"] >= 2 and r.get(fc) is not None]
    if len(rs) < 50:
        return out
    rs.sort(key=lambda r: r["atr_pct"])
    k = len(rs) // 5
    for qi in range(5):
        grp = rs[qi * k:(qi + 1) * k] if qi < 4 else rs[qi * k:]
        if len(grp) < 10:
            continue
        g2 = sorted(grp, key=lambda r: r[fc])
        h = len(g2) // 2
        out.append(np.mean([r["hit"] for r in g2[-h:]]) - np.mean([r["hit"] for r in g2[:h]]))
    return out


def main() -> None:
    n_dates = int(sys.argv[1]) if len(sys.argv) > 1 else 260
    sample = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    folds = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    session = SessionLocal()
    try:
        axis = session.execute(
            select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)
        ).scalars().all()
        eligible = axis[: len(axis) - _H]
        targets = sorted(eligible[::sample][-n_dates:])
        print(f"進場日 {len(targets)} 個：{targets[0]} → {targets[-1]}，切 {folds} 段\n")

        stocks = {s.id: s for s in session.execute(select(models.Stock)).scalars().all()}
        stock_ids = sorted(sid for sid in stocks if not sid.startswith("00"))
        date_lo = min(targets) - timedelta(days=_WARMUP)

        rows_by_date = {t: [] for t in targets}
        # 時序擇時：per (date) 累積 (hit, is_flip)；事後按 fold 切
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
                vol_ma20 = ind.get("vol_ma20")
                c0 = highs[p]
                if (not c0 or c0 <= 0 or atr14 is None or pd.isna(atr14)
                        or any(m is None or pd.isna(m) for m in ma)):
                    continue
                cT = closes[p]
                atr_pct = float(atr14) / cT if cT else None
                ma_align = int(ma[0] > ma[1]) + int(ma[1] > ma[2]) + int(ma[2] > ma[3])
                vma = (float(vol_ma20) / 1000.0) if vol_ma20 and not pd.isna(vol_ma20) else 0.0
                fhi = highs[p + 1: p + 1 + _H]
                fhi = fhi[~np.isnan(fhi)]
                if len(fhi) == 0:
                    continue
                hit = 1 if (float(fhi.max()) / c0 - 1.0) >= _POP_TARGET else 0

                level = trust_norm = consensus = streak = persist = flip = None
                if not inst_sorted.empty:
                    upto = inst_sorted[inst_sorted["date"] <= T]
                    if len(upto) >= 21:
                        f = upto["foreign_net"].fillna(0).to_numpy(dtype=float)
                        tr = upto["trust_net"].fillna(0).to_numpy(dtype=float)
                        ft = f + tr
                        if vma > 0:
                            level = float(ft[-20:].sum()) / (vma * 20)
                            trust_norm = float(tr[-20:].sum()) / (vma * 20)
                        f20, t20 = f[-20:].sum(), tr[-20:].sum()
                        consensus = 1.0 if (f20 > 0 and t20 > 0) else (-1.0 if (f20 < 0 and t20 < 0) else 0.0)
                        persist = float((ft[-20:] > 0).mean())
                        s = 0
                        for v in tr[::-1]:
                            if v > 0:
                                s += 1
                            else:
                                break
                        streak = float(s)
                        cum_now, cum_prev = ft[-20:].sum(), ft[-21:-1].sum()
                        flip = (cum_prev <= 0 and cum_now > 0)
                rows_by_date[T].append({
                    "atr_pct": atr_pct, "ma_align": ma_align, "level": level,
                    "trust_norm": trust_norm, "consensus": consensus, "streak": streak,
                    "persist": persist, "flip": flip, "hit": hit,
                })

        # 切 fold
        fold_dates = [targets[i * len(targets) // folds:(i + 1) * len(targets) // folds] for i in range(folds)]
        labels = {
            "level": "法人買超量級(ChipScore現用)", "persist": "法人買超天數比例",
            "trust_norm": "投信獨立佔量", "consensus": "外資投信共識同向",
            "streak": "投信連續買超天數",
        }

        # ── A. 各重構 控制波動增量，逐 fold ──
        print("=" * 92)
        print("【控制波動後 高半-低半 命中差(pp)】逐段；看後段(OOS)是否維持、贏家排名是否穩定")
        print("=" * 92)
        hdr = f"{'用法':<26}" + "".join(f"{('段'+str(i+1)):>16}" for i in range(folds))
        print(hdr)
        per = [{d: rows_by_date[d] for d in fd} for fd in fold_dates]
        for fc in labels:
            cells = []
            for fi in range(folds):
                diffs = []
                for d in fold_dates[fi]:
                    diffs += _ctrl_vol_diff(rows_by_date[d], fc)
                if len(diffs) >= 2:
                    arr = np.array(diffs)
                    t = arr.mean() / (arr.std(ddof=1) / np.sqrt(len(arr))) if arr.std(ddof=1) else float("nan")
                    cells.append(f"{arr.mean()*100:+.1f}pp(t{t:.1f})")
                else:
                    cells.append("—")
            print(f"{labels[fc]:<24}" + "".join(f"{c:>16}" for c in cells))

        # ── B. 翻買擇時 event study，逐 fold（含 z 檢定）──
        print("\n" + "=" * 92)
        print("【法人翻買擇時】事件命中率 vs 同段全池基準（多頭池）")
        print("=" * 92)
        print(f"{'段(期間)':<30}{'全池基準':>10}{'翻買事件':>10}{'增量':>9}{'事件n':>8}{'z值':>7}")
        print("-" * 92)
        for fi in range(folds):
            base, ev = [], []
            for d in fold_dates[fi]:
                for r in rows_by_date[d]:
                    if r["ma_align"] >= 2 and r["flip"] is not None:
                        base.append(r["hit"])
                        if r["flip"]:
                            ev.append(r["hit"])
            if not base or not ev:
                continue
            pb, pe, ne = np.mean(base), np.mean(ev), len(ev)
            z = (pe - pb) / np.sqrt(pb * (1 - pb) / ne) if pb not in (0, 1) else float("nan")
            span = f"{fold_dates[fi][0]}~{fold_dates[fi][-1]}"
            print(f"{('段'+str(fi+1)+' '+span):<30}{pb*100:>9.1f}%{pe*100:>9.1f}%"
                  f"{(pe-pb)*100:>+8.1f}pp{ne:>8}{z:>7.2f}")
        print("\nz>1.96 = 該段 OOS 仍統計顯著。贏家在後段維持且排名穩 → 真訊號；坍縮/重洗 → 樣本內運氣。")
    finally:
        session.close()


if __name__ == "__main__":
    main()
