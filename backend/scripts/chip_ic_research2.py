"""籌碼「用法」重構回測 round2（PIT，研究用，不寫DB）。

round1 證偽的只是「法人買超量級的橫截面線性選股」。本輪測其他用法是否突破 +5pp 天花板：
  A 持續性  ：20日內法人買超天數比例、投信連續買超天數（穩定吸貨 vs 單日爆量）
  B 拆投信  ：投信獨立佔量、外資投信「共識同向」
  C 加速度  ：近5日佔量 − 前15日佔量（買盤轉強拐點）
  D 時序擇時：個股「20日累計法人由負轉正(翻買)」當天進場 → 之後摸+10%率 vs 該股基準
              （= 框架裡「選池→擇時」那條未驗證的腿；event study 非橫截面）
  E 大盤regime：用 BFI82U 全市場法人淨額把進場日切兩半，比多頭池命中率 & 個股籌碼IC強弱
所有 outcome 與 round1 一致（進場價=當天高、之後20日 MFE 摸+10%）。對照=波動度/均線。

用法：python scripts/chip_ic_research2.py [n_dates] [sample]
"""

from __future__ import annotations

import sys
from datetime import timedelta

import numpy as np
import pandas as pd
from sqlalchemy import distinct, select

sys.path.insert(0, __file__.replace("\\", "/").rsplit("/scripts/", 1)[0])

from app.engines.calibration import _INST_COLS, _MIN_BARS, _iter_stock_groups  # noqa: E402
from app.storage import models  # noqa: E402
from app.storage.database import SessionLocal  # noqa: E402

_H = 20
_POP_TARGET = 0.10
_WARMUP = 160


def _spearman(a, b):
    if len(a) < 8:
        return None
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def _tstat(vals):
    arr = np.array([v for v in vals if v is not None], dtype=float)
    n = len(arr)
    if n < 2:
        return (float("nan"), float("nan"), n)
    m = arr.mean()
    se = arr.std(ddof=1) / np.sqrt(n)
    return (m, m / se if se else float("nan"), n)


def main() -> None:
    n_dates = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    sample = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    session = SessionLocal()
    try:
        axis = session.execute(
            select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)
        ).scalars().all()
        eligible = axis[: len(axis) - _H]
        targets = sorted(eligible[::sample][-n_dates:])
        tset = set(targets)
        print(f"進場日 {len(targets)} 個：{targets[0]} → {targets[-1]}\n")

        # 大盤法人（BFI82U 全市場），每進場日 total_net 正負
        mkt = {r.date: r.total_net for r in session.execute(
            select(models.InstitutionalMarketTotal)).scalars().all()}
        mkt_pos = {t: (mkt.get(t) or 0) > 0 for t in targets}

        stocks = {s.id: s for s in session.execute(select(models.Stock)).scalars().all()}
        stock_ids = sorted(sid for sid in stocks if not sid.startswith("00"))
        date_lo = min(targets) - timedelta(days=_WARMUP)

        rows_by_date = {t: [] for t in targets}
        # 時序擇時：每股蒐集 (是否翻買事件, hit) 與該股所有樣本 hit（算基準）
        ts_events = []  # (hit,) 當「翻買」事件發生
        ts_base = []    # 所有樣本 hit（同池）

        for sid, pdf, ind_g, inst_g, margin_g in _iter_stock_groups(session, stock_ids, date_lo):
            if ind_g is None or pdf is None:
                continue
            if inst_g is None:
                inst_g = pd.DataFrame(columns=_INST_COLS)
            pos = {d: i for i, d in enumerate(pdf["date"])}
            highs = pdf["high"].to_numpy(dtype=float)
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
                close_T = float(pdf["close"].iloc[p])
                atr_pct = float(atr14) / close_T if close_T else None
                ma_align = int(ma[0] > ma[1]) + int(ma[1] > ma[2]) + int(ma[2] > ma[3])
                vma = (float(vol_ma20) / 1000.0) if vol_ma20 and not pd.isna(vol_ma20) else 0.0

                fhi = highs[p + 1: p + 1 + _H]
                fhi = fhi[~np.isnan(fhi)]
                if len(fhi) == 0:
                    continue
                mfe = float(fhi.max()) / c0 - 1.0
                hit = 1 if mfe >= _POP_TARGET else 0

                # 法人序列（PIT）
                persist = trust_norm = consensus = accel = streak = flip = None
                if not inst_sorted.empty:
                    upto = inst_sorted[inst_sorted["date"] <= T]
                    if len(upto) >= 20:
                        f = upto["foreign_net"].fillna(0).to_numpy(dtype=float)
                        tr = upto["trust_net"].fillna(0).to_numpy(dtype=float)
                        ft = f + tr
                        last20 = ft[-20:]
                        persist = float((last20 > 0).mean())                      # A 買超天數比例
                        # B 投信獨立 + 共識
                        if vma > 0:
                            trust_norm = float(tr[-20:].sum()) / (vma * 20)
                        f20, t20 = f[-20:].sum(), tr[-20:].sum()
                        consensus = 1.0 if (f20 > 0 and t20 > 0) else (-1.0 if (f20 < 0 and t20 < 0) else 0.0)
                        # C 加速度：近5 vs 前15 佔量
                        if vma > 0:
                            accel = float(ft[-5:].sum()) / (vma * 5) - float(ft[-20:-5].sum()) / (vma * 15)
                        # 投信連續買超天數
                        s = 0
                        for v in tr[::-1]:
                            if v > 0:
                                s += 1
                            else:
                                break
                        streak = float(s)
                        # D 翻買事件：20日累計由負(前一日)轉正(當日)
                        cum_now = ft[-20:].sum()
                        cum_prev = ft[-21:-1].sum() if len(ft) >= 21 else None
                        if cum_prev is not None:
                            flip = (cum_prev <= 0 and cum_now > 0)

                rows_by_date[T].append({
                    "atr_pct": atr_pct, "ma_align": ma_align,
                    "persist": persist, "trust_norm": trust_norm, "consensus": consensus,
                    "accel": accel, "streak": streak, "hit": hit,
                })
                # 時序擇時（限多頭池，與推薦同池）
                if ma_align >= 2 and flip is not None:
                    ts_base.append(hit)
                    if flip:
                        ts_events.append(hit)

        labels = {
            "atr_pct": "波動度（對照）", "ma_align": "均線多排（對照）",
            "persist": "A 20日法人買超天數比例", "trust_norm": "B 投信20日佔量(獨立)",
            "consensus": "B 外資投信共識同向(+1/0/-1)", "accel": "C 法人買盤加速度",
            "streak": "A 投信連續買超天數",
        }
        factors = list(labels)

        # ── 橫截面 IC + 控制波動後增量（多頭池）──
        print("=" * 90)
        print("【會噴池 ma_align≥2】重構因子的 IC 與『控制波動後 高半-低半 命中差』")
        print("=" * 90)
        print(f"{'因子':<30}{'IC vs hit':>11}{'t值':>7}{'控波動後增量':>14}{'t值':>7}")
        print("-" * 90)
        for fc in factors:
            ics, diffs = [], []
            for t in targets:
                rs = [r for r in rows_by_date[t] if r["ma_align"] >= 2 and r.get(fc) is not None]
                if len(rs) < 50:
                    continue
                fv = np.array([r[fc] for r in rs], dtype=float)
                hv = np.array([r["hit"] for r in rs], dtype=float)
                ics.append(_spearman(fv, hv))
                # 控制波動：按 atr 切5組，組內高半-低半
                rs.sort(key=lambda r: r["atr_pct"])
                k = len(rs) // 5
                for qi in range(5):
                    grp = rs[qi * k:(qi + 1) * k] if qi < 4 else rs[qi * k:]
                    if len(grp) < 10:
                        continue
                    g2 = sorted(grp, key=lambda r: r[fc])
                    h = len(g2) // 2
                    diffs.append(np.mean([r["hit"] for r in g2[-h:]]) - np.mean([r["hit"] for r in g2[:h]]))
            m_ic, t_ic, _ = _tstat([x for x in ics if x is not None])
            m_d, t_d, _ = _tstat(diffs)
            print(f"{labels[fc]:<28}{m_ic:>11.4f}{t_ic:>7.2f}{m_d*100:>+12.1f}pp{t_d:>7.2f}")

        # ── D 時序擇時 event study ──
        print("\n" + "=" * 90)
        print("【D 時序擇時】法人20日累計『由賣轉買』當天進場（多頭池）")
        print("=" * 90)
        be = np.mean(ts_base) * 100 if ts_base else float("nan")
        ev = np.mean(ts_events) * 100 if ts_events else float("nan")
        print(f"  全池基準摸+10%率 {be:.1f}%（n={len(ts_base)}）")
        print(f"  翻買事件當天進場 {ev:.1f}%（n={len(ts_events)}）  →  擇時增量 {ev-be:+.1f}pp")

        # ── E 大盤 regime ──
        print("\n" + "=" * 90)
        print("【E 大盤法人 regime】用 BFI82U 全市場法人淨額把進場日切兩半（多頭池命中率）")
        print("=" * 90)
        for label, keep in (("大盤法人淨買日", True), ("大盤法人淨賣日", False)):
            hits, ns = [], 0
            for t in targets:
                if mkt_pos.get(t) != keep:
                    continue
                rs = [r for r in rows_by_date[t] if r["ma_align"] >= 2]
                if rs:
                    hits.append(np.mean([r["hit"] for r in rs]))
                    ns += len(rs)
            n_days = sum(1 for t in targets if mkt_pos.get(t) == keep)
            print(f"  {label}：{n_days} 個進場日、命中率 {np.mean(hits)*100:.1f}%" if hits else f"  {label}：無資料")
    finally:
        session.close()


if __name__ == "__main__":
    main()
