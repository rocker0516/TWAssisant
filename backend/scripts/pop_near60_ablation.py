"""硬篩消融：near60(|季線乖離|<15%) 是不是負貢獻？(PIT walk-forward，研究用，不寫DB)

承 pop_reverse_conditions.py：現行硬篩兩關皆過 precision 33.8% < 僅站上上揚月線 41.3%
< 乖離 8~15% 桶 50.8%——near60 疑似把命中率最高的熱股砍掉。本腳本對「硬篩定義」做消融，
其餘（會噴分數=2·rank(atr)+rank(ma_align)、前 top% 進清單）不動：

  A 現行        = above_rising + |bias60|<15
  B 拿掉near60  = above_rising
  C 放寬25      = above_rising + |bias60|<25
  D 只擋左尾    = above_rising + bias60>-15（過熱不擋、只擋跌深反彈）
  E 現行+前10%  = A 但 top_pct 減半（嚴選能否換 precision）
  F B+前10%     = B 但 top_pct 減半

口徑對齊線上回看：進場錨=隔天最高價、+10% 只看隔天之後 H 根；同列風險調整命中
(先摸+10% vs 先被 −stop% 打到，同日算先停損) 與 avg MAE，看拿掉 near60 的代價。三段 OOS。
用法：python scripts/pop_near60_ablation.py [n_dates] [sample] [folds] [top_pct] [stop_pct]
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


def main() -> None:
    n_dates = int(sys.argv[1]) if len(sys.argv) > 1 else 240
    sample = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    folds = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    top_pct = float(sys.argv[4]) if len(sys.argv) > 4 else 20.0
    stop_pct = float(sys.argv[5]) if len(sys.argv) > 5 else 8.0
    stop = -stop_pct / 100.0
    session = SessionLocal()
    try:
        axis = session.execute(
            select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)
        ).scalars().all()
        eligible = axis[: len(axis) - _H - 2]  # 隔天進場 + H 根未來
        targets = sorted(eligible[::sample][-n_dates:])
        print(f"進場日 {len(targets)}：{targets[0]}→{targets[-1]}，"
              f"錨=隔天最高，target 摸+10%/{_H}日，停損−{stop_pct:.0f}%，{folds} 段 OOS\n")

        stocks = {s.id: s for s in session.execute(select(models.Stock)).scalars().all()}
        stock_ids = sorted(sid for sid in stocks if not sid.startswith("00"))  # 排除 ETF
        date_lo = min(targets) - timedelta(days=_WARMUP)

        rows_by_date = {t: [] for t in targets}
        for sid, pdf, ind_g, _inst_g, _margin_g in _iter_stock_groups(session, stock_ids, date_lo):
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
                if p is None or p < _MIN_BARS - 1 or p + 2 + _H > len(highs):
                    continue
                ii = ind_by_date.get(T)
                if ii is None or ii < 5:
                    continue
                ind = ind_g.iloc[ii]
                atr14 = ind.get("atr14")
                ma = [ind.get(c) for c in ("ma5", "ma10", "ma20", "ma60")]
                cT = closes[p]
                if (not cT or cT <= 0 or atr14 is None or pd.isna(atr14)
                        or any(m is None or pd.isna(m) for m in ma)):
                    continue
                atr_pct = float(atr14) / cT
                ma_align = int(ma[0] > ma[1]) + int(ma[1] > ma[2]) + int(ma[2] > ma[3])

                ma20_now, ma20_prev = ma20_arr[ii], ma20_arr[ii - 5]
                bias60 = bias60_arr[ii]
                if pd.isna(ma20_now) or pd.isna(ma20_prev) or pd.isna(bias60):
                    continue
                above_rising = bool(cT > ma20_now and ma20_now > ma20_prev)

                c0 = highs[p + 1]  # 隔天最高（與線上回看同錨）
                if not c0 or np.isnan(c0) or c0 <= 0:
                    continue
                fhi = highs[p + 2: p + 2 + _H]
                flo = lows[p + 2: p + 2 + _H]
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
                ra_hit = 0 if tgt_day is None else (1 if stp_day is None or tgt_day < stp_day else 0)

                rows_by_date[T].append({
                    "atr_pct": atr_pct, "ma_align": ma_align,
                    "above_rising": above_rising, "bias60": float(bias60),
                    "hit": hit, "ra_hit": ra_hit, "mfe": mfe, "mae": mae,
                })

        variants = {
            "A 現行(rising+|乖|<15)": (lambda c: c["above_rising"] and abs(c["bias60"]) < 15, top_pct),
            "B 拿掉near60(僅rising)": (lambda c: c["above_rising"], top_pct),
            "C 放寬(|乖|<25)": (lambda c: c["above_rising"] and abs(c["bias60"]) < 25, top_pct),
            "D 只擋左尾(乖>-15)": (lambda c: c["above_rising"] and c["bias60"] > -15, top_pct),
            "E A+前10%": (lambda c: c["above_rising"] and abs(c["bias60"]) < 15, top_pct / 2),
            "F B+前10%": (lambda c: c["above_rising"], top_pct / 2),
        }

        # 逐日：對每個 variant 在其硬篩宇宙內 rank → 前 top% 清單
        lists = {name: {t: [] for t in targets} for name in variants}
        for T in targets:
            cands = rows_by_date[T]
            if not cands:
                continue
            for name, (pred, tp) in variants.items():
                uni = [c for c in cands if pred(c)]
                if len(uni) < 10:
                    continue
                pop = (2 * _pct_rank([c["atr_pct"] for c in uni])
                       + _pct_rank([c["ma_align"] for c in uni])) / 3.0
                cut = 100.0 - tp
                lists[name][T] = [c for c, pv in zip(uni, pop) if pv >= cut]

        fold_dates = [targets[i * len(targets) // folds:(i + 1) * len(targets) // folds]
                      for i in range(folds)]

        print(f"{'variant':<26}{'總n':>7}{'摸+10%':>8}{'風調':>7}{'avgMAE':>8}   逐段 摸+10%/風調")
        print("-" * 96)
        for name in variants:
            lst_all = [c for t in targets for c in lists[name][t]]
            if not lst_all:
                continue
            ph = np.mean([c["hit"] for c in lst_all])
            pr = np.mean([c["ra_hit"] for c in lst_all])
            mae = np.mean([c["mae"] for c in lst_all])
            segs = []
            for fd in fold_dates:
                seg = [c for t in fd for c in lists[name][t]]
                segs.append(f"{np.mean([c['hit'] for c in seg])*100:.0f}%/"
                            f"{np.mean([c['ra_hit'] for c in seg])*100:.0f}%" if seg else "—")
            print(f"{name:<26}{len(lst_all):>7}{ph*100:>7.1f}%{pr*100:>6.1f}%"
                  f"{mae*100:>+7.1f}%   {'  '.join(segs)}")
        print("\n判讀：B/C/D 若摸+10% 明顯 > A 且三段穩，near60 判死；同時看風調/MAE 付了多少代價。"
              "\nE/F 看嚴選(前10%)能否再換 precision（代價=檔數減半）。")
    finally:
        session.close()


if __name__ == "__main__":
    main()
