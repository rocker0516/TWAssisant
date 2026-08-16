"""大盤 regime 閘門 × 會噴清單命中率（PIT walk-forward，研究用，不寫 DB）。

問題：能否在「摸到 +10% 機率偏低」的時段跳過推薦？
條件變數（皆進場日 T 收盤可知）：
  A. MA60 遲滯燈（收盤跌破 MA60 逾 2% 出、站回 MA60 進 — 與 regime 研究同規則）
  B. 大盤近 20 日自高點回落是否 ≥10%（使用者定義的急跌段）
  C. 外資 10 日累計 expanding 分位（脆弱度紅燈 = 最兇 20%）
進場錨與線上一致：盤後看到 → 隔天進，保守錨 = 隔天最高價；風險調整命中 stop=−8%。

用法：python scripts/pop_regime_gate.py [n_dates] [sample] [folds] [top_pct] [stop_pct]
"""
from __future__ import annotations

import sys
from bisect import bisect_left, insort
from datetime import timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select, distinct

sys.path.insert(0, __file__.replace("\\", "/").rsplit("/scripts/", 1)[0])
from app.engines.calibration import _MIN_BARS, _iter_stock_groups  # noqa: E402
from app.storage import models  # noqa: E402
from app.storage.database import SessionLocal  # noqa: E402

_H = 30
_POP_TARGET = 0.10
_WARMUP = 200


def _pct_rank(vals):
    return (pd.Series(vals, dtype=float).rank(pct=True) * 100).to_numpy()


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
        # ── 大盤狀態（皆 point-in-time）──
        mkt = session.execute(
            select(models.MarketIndex.date, models.MarketIndex.close)
            .order_by(models.MarketIndex.date)
        ).all()
        mdates = [r[0] for r in mkt]
        mclose = [float(r[1]) for r in mkt]
        n = len(mdates)
        ma60 = [None] * n
        for i in range(59, n):
            ma60[i] = sum(mclose[i - 59:i + 1]) / 60
        # A. MA60 遲滯燈
        held = [True] * n
        h = True
        for i in range(n):
            if ma60[i] is not None:
                h = (mclose[i] > ma60[i] * 0.98) if h else (mclose[i] > ma60[i])
            held[i] = h
        # B. 近20日自高點回落≥10%
        crash20 = [False] * n
        for i in range(20, n):
            crash20[i] = mclose[i] / max(mclose[i - 19:i + 1]) - 1 <= -0.10
        # C. 外資10日累計 expanding 分位
        inst = dict(session.execute(
            select(models.InstitutionalMarketTotal.date, models.InstitutionalMarketTotal.foreign_net)
        ).all())
        f10p = [None] * n
        hist: list[float] = []
        for i in range(n):
            if i >= 9:
                v = sum(float(inst.get(mdates[j]) or 0) for j in range(i - 9, i + 1))
                if len(hist) >= 120:
                    f10p[i] = bisect_left(hist, v) / len(hist)
                insort(hist, v)
        mpos = {d: i for i, d in enumerate(mdates)}

        axis = session.execute(
            select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)
        ).scalars().all()
        eligible = axis[: len(axis) - (_H + 2)]  # 隔天進場 + H 天出場窗
        targets = sorted(eligible[::sample][-n_dates:])
        print(f"進場日 {len(targets)} 個：{targets[0]} → {targets[-1]}，切 {folds} 段")
        print(f"清單＝硬篩＋會噴前 {top_pct:.0f}%；進場=隔天最高(保守)；風險調整 stop=−{stop_pct:.0f}%\n")

        stocks = {s.id: s for s in session.execute(select(models.Stock)).scalars().all()}
        stock_ids = sorted(sid for sid in stocks if not sid.startswith("00"))
        date_lo = min(targets) - timedelta(days=_WARMUP)

        rows_by_date: dict = {t: [] for t in targets}
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
                if p is None or p < _MIN_BARS - 1 or p + 1 + _H >= len(highs):
                    continue
                ii = ind_by_date.get(T)
                if ii is None or ii < 5:
                    continue
                ind = ind_g.iloc[ii]
                atr14 = ind.get("atr14")
                ma = [ind.get(c) for c in ("ma5", "ma10", "ma20", "ma60")]
                c0 = highs[p + 1]  # 隔天最高(保守錨)
                if (not c0 or c0 <= 0 or np.isnan(c0) or atr14 is None or pd.isna(atr14)
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
                    "passed_filter": passed_filter, "hit": hit, "ra_hit": ra_hit,
                    "mfe": mfe, "mae": mae,
                })

        # 逐日橫截面 rank → 清單
        for T in targets:
            cands = rows_by_date[T]
            if not cands:
                continue
            ar = _pct_rank([c["atr_pct"] for c in cands])
            lr = _pct_rank([c["ma_align"] for c in cands])
            for c, ra, rl in zip(cands, ar, lr):
                c["pop"] = (2.0 * ra + rl) / 3.0
                c["in_list"] = c["passed_filter"] and c["pop"] >= cutoff

        def regime_of(T):
            i = mpos.get(T)
            if i is None:
                return None
            return dict(held=held[i], crash20=crash20[i],
                        frag=(f10p[i] is not None and f10p[i] < 0.2))

        def stats(dates_sel, label):
            lst = [c for d in dates_sel for c in rows_by_date[d] if c.get("in_list")]
            nd = len(dates_sel)
            if not lst:
                print(f"  {label:<24} 進場日{nd:>4}  清單0檔")
                return
            hits = sum(c["hit"] for c in lst)
            ra = sum(c["ra_hit"] for c in lst)
            mfe = np.mean([c["mfe"] for c in lst]) * 100
            mae = np.mean([c["mae"] for c in lst]) * 100
            print(f"  {label:<24} 進場日{nd:>4}  n={len(lst):>5}  "
                  f"摸+10%={hits/len(lst)*100:5.1f}%  風調命中={ra/len(lst)*100:5.1f}%  "
                  f"avgMFE={mfe:+5.1f}%  avgMAE={mae:+6.1f}%")

        states = {T: regime_of(T) for T in targets}
        valid = [T for T in targets if states[T] is not None]

        print("=== 全期 ===")
        stats(valid, "全部")
        for key, name_on, name_off in (("held", "MA60遲滯=持有", "MA60遲滯=空手"),
                                        ("crash20", "近月大盤已跌≥10%", "近月大盤未跌≥10%"),
                                        ("frag", "外資脆弱紅燈", "外資非紅燈")):
            on = [T for T in valid if states[T][key]] if key != "held" else [T for T in valid if states[T]["held"]]
            off = [T for T in valid if T not in set(on)]
            if key == "held":
                stats(on, name_on); stats(off, name_off)
            else:
                stats(on, name_on); stats(off, name_off)

        print("\n=== 組合: 遲滯空手 或 近月已跌≥10% (任一成立=閘門關) ===")
        gate_off = [T for T in valid if (not states[T]["held"]) or states[T]["crash20"]]
        gate_on = [T for T in valid if T not in set(gate_off)]
        stats(gate_off, "閘門關(跳過推薦)")
        stats(gate_on, "閘門開(正常進場)")

        # 每日明細 CSV（date-level 顯著性/逐段分析用）
        import csv, os
        out_csv = os.environ.get("REGIME_GATE_CSV")
        if out_csv:
            with open(out_csv, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["date", "held", "n", "hits", "ra_hits", "sum_mfe", "sum_mae"])
                for T in valid:
                    lst = [c for c in rows_by_date[T] if c.get("in_list")]
                    w.writerow([T, int(states[T]["held"]), len(lst),
                                sum(c["hit"] for c in lst), sum(c["ra_hit"] for c in lst),
                                round(sum(c["mfe"] for c in lst), 4), round(sum(c["mae"] for c in lst), 4)])
            print(f"[csv] {out_csv}")

        print(f"\n=== 分 {folds} 段穩定性 (MA60遲滯) ===")
        fold_dates = [valid[i * len(valid) // folds:(i + 1) * len(valid) // folds] for i in range(folds)]
        for fi, fd in enumerate(fold_dates):
            print(f"-- fold{fi+1}: {fd[0]} ~ {fd[-1]}")
            stats([T for T in fd if states[T]["held"]], "持有")
            stats([T for T in fd if not states[T]["held"]], "空手")
    finally:
        session.close()


if __name__ == "__main__":
    main()
