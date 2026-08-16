"""推薦 cohort 健康度閘門（PIT walk-forward，研究用，不寫 DB）。

問題：7 月式崩壞要到月底回看才發現。能否用「最近 K 個選股日推出的清單
到今天為止的實況」當即時儀表，健康度差 → 跳過進場？
這不是預測大盤方向（已定論不可測），是把回饋週期從 30 天縮到 1~2 天。

健康度指標（T 日收盤可知，cohort = T-K‥T-2 選股日的清單、進場錨=隔天最高）：
  alive  = 收盤仍在進場價上的比率
  medret = 到 T 收盤報酬中位數
  hitso  = 已摸 +10% 比率
  dn8    = 已破 −8% 比率
驗證：健康差 vs 好的「往後30天摸+10%」日層級差 + Welch t + 3 段 fold +
      circular-shift 安慰劑 + 對 MA60 遲滯閘門的增量（held=True 內再切）。
清單重建與 pop_regime_gate.py 完全同規則（硬篩＋會噴 rank 前 20%）。

用法：python scripts/pop_cohort_health_gate.py [n_dates] [K] [n_shifts]
"""
from __future__ import annotations

import sys
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


def _welch_t(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    va, vb = a.var(ddof=1), b.var(ddof=1)
    se = np.sqrt(va / len(a) + vb / len(b))
    return float((a.mean() - b.mean()) / se) if se > 0 else float("nan")


def main() -> None:
    n_dates = int(sys.argv[1]) if len(sys.argv) > 1 else 1150
    K = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    n_shifts = int(sys.argv[3]) if len(sys.argv) > 3 else 2000
    top_pct = 20.0
    cutoff = 100.0 - top_pct
    session = SessionLocal()
    try:
        # ── 大盤 MA60 遲滯（對照組）──
        mkt = session.execute(
            select(models.MarketIndex.date, models.MarketIndex.close)
            .order_by(models.MarketIndex.date)
        ).all()
        mdates = [r[0] for r in mkt]
        mclose = [float(r[1]) for r in mkt]
        nm = len(mdates)
        ma60 = [None] * nm
        for i in range(59, nm):
            ma60[i] = sum(mclose[i - 59:i + 1]) / 60
        held_l = [True] * nm
        h = True
        for i in range(nm):
            if ma60[i] is not None:
                h = (mclose[i] > ma60[i] * 0.98) if h else (mclose[i] > ma60[i])
            held_l[i] = h
        held_by_date = dict(zip(mdates, held_l))

        axis = session.execute(
            select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)
        ).scalars().all()
        eligible = axis[: len(axis) - (_H + 2)]  # 隔天進場 + H 天出場窗
        targets = sorted(eligible[-n_dates:])    # 連續日（cohort 需要）
        tpos = {d: i for i, d in enumerate(targets)}
        apos = {d: i for i, d in enumerate(axis)}
        print(f"進場日 {len(targets)} 個(連續)：{targets[0]} → {targets[-1]}，cohort K={K}")

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
            ind_dates = list(ind_g["date"])
            ind_by_date = {d: i for i, d in enumerate(ind_dates)}
            ma20_arr = ind_g["ma20"].to_numpy(dtype=float)
            bias60_arr = ind_g["bias_60"].to_numpy(dtype=float)
            atr_arr = ind_g["atr14"].to_numpy(dtype=float)
            ma5_arr = ind_g["ma5"].to_numpy(dtype=float)
            ma10_arr = ind_g["ma10"].to_numpy(dtype=float)
            ma60_arr = ind_g["ma60"].to_numpy(dtype=float)

            for T in targets:
                p = pos.get(T)
                if p is None or p < _MIN_BARS - 1 or p + 1 + _H >= len(highs):
                    continue
                ii = ind_by_date.get(T)
                if ii is None or ii < 5:
                    continue
                cT = closes[p]
                ma20_now, ma20_prev = ma20_arr[ii], ma20_arr[ii - 5]
                bias60 = bias60_arr[ii]
                above_rising = bool(cT > ma20_now and ma20_now > ma20_prev) if not (
                    np.isnan(ma20_now) or np.isnan(ma20_prev)) else False
                near60 = bool(abs(bias60) < 15) if not np.isnan(bias60) else False
                if not (above_rising and near60):
                    continue  # 硬篩沒過的不進 rank 也不佔記憶體
                c0 = highs[p + 1]
                atr14 = atr_arr[ii]
                mas = (ma5_arr[ii], ma10_arr[ii], ma20_arr[ii], ma60_arr[ii])
                if (not c0 or c0 <= 0 or np.isnan(c0) or np.isnan(atr14)
                        or any(np.isnan(m) for m in mas) or not cT):
                    continue
                atr_pct = float(atr14) / cT
                ma_align = int(mas[0] > mas[1]) + int(mas[1] > mas[2]) + int(mas[2] > mas[3])

                fhi = highs[p + 2: p + 2 + _H]
                flo = lows[p + 2: p + 2 + _H]
                fcl = closes[p + 2: p + 2 + _H]
                m = ~(np.isnan(fhi) | np.isnan(flo))
                if m.sum() == 0:
                    continue
                up = fhi / c0 - 1.0
                dn = flo / c0 - 1.0
                cr = fcl / c0 - 1.0
                mfe = float(np.nanmax(up))
                hit = 1 if mfe >= _POP_TARGET else 0
                # 健康度只需前 K+2 根（float32 省記憶體）
                nb = K + 2
                rows_by_date[T].append({
                    "atr_pct": atr_pct, "ma_align": ma_align, "hit": hit,
                    "mfe": mfe, "mae": float(np.nanmin(dn)),
                    "ret5": (float(cr[:5][~np.isnan(cr[:5])][-1])
                             if (~np.isnan(cr[:5])).any() else np.nan),
                    "mae5": (float(np.nanmin(dn[:5]))
                             if (~np.isnan(dn[:5])).any() else np.nan),
                    "up_k": up[:nb].astype(np.float32),
                    "dn_k": dn[:nb].astype(np.float32),
                    "cr_k": cr[:nb].astype(np.float32),
                })

        # 逐日 rank → 清單（硬篩已在上面過了）
        for T in targets:
            cands = rows_by_date[T]
            if not cands:
                continue
            ar = _pct_rank([c["atr_pct"] for c in cands])
            lr = _pct_rank([c["ma_align"] for c in cands])
            for c, ra, rl in zip(cands, ar, lr):
                c["in_list"] = (2.0 * ra + rl) / 3.0 >= cutoff
        lists = {T: [c for c in rows_by_date[T] if c.get("in_list")] for T in targets}

        # ── cohort 健康度（PIT：T 收盤時看得到的）──
        health: dict = {}
        for T in targets:
            ti = tpos[T]
            alive_n = alive_y = hit_y = dn8_y = 0
            rets: list[float] = []
            for back in range(2, K + 2):          # E = T-2 ‥ T-(K+1)（E=T-1 尚無實況）
                if ti - back < 0:
                    break
                E = targets[ti - back]
                elapsed = apos[T] - apos[E] - 1   # E+2 起算、含 T 的實況根數（全市場軸近似）
                if elapsed < 1:
                    continue
                for c in lists[E]:
                    upk, dnk, crk = c["up_k"], c["dn_k"], c["cr_k"]
                    e = min(elapsed, len(crk))
                    if e < 1 or np.isnan(crk[e - 1]):
                        continue
                    alive_n += 1
                    if crk[e - 1] > 0:
                        alive_y += 1
                    rets.append(float(crk[e - 1]))
                    if np.nanmax(upk[:e]) >= _POP_TARGET:
                        hit_y += 1
                    if np.nanmin(dnk[:e]) <= -0.08:
                        dn8_y += 1
            if alive_n >= 10:
                health[T] = dict(
                    n=alive_n, alive=alive_y / alive_n, medret=float(np.median(rets)),
                    hitso=hit_y / alive_n, dn8=dn8_y / alive_n,
                )

        valid = [T for T in targets if T in health and lists[T]]
        day_hit = {T: np.mean([c["hit"] for c in lists[T]]) for T in valid}
        day_mae = {T: np.mean([c["mae"] for c in lists[T]]) for T in valid}
        day_ret5 = {T: np.nanmean([c["ret5"] for c in lists[T]]) for T in valid}
        day_mae5 = {T: np.nanmean([c["mae5"] for c in lists[T]]) for T in valid}
        print(f"可評估日 {len(valid)}（有健康度且當日清單非空）\n")

        def stats(sel, label):
            if not sel:
                print(f"  {label:<30} 0 日")
                return None
            arr = np.array([day_hit[T] for T in sel])
            mae = np.mean([day_mae[T] for T in sel]) * 100
            r5 = np.nanmean([day_ret5[T] for T in sel]) * 100
            m5 = np.nanmean([day_mae5[T] for T in sel]) * 100
            navg = np.mean([len(lists[T]) for T in sel])
            print(f"  {label:<30} 日={len(sel):>4}  日均清單{navg:4.1f}檔  "
                  f"日層級摸+10%={arr.mean()*100:5.1f}%  avgMAE={mae:+6.1f}%  "
                  f"ret5={r5:+5.1f}%  mae5={m5:+5.1f}%")
            return arr

        # ── 描述性：健康度五分位 → 往後命中 ──
        for key in ("alive", "medret", "hitso", "dn8"):
            vals = np.array([health[T][key] for T in valid])
            qs = np.quantile(vals, [0.2, 0.4, 0.6, 0.8])
            print(f"=== {key} 五分位 → 當日清單往後30天日層級命中 ===")
            for qi in range(5):
                lo = -np.inf if qi == 0 else qs[qi - 1]
                hi = np.inf if qi == 4 else qs[qi]
                sel = [T for T, v in zip(valid, vals) if lo < v <= hi]
                stats(sel, f"Q{qi+1} ({lo:.2f},{hi:.2f}]")
            print()

        # ── 主閘門：alive<0.4 或 medret<-0.02（cohort 過半套牢且中位虧損²選一寬鬆或）──
        def bad_of(T):
            hh = health[T]
            return hh["alive"] < 0.40 or hh["medret"] < -0.02

        bad = [T for T in valid if bad_of(T)]
        good = [T for T in valid if not bad_of(T)]
        print("=== 主閘門: alive<40% or medret<-2% ===")
        a = stats(bad, "健康差(跳過)")
        b = stats(good, "健康好(進場)")
        if a is not None and b is not None:
            print(f"  Welch t (good-bad, 日層級) = {_welch_t(b, a):+.2f}")

        # 對 MA60 的增量
        print("\n=== 對 MA60 遲滯的增量（held=True 內再切健康度）===")
        held_T = [T for T in valid if held_by_date.get(T, True)]
        stats([T for T in held_T if bad_of(T)], "MA60持有 × 健康差")
        stats([T for T in held_T if not bad_of(T)], "MA60持有 × 健康好")
        stats([T for T in valid if not held_by_date.get(T, True)], "MA60空手(對照)")

        # 敏感度：K 固定、門檻掃
        print("\n=== 門檻敏感度 (單指標) ===")
        for key, ths, sign in (("alive", (0.35, 0.40, 0.45, 0.50), "<"),
                               ("medret", (-0.03, -0.02, -0.01, 0.0), "<"),
                               ("dn8", (0.25, 0.30, 0.35), ">")):
            for th in ths:
                sel_bad = [T for T in valid if (health[T][key] < th if sign == "<" else health[T][key] > th)]
                sel_good = [T for T in valid if T not in set(sel_bad)]
                ha = np.mean([day_hit[T] for T in sel_bad]) * 100 if sel_bad else float("nan")
                hb = np.mean([day_hit[T] for T in sel_good]) * 100 if sel_good else float("nan")
                print(f"  {key}{sign}{th:+.2f}: 差{len(sel_bad):>4}日 {ha:5.1f}%  "
                      f"好{len(sel_good):>4}日 {hb:5.1f}%  Δ={hb-ha:+5.1f}pp")

        # 3 段 fold
        print("\n=== 3 段穩定性（主閘門）===")
        for fi in range(3):
            fd = valid[fi * len(valid) // 3:(fi + 1) * len(valid) // 3]
            print(f"-- fold{fi+1}: {fd[0]} ~ {fd[-1]}")
            stats([T for T in fd if bad_of(T)], "健康差")
            stats([T for T in fd if not bad_of(T)], "健康好")

        # circular-shift 安慰劑（日層級命中差）
        state = np.array([bad_of(T) for T in valid])
        hits_arr = np.array([day_hit[T] for T in valid])
        real_gap = hits_arr[~state].mean() - hits_arr[state].mean() if state.any() and (~state).any() else float("nan")
        rng = np.random.default_rng(42)
        beat = 0
        tot = 0
        for _ in range(n_shifts):
            k = int(rng.integers(1, len(state)))
            s = np.roll(state, k)
            if s.any() and (~s).any():
                gap = hits_arr[~s].mean() - hits_arr[s].mean()
                tot += 1
                if gap >= real_gap:
                    beat += 1
        print(f"\n=== circular-shift 安慰劑 ===\n  真實差 {real_gap*100:+.1f}pp，"
              f"{tot} 次平移中 {beat} 次 ≥ 真實（p≈{beat/max(tot,1):.3f}）")

        # 亮燈時序（最近 60 個可評估日）
        print("\n=== 最近 60 日健康度時序（含 2026-07）===")
        for T in valid[-60:]:
            hh = health[T]
            flag = "🔴" if bad_of(T) else "  "
            held_f = " " if held_by_date.get(T, True) else "M"  # M = MA60 空手
            print(f"  {T} {flag}{held_f} alive={hh['alive']*100:3.0f}% med={hh['medret']*100:+5.1f}% "
                  f"hitso={hh['hitso']*100:3.0f}% dn8={hh['dn8']*100:3.0f}% "
                  f"(cohort n={hh['n']}, 當日清單{len(lists[T])}檔, 往後命中{day_hit[T]*100:.0f}%)")
    finally:
        session.close()


if __name__ == "__main__":
    main()
