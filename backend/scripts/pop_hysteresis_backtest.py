"""遲滯版硬篩 vs 現行硬篩 回測（PIT，研究用，不寫DB）。

背景：現行硬篩「收盤>上揚月線」是二元開關，月線附近震盪股天天翻面 →
2026-07 實測每天榜單換血 25~40%、278 檔閃爍。方案=遲滯（hysteresis）：
  進榜（嚴）：現行硬篩全過 且 收盤 > 月線×(1+ENTER_MARGIN)
  出榜（鬆）：連 2 天收盤<月線 或 單日收盤<月線×(1−HARD_BREAK)（其餘條件只在進榜時查）
  ※ 已在榜者破線第 1 天仍留榜 —— 這批「寬限股」就是與現行版的差集。

要拍板的數據：
  表1 命中率對照：現行 vs 遲滯 清單的摸+10%率 + 風險調整命中（先+10%還是先−8%），
      walk-forward 三段獨立看。重點看「遲滯多留的寬限股」(B有A無) 本身的命中率——
      若不比清單均值差太多，穩定性就是免費的。
  表2 穩定性對照：日榜存活率 / 每檔進出次數 / 平均在榜連續天數（近一年逐日）。

進場錨與線上一致（entry-anchor-next-day）：進場價＝推薦日「隔天最高價」，
實現窗＝隔天之後 H 根。清單＝硬篩＋會噴分數（(2·rank(atr%)+rank(ma_align))/3）前 top_pct%。
用法：python scripts/pop_hysteresis_backtest.py [n_dates] [sample] [folds] [top_pct] [stop_pct]
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
_POP_TARGET = 0.10   # 摸到 +10% = 命中
_WARMUP = 260        # 往前多抓日曆天（指標回看 + 遲滯狀態機暖身）
_CHURN_DAYS = 250    # 表2 穩定性統計用最近 N 個交易日（逐日）

ENTER_MARGIN = 0.01  # 進榜要收盤 > 月線×1.01
HARD_BREAK = 0.02    # 單日收盤 < 月線×0.98 → 立即出榜
SOFT_BREAK_DAYS = 2  # 現行硬篩連 N 天不滿足 → 出榜（單日失守寬限）


def _hyst_states(closes, ma20, strict, enter_ok) -> np.ndarray:
    """逐日遲滯狀態機（單檔）。strict=現行硬篩、enter_ok=strict+進榜margin。

    出榜＝現行硬篩連 SOFT_BREAK_DAYS 天不滿足（單日失守寬限），或單日收盤跌破
    月線 HARD_BREAK 以上（大破線立即踢）。避免「只查破線」讓月線轉跌/乖離過大
    的陳舊成員賴在榜上。
    """
    n = len(closes)
    st = np.zeros(n, dtype=bool)
    fail_run = 0  # 連續「現行硬篩不滿足」天數
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
    n_dates = int(sys.argv[1]) if len(sys.argv) > 1 else 150
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
        eligible = axis[: len(axis) - _H - 1]  # 隔天進場 + H 根實現
        targets = sorted(eligible[::sample][-n_dates:])
        churn_days = [d for d in axis if d >= targets[0]][-_CHURN_DAYS:]
        need_days = sorted(set(targets) | set(churn_days))
        print(f"進場日 {len(targets)} 個：{targets[0]} → {targets[-1]}，切 {folds} 段；"
              f"穩定性統計 {churn_days[0]} → {churn_days[-1]}（{len(churn_days)} 日逐日）")
        print(f"清單＝硬篩＋會噴分前 {top_pct:.0f}%；遲滯=進榜月線+{ENTER_MARGIN:.0%}/"
              f"出榜連{SOFT_BREAK_DAYS}日破線或破{HARD_BREAK:.0%}；"
              f"進場價=隔天最高；停損 −{stop_pct:.0f}%\n")

        stocks = {s.id: s for s in session.execute(select(models.Stock)).scalars().all()}
        stock_ids = sorted(sid for sid in stocks if not sid.startswith("00"))  # 排除 ETF
        date_lo = min(targets) - timedelta(days=_WARMUP)

        # rows_by_date[T] 只存「至少一版在榜候選」的列（省記憶體）；
        # 會噴分 rank 用全市場分布（atr_all/align_all 收所有有料股，與線上一致）。
        rows_by_date: dict = {t: [] for t in need_days}
        atr_all: dict = {t: [] for t in need_days}
        align_all: dict = {t: [] for t in need_days}
        target_set = set(targets)

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

            # 對齊指標軸的收盤價（狀態機用指標軸逐日跑）
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

            for T in need_days:
                ii = ind_by_date.get(T)
                if ii is None or ii < 5:
                    continue
                atr14, cT = atr_arr[ii], close_on_ind[ii]
                mas = [col[ii] for col in ma_cols]
                if np.isnan(atr14) or np.isnan(cT) or cT <= 0 or any(np.isnan(m) for m in mas):
                    continue
                a_pct = float(atr14) / cT
                m_align = int(mas[0] > mas[1]) + int(mas[1] > mas[2]) + int(mas[2] > mas[3])
                atr_all[T].append(a_pct)      # 全市場分布（rank 母體，與線上一致）
                align_all[T].append(m_align)
                if not (strict[ii] or hyst[ii]):
                    continue  # 兩版都不在榜 → 不用存列
                row = {
                    "atr_pct": a_pct, "ma_align": m_align,
                    "strict": bool(strict[ii]), "hyst": bool(hyst[ii]), "sid": sid,
                    "hit": None, "ra_hit": None, "mfe": None, "mae": None,
                }
                if T in target_set:
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
                            tgt = next((k for k in range(len(up)) if up[k] >= _POP_TARGET), None)
                            stp = next((k for k in range(len(dn)) if dn[k] <= stop), None)
                            row["ra_hit"] = (0 if tgt is None
                                             else 1 if stp is None else int(tgt < stp))
                rows_by_date[T].append(row)

        # 逐日會噴分＝對全市場分布的百分位（searchsorted 平手取中位，等價 pandas rank(pct)）
        # in_A = 現行硬篩＋前20%；in_B = 遲滯＋前20%
        def _rank_against(sorted_arr, x):
            n = len(sorted_arr)
            lo = np.searchsorted(sorted_arr, x, side="left")
            hi = np.searchsorted(sorted_arr, x, side="right")
            return (lo + (hi - lo + 1) / 2.0) / n * 100.0

        for T in need_days:
            cands = rows_by_date[T]
            if not cands or not atr_all[T]:
                continue
            atr_sorted = np.sort(np.array(atr_all[T], dtype=float))
            align_sorted = np.sort(np.array(align_all[T], dtype=float))
            for c in cands:
                pv = (2 * _rank_against(atr_sorted, c["atr_pct"])
                      + _rank_against(align_sorted, c["ma_align"])) / 3.0
                c["in_A"] = bool(c["strict"] and pv >= cutoff)
                c["in_B"] = bool(c["hyst"] and pv >= cutoff)

        # ── 表1 命中率對照（walk-forward 分段） ──
        fold_dates = [targets[i * len(targets) // folds:(i + 1) * len(targets) // folds]
                      for i in range(folds)]

        def agg(dates, pred):
            lst = [c for d in dates for c in rows_by_date.get(d, [])
                   if c["hit"] is not None and pred(c)]
            if not lst:
                return None
            return {
                "n": len(lst),
                "hit": float(np.mean([c["hit"] for c in lst])) * 100,
                "ra": float(np.mean([c["ra_hit"] for c in lst])) * 100,
                "mfe": float(np.mean([c["mfe"] for c in lst])) * 100,
                "mae": float(np.mean([c["mae"] for c in lst])) * 100,
            }

        def fmt(r):
            if r is None:
                return "  (無樣本)"
            return (f"n={r['n']:>5}  摸+10%={r['hit']:5.1f}%  風調命中={r['ra']:5.1f}%  "
                    f"avgMFE={r['mfe']:+5.1f}%  avgMAE={r['mae']:+5.1f}%")

        print("── 表1 命中率對照（A=現行、B=遲滯、寬限股=B有A無、被擋股=A有B無） ──")
        for i, fd in enumerate(fold_dates):
            print(f"\n[fold {i+1}] {fd[0]} → {fd[-1]}")
            print("  A 現行清單   " + fmt(agg(fd, lambda c: c["in_A"])))
            print("  B 遲滯清單   " + fmt(agg(fd, lambda c: c["in_B"])))
            print("  寬限股(B-A)  " + fmt(agg(fd, lambda c: c["in_B"] and not c["in_A"])))
            print("  被擋股(A-B)  " + fmt(agg(fd, lambda c: c["in_A"] and not c["in_B"])))
        print("\n[全期]")
        print("  A 現行清單   " + fmt(agg(targets, lambda c: c["in_A"])))
        print("  B 遲滯清單   " + fmt(agg(targets, lambda c: c["in_B"])))
        print("  寬限股(B-A)  " + fmt(agg(targets, lambda c: c["in_B"] and not c["in_A"])))
        print("  被擋股(A-B)  " + fmt(agg(targets, lambda c: c["in_A"] and not c["in_B"])))

        # ── 表2 穩定性對照（近一年逐日） ──
        print("\n── 表2 穩定性對照（逐日）──")
        for key, label in (("in_A", "A 現行"), ("in_B", "B 遲滯")):
            members = {d: {c["sid"] for c in rows_by_date.get(d, []) if c.get(key)}
                       for d in churn_days}
            sizes = [len(members[d]) for d in churn_days]
            # k 日存活率：day d 在榜者，k 個交易日後仍在榜的比例
            surv_k = {}
            for k in (1, 3, 5, 10):
                vals = [len(members[churn_days[i]] & members[churn_days[i + k]])
                        / len(members[churn_days[i]])
                        for i in range(len(churn_days) - k) if members[churn_days[i]]]
                surv_k[k] = float(np.mean(vals)) * 100 if vals else float("nan")
            # 每檔進出次數 + 在榜連續段長
            trans: dict = {}
            runs: list[int] = []
            run_now: dict = {}
            for d in churn_days:
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
