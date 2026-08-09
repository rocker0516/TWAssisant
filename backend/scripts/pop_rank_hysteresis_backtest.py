"""會噴分數 rank 遲滯回測（遲滯第二階段，PIT，研究用，不寫 DB）。

背景：硬篩遲滯已上線（strict_filter），但「會噴分前 20%」的分數邊界附近
股每天仍跳進跳出。方案＝rank 遲滯：
  進榜：硬篩遲滯在榜 且 會噴分 percentile ≥ ENTER_CUT（前 20%）
  留榜：已在榜者放鬆到 percentile ≥ STAY_CUT 才踢（硬篩遲滯掉了照樣踢）

rank 遲滯有路徑依賴 → 必須逐日跑（不能週頻抽樣），預設近 500 交易日。
前 BURN 日為狀態機暖身，不進統計。

要拍板的數據：
  表1 命中率：B=現行(無rank遲滯) vs C=rank遲滯(兩檔 stay cut)，
      重點看「rank寬限股」(C有B無) 的 30 日摸+10% —— 不比清單均值差太多
      穩定性就是免費的。
  表2 穩定性：日榜存活率 / 平均在榜連續天數。
進場錨=隔天最高(保守)、實現窗=隔天之後 30 根摸 +10%，與線上定義一致。
用法：python scripts/pop_rank_hysteresis_backtest.py [n_days] [stay1] [stay2]
"""

from __future__ import annotations

import sys
from datetime import timedelta

import numpy as np
from sqlalchemy import distinct, select

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])

from app.engines.calibration import _MIN_BARS, _iter_stock_groups  # noqa: E402
from app.storage import models  # noqa: E402
from app.storage.database import SessionLocal  # noqa: E402

_H = 30              # 實現窗（未來交易日，與會噴定義一致：30 日內碰到 +10%）
_POP_TARGET = 0.10
_WARMUP = 260        # 往前多抓日曆天（指標回看 + 硬篩遲滯狀態機暖身）
_BURN = 10           # rank 遲滯狀態機暖身天數（不進統計）

# 硬篩遲滯參數（與 pop_hysteresis_backtest.py / 線上一致）
ENTER_MARGIN = 0.01
HARD_BREAK = 0.02
SOFT_BREAK_DAYS = 2

ENTER_CUT = 80.0     # 進榜：會噴分 percentile ≥ 80（前 20%，與線上 top_pct=20 一致）


def _hyst_states(closes, ma20, strict, enter_ok) -> np.ndarray:
    """逐日硬篩遲滯狀態機（單檔，與 pop_hysteresis_backtest.py 同規則）。"""
    n = len(closes)
    st = np.zeros(n, dtype=bool)
    fail_run = 0
    for i in range(n):
        c, m = closes[i], ma20[i]
        bad = np.isnan(c) or np.isnan(m)
        fail_run = 0 if strict[i] else fail_run + 1
        prev = st[i - 1] if i else False
        if prev:
            hard = bad or c < m * (1.0 - HARD_BREAK)
            soft = fail_run >= SOFT_BREAK_DAYS
            st[i] = not (hard or soft)
        else:
            st[i] = bool(enter_ok[i])
    return st


def main() -> None:
    n_days = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    stay1 = float(sys.argv[2]) if len(sys.argv) > 2 else 75.0
    stay2 = float(sys.argv[3]) if len(sys.argv) > 3 else 70.0
    variants = [(f"in_C{int(s)}", s) for s in (stay1, stay2)]
    session = SessionLocal()
    try:
        axis = session.execute(
            select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)
        ).scalars().all()
        days = axis[-n_days:]
        stat_days = days[_BURN:]
        print(f"逐日 {len(days)} 個交易日：{days[0]} → {days[-1]}（前 {_BURN} 日暖身不計）")
        print(f"進榜=硬篩遲滯+會噴分前 {100-ENTER_CUT:.0f}%；留榜放鬆到 percentile "
              f"≥ {stay1:.0f} / {stay2:.0f}；進場價=隔天最高；30 日摸+10%\n")

        stocks = {s.id: s for s in session.execute(select(models.Stock)).scalars().all()}
        stock_ids = sorted(sid for sid in stocks if not sid.startswith("00"))  # 排除 ETF
        date_lo = min(days) - timedelta(days=_WARMUP)

        rows_by_date: dict = {t: [] for t in days}
        atr_all: dict = {t: [] for t in days}
        align_all: dict = {t: [] for t in days}

        for sid, pdf, ind_g, _inst, _margin in _iter_stock_groups(session, stock_ids, date_lo):
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
            ma_cols = [ind_g[c].to_numpy(dtype=float) for c in ("ma5", "ma10", "ma20", "ma60")]
            close_on_ind = np.array(
                [closes[pos[d]] if d in pos else np.nan for d in ind_dates], dtype=float)

            n = len(ind_dates)
            strict = np.zeros(n, dtype=bool)
            enter_ok = np.zeros(n, dtype=bool)
            for i in range(5, n):
                m_now, m_prev, b60, c = ma20_arr[i], ma20_arr[i - 5], bias60_arr[i], close_on_ind[i]
                if np.isnan(m_now) or np.isnan(m_prev) or np.isnan(b60) or np.isnan(c):
                    continue
                rising = m_now > m_prev
                strict[i] = c > m_now and rising and abs(b60) < 15
                enter_ok[i] = strict[i] and c > m_now * (1.0 + ENTER_MARGIN)
            hyst = _hyst_states(close_on_ind, ma20_arr, strict, enter_ok)

            for T in days:
                ii = ind_by_date.get(T)
                if ii is None or ii < 5:
                    continue
                atr14, cT = atr_arr[ii], close_on_ind[ii]
                mas = [col[ii] for col in ma_cols]
                if np.isnan(atr14) or np.isnan(cT) or cT <= 0 or any(np.isnan(m) for m in mas):
                    continue
                a_pct = float(atr14) / cT
                m_align = int(mas[0] > mas[1]) + int(mas[1] > mas[2]) + int(mas[2] > mas[3])
                atr_all[T].append(a_pct)
                align_all[T].append(m_align)
                if not hyst[ii]:
                    continue  # B/C 都以硬篩遲滯在榜為前提
                row = {
                    "sid": sid, "atr_pct": a_pct, "ma_align": m_align,
                    "hit": None, "mfe": None, "mae": None,
                }
                p = pos.get(T)
                if p is not None and p >= _MIN_BARS - 1 and p + 1 < len(highs):
                    c0 = highs[p + 1]  # 進場錨：隔天最高（保守）
                    fhi = highs[p + 2: p + 2 + _H]
                    flo = lows[p + 2: p + 2 + _H]
                    m = ~(np.isnan(fhi) | np.isnan(flo))
                    fhi, flo = fhi[m], flo[m]
                    if c0 and c0 > 0 and not np.isnan(c0) and len(fhi):
                        up = fhi / c0 - 1.0
                        dn = flo / c0 - 1.0
                        row["mfe"], row["mae"] = float(up.max()), float(dn.min())
                        row["hit"] = 1 if row["mfe"] >= _POP_TARGET else 0
                rows_by_date[T].append(row)

        def _rank_against(sorted_arr, x):
            n = len(sorted_arr)
            lo = np.searchsorted(sorted_arr, x, side="left")
            hi = np.searchsorted(sorted_arr, x, side="right")
            return (lo + (hi - lo + 1) / 2.0) / n * 100.0

        # 逐日（時序）跑 rank 遲滯狀態機 —— member 集合帶到下一天
        member: dict = {name: set() for name, _ in variants}
        for T in days:
            cands = rows_by_date[T]
            if not cands or not atr_all[T]:
                for name, _ in variants:
                    member[name] = set()
                continue
            atr_sorted = np.sort(np.array(atr_all[T], dtype=float))
            align_sorted = np.sort(np.array(align_all[T], dtype=float))
            for c in cands:
                pv = (2 * _rank_against(atr_sorted, c["atr_pct"])
                      + _rank_against(align_sorted, c["ma_align"])) / 3.0
                c["in_B"] = pv >= ENTER_CUT
                for name, stay in variants:
                    c[name] = pv >= ENTER_CUT or (c["sid"] in member[name] and pv >= stay)
            for name, _ in variants:
                member[name] = {c["sid"] for c in cands if c[name]}

        # ── 表1 命中率（walk-forward 3 段） ──
        folds = 3
        fold_dates = [stat_days[i * len(stat_days) // folds:(i + 1) * len(stat_days) // folds]
                      for i in range(folds)]

        def agg(dates, pred):
            lst = [c for d in dates for c in rows_by_date.get(d, [])
                   if c["hit"] is not None and pred(c)]
            if not lst:
                return None
            return {
                "n": len(lst),
                "hit": float(np.mean([c["hit"] for c in lst])) * 100,
                "mfe": float(np.mean([c["mfe"] for c in lst])) * 100,
                "mae": float(np.mean([c["mae"] for c in lst])) * 100,
            }

        def fmt(r):
            if r is None:
                return "  (無樣本)"
            return (f"n={r['n']:>6}  摸+10%={r['hit']:5.1f}%  "
                    f"avgMFE={r['mfe']:+5.1f}%  avgMAE={r['mae']:+5.1f}%")

        rows_spec = [("B 現行(無rank遲滯) ", lambda c: c["in_B"])]
        for name, stay in variants:
            rows_spec.append((f"C{int(stay)} rank遲滯      ",
                              lambda c, k=name: c[k]))
            rows_spec.append((f"  寬限股(C{int(stay)}-B)  ",
                              lambda c, k=name: c[k] and not c["in_B"]))

        print("── 表1 命中率對照（B=現行、C=rank遲滯、寬限股=C有B無） ──")
        for i, fd in enumerate(fold_dates):
            print(f"\n[fold {i+1}] {fd[0]} → {fd[-1]}")
            for label, pred in rows_spec:
                print(f"  {label}" + fmt(agg(fd, pred)))
        print("\n[全期]")
        for label, pred in rows_spec:
            print(f"  {label}" + fmt(agg(stat_days, pred)))

        # ── 表2 穩定性（逐日） ──
        print("\n── 表2 穩定性對照（逐日）──")
        keys = [("in_B", "B 現行")] + [(name, f"C{int(stay)} rank遲滯")
                                       for name, stay in variants]
        for key, label in keys:
            members = {d: {c["sid"] for c in rows_by_date.get(d, []) if c.get(key)}
                       for d in stat_days}
            sizes = [len(members[d]) for d in stat_days]
            surv_k = {}
            for k in (1, 3, 5, 10):
                vals = [len(members[stat_days[i]] & members[stat_days[i + k]])
                        / len(members[stat_days[i]])
                        for i in range(len(stat_days) - k) if members[stat_days[i]]]
                surv_k[k] = float(np.mean(vals)) * 100 if vals else float("nan")
            trans: dict = {}
            runs: list = []
            run_now: dict = {}
            for d in stat_days:
                mem = members[d]
                for sid in mem:
                    run_now[sid] = run_now.get(sid, 0) + 1
                for sid in list(run_now):
                    if sid not in mem:
                        runs.append(run_now.pop(sid))
                        trans[sid] = trans.get(sid, 0) + 1
            runs.extend(run_now.values())
            flip2 = sum(1 for v in trans.values() if v >= 2)
            print(f"  {label}: 日均榜量={np.mean(sizes):.0f}  "
                  f"存活率 1日={surv_k[1]:.1f}% 3日={surv_k[3]:.1f}% "
                  f"5日={surv_k[5]:.1f}% 10日={surv_k[10]:.1f}%  "
                  f"平均在榜連續={np.mean(runs):.1f}天  出榜≥2次股數={flip2}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
