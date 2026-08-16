"""時間結構（路徑形狀）→ 動能方向 對會噴的增量 IC（PIT walk-forward，研究用）。

假說（使用者 2026-07-28）：二維快照丟失時間結構；看得到路徑形狀就能觀察動能方向。
測法：在現行清單（硬篩＋會噴 rank 前 20%，與線上同規則）**清單內**算 6 個
路徑特徵，逐日 Spearman IC 對「往後 30 天摸 +10%」，日層級 t + 五分位 + 3 fold。
清單內 = 直接回答「對現有兩因子 rank 有無增量」。

特徵（皆 T 收盤 PIT 可知，W=回看窗）：
  ret5 / ret20   簡單動能（舊研究已判死的基線，對照用）
  slope10        近10日收盤 OLS 斜率 /日（%），方向+強度
  r2_20          近20日線性擬合 R²，趨勢平滑度（穩步爬 vs 一根噴）
  accel          ret5 − ret20×(5/20)，動能加速度（方向變化）
  brk20          收盤距近20日高點（0=創高，越負越深）
  upratio10      近10日紅K比率

用法：python scripts/pop_pathshape_ic.py [n_dates]
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
_FEATS = ["ret5", "ret20", "slope10", "r2_20", "accel", "brk20", "upratio10"]


def _pct_rank(vals):
    return (pd.Series(vals, dtype=float).rank(pct=True) * 100).to_numpy()


def _feats(closes: np.ndarray, p: int) -> dict | None:
    w = closes[p - 20: p + 1]
    if len(w) < 21 or np.isnan(w).any() or (w <= 0).any():
        return None
    c = w[-1]
    ret5 = c / w[-6] - 1.0
    ret20 = c / w[0] - 1.0
    y10 = np.log(w[-10:])
    x10 = np.arange(10.0)
    slope10 = float(np.polyfit(x10, y10, 1)[0])
    y20 = np.log(w)
    x20 = np.arange(21.0)
    b = np.polyfit(x20, y20, 1)
    resid = y20 - (b[0] * x20 + b[1])
    ss_tot = float(((y20 - y20.mean()) ** 2).sum())
    r2 = 1.0 - float((resid ** 2).sum()) / ss_tot if ss_tot > 0 else 0.0
    accel = ret5 - ret20 * (5.0 / 20.0)
    brk20 = c / float(w.max()) - 1.0
    d10 = np.diff(w[-11:])
    upratio10 = float((d10 > 0).mean())
    return {"ret5": ret5, "ret20": ret20, "slope10": slope10, "r2_20": r2,
            "accel": accel, "brk20": brk20, "upratio10": upratio10}


def main() -> None:
    n_dates = int(sys.argv[1]) if len(sys.argv) > 1 else 1150
    top_pct = 20.0
    cutoff = 100.0 - top_pct
    session = SessionLocal()
    try:
        axis = session.execute(
            select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)
        ).scalars().all()
        eligible = axis[: len(axis) - (_H + 2)]
        targets = sorted(eligible[-n_dates:])
        print(f"進場日 {len(targets)} 個：{targets[0]} → {targets[-1]}（清單內路徑特徵 IC）")

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
            atr_arr = ind_g["atr14"].to_numpy(dtype=float)
            ma5_arr = ind_g["ma5"].to_numpy(dtype=float)
            ma10_arr = ind_g["ma10"].to_numpy(dtype=float)
            ma60_arr = ind_g["ma60"].to_numpy(dtype=float)

            for T in targets:
                p = pos.get(T)
                if p is None or p < max(_MIN_BARS - 1, 21) or p + 1 + _H >= len(highs):
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
                    continue
                c0 = highs[p + 1]
                atr14 = atr_arr[ii]
                mas = (ma5_arr[ii], ma10_arr[ii], ma20_arr[ii], ma60_arr[ii])
                if (not c0 or c0 <= 0 or np.isnan(c0) or np.isnan(atr14)
                        or any(np.isnan(m) for m in mas) or not cT):
                    continue
                fe = _feats(closes, p)
                if fe is None:
                    continue
                fhi = highs[p + 2: p + 2 + _H]
                m = ~np.isnan(fhi)
                if m.sum() == 0:
                    continue
                mfe = float(np.nanmax(fhi / c0 - 1.0))
                fe.update({
                    "atr_pct": float(atr14) / cT,
                    "ma_align": int(mas[0] > mas[1]) + int(mas[1] > mas[2]) + int(mas[2] > mas[3]),
                    "hit": 1 if mfe >= _POP_TARGET else 0,
                })
                rows_by_date[T].append(fe)

        for T in targets:
            cands = rows_by_date[T]
            if not cands:
                continue
            ar = _pct_rank([c["atr_pct"] for c in cands])
            lr = _pct_rank([c["ma_align"] for c in cands])
            for c, ra, rl in zip(cands, ar, lr):
                c["in_list"] = (2.0 * ra + rl) / 3.0 >= cutoff
        lists = {T: [c for c in rows_by_date[T] if c.get("in_list")] for T in targets}
        valid = [T for T in targets if len(lists[T]) >= 10]
        print(f"可評估日 {len(valid)}（清單 ≥10 檔），日均清單 "
              f"{np.mean([len(lists[T]) for T in valid]):.1f} 檔，"
              f"整體命中 {np.mean([c['hit'] for T in valid for c in lists[T]])*100:.1f}%\n")

        # 逐日清單內 Spearman IC（hit 二元 → rank-biserial 等價）
        print("=== 清單內逐日 IC（mean ± t）＋ 3 fold ===")
        folds = [valid[i * len(valid) // 3:(i + 1) * len(valid) // 3] for i in range(3)]
        for f in _FEATS:
            ics = {}
            for T in valid:
                df = pd.DataFrame(lists[T])
                if df["hit"].nunique() < 2 or df[f].nunique() < 2:
                    continue
                ics[T] = df[f].corr(df["hit"], method="spearman")
            arr = np.array(list(ics.values()))
            t = arr.mean() / (arr.std(ddof=1) / np.sqrt(len(arr))) if len(arr) > 2 else float("nan")
            fold_s = " / ".join(
                f"{np.mean([v for T, v in ics.items() if T in set(fd)])*100:+.1f}"
                for fd in folds)
            print(f"  {f:<10} IC={arr.mean()*100:+5.1f} (t={t:+5.1f}, 日={len(arr)})  folds[{fold_s}]")

        # 清單內五分位命中（pooled，逐日內分位再彙總）
        print("\n=== 清單內五分位命中率（逐日內排 → pooled）===")
        for f in _FEATS:
            buckets = [[] for _ in range(5)]
            for T in valid:
                df = pd.DataFrame(lists[T])
                if df[f].nunique() < 2:
                    continue
                q = pd.qcut(df[f].rank(method="first"), 5, labels=False)
                for b, h in zip(q, df["hit"]):
                    buckets[int(b)].append(h)
            hits = [np.mean(b) * 100 if b else float("nan") for b in buckets]
            print(f"  {f:<10} " + "  ".join(f"Q{i+1}={h:5.1f}%" for i, h in enumerate(hits))
                  + f"   Q5−Q1={hits[4]-hits[0]:+5.1f}pp")
    finally:
        session.close()


if __name__ == "__main__":
    main()
