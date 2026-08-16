"""借券/當沖 IC 研究（PIT walk-forward，研究用，不寫 DB）。

背景：chip_ic_research 實測籌碼最強的是軋空軸（券資比 +4.9pp）但只用了融券；
借券賣出（外資主要放空管道）2026-07-30 才接入。本腳本驗證三個問題：

  1. 軋空軸補完借券後是否更強：舊(融券/均量) vs 新(融券+借券/均量) vs 純借券
  2. 借券 20 日增減（空方加碼動能）有無獨立資訊
  3. 當沖比率（浮額指標）高低對「摸 +10%」命中率的影響（假說：高浮額壓命中）
  4. 事件研究：「借券暴增」警示規則（單日 ≥1000 張且 ≥ 近月均日變動 3 倍）
     事後命中率 vs 全池基準 —— 直接檢驗 chip_alerts 上線規則有無資訊量

方法與 chip_ic_walkforward 一致可對照：hit=進場日高點追高、20 日內最高 ≥ +10%；
控制波動（atr 五分箱內高半-低半差）；時序切 folds 段看 OOS 維持性。
多頭池限定（ma_align ≥ 2，對齊會噴清單的實際情境）。

需先跑完 scripts/backfill_flow_sources（2020 全歷史）才有足夠樣本。
用法：python scripts/chip_squeeze_daytrade_ic.py [n_dates=260] [sample=5] [folds=3]
"""

from __future__ import annotations

import sys
from datetime import timedelta

import numpy as np
import pandas as pd
from sqlalchemy import distinct, select

sys.path.insert(0, __file__.replace("\\", "/").rsplit("/scripts/", 1)[0])

from app.engines.calibration import _MIN_BARS, _iter_stock_groups  # noqa: E402
from app.storage import models  # noqa: E402
from app.storage.database import SessionLocal  # noqa: E402

_H = 20
_POP_TARGET = 0.10
_WARMUP = 160

LABELS = {
    "squeeze_old": "軋空舊(融券/20日均量)",
    "squeeze_new": "軋空新(融券+借券/均量)",
    "sbl_level": "借券餘額/20日均量",
    "sbl_mom": "借券20日增減/均量",
    "dt_ratio": "當沖占比(近5日)",
}


def _load_table(session, model, cols: list) -> dict[str, pd.DataFrame]:
    """整表載入（窄表，全史約 2~3M 列可承受）→ per-stock 升冪 DataFrame。"""
    rows = session.execute(
        select(*(getattr(model, c) for c in ("stock_id", "date", *cols)))
        .order_by(model.stock_id, model.date)
    ).all()
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=["stock_id", "date", *cols])
    return {sid: g.reset_index(drop=True) for sid, g in df.groupby("stock_id", sort=False)}


def _ctrl_vol_diff(rows, fc):
    """控制波動：atr 五分箱內 fc 高半-低半 hit 差（多頭池）。"""
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
    folds = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    session = SessionLocal()
    try:
        axis = session.execute(
            select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)
        ).scalars().all()
        # 目標日受限於借券資料涵蓋範圍（回補未完成時樣本自動縮小）
        sbl_min = session.execute(select(models.ShortLending.date).order_by(models.ShortLending.date).limit(1)).scalar()
        if sbl_min is None:
            raise SystemExit("short_lending 無資料，先跑 scripts/backfill_flow_sources")
        eligible = [d for d in axis[: len(axis) - _H] if d >= sbl_min + timedelta(days=40)]
        targets = sorted(eligible[::sample][-n_dates:])
        if len(targets) < folds * 10:
            print(f"⚠️ 可用進場日僅 {len(targets)} 個（借券史自 {sbl_min}），統計力不足，建議回補完再跑")
        print(f"進場日 {len(targets)} 個：{targets[0]} → {targets[-1]}，切 {folds} 段（借券史自 {sbl_min}）\n")

        stocks = {s.id: s for s in session.execute(select(models.Stock)).scalars().all()}
        stock_ids = sorted(sid for sid in stocks if not sid.startswith("00"))
        date_lo = min(targets) - timedelta(days=_WARMUP)

        print("載入借券/當沖全表…", flush=True)
        sbl_by_sid = _load_table(session, models.ShortLending, ["sbl_balance", "sbl_change"])
        dt_by_sid = _load_table(session, models.DayTrading, ["dt_volume"])
        print(f"借券 {len(sbl_by_sid)} 檔、當沖 {len(dt_by_sid)} 檔\n", flush=True)

        rows_by_date = {t: [] for t in targets}
        for sid, pdf, ind_g, inst_g, margin_g in _iter_stock_groups(session, stock_ids, date_lo):
            if ind_g is None or pdf is None:
                continue
            pos = {d: i for i, d in enumerate(pdf["date"])}
            highs = pdf["high"].to_numpy(dtype=float)
            closes = pdf["close"].to_numpy(dtype=float)
            vols = pdf["volume"].fillna(0).to_numpy(dtype=float) / 1000.0  # 張
            ind_by_date = {d: i for i, d in enumerate(ind_g["date"])}
            mg = margin_g.sort_values("date") if margin_g is not None and not margin_g.empty else None
            sg = sbl_by_sid.get(sid)
            dg = dt_by_sid.get(sid)
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
                if len(fhi) == 0 or vma <= 0:
                    continue
                hit = 1 if (float(fhi.max()) / c0 - 1.0) >= _POP_TARGET else 0

                # 融券（margin 表）
                short_bal = None
                if mg is not None:
                    m_upto = mg[mg["date"] <= T]
                    if not m_upto.empty:
                        v = m_upto.iloc[-1].get("short_balance")
                        short_bal = float(v) if v is not None and not pd.isna(v) else None
                # 借券（PIT：只取 ≤T；要求近 40 天內至少 21 筆才算動能）
                sbl_bal = sbl_mom = None
                sbl_spike = None
                if sg is not None:
                    s_upto = sg[sg["date"] <= T]
                    if not s_upto.empty and (T - s_upto.iloc[-1]["date"]).days <= 7:
                        v = s_upto.iloc[-1]["sbl_balance"]
                        sbl_bal = float(v) if v is not None and not pd.isna(v) else None
                        if len(s_upto) >= 21:
                            tail = s_upto.tail(21)
                            b_now = tail.iloc[-1]["sbl_balance"]
                            b_ref = tail.iloc[0]["sbl_balance"]
                            if b_now is not None and b_ref is not None:
                                sbl_mom = (float(b_now) - float(b_ref)) / (vma * 20)
                            # 借券暴增事件（同 chip_alerts 規則）
                            chg_now = tail.iloc[-1]["sbl_change"]
                            prev_abs = tail.iloc[:-1]["sbl_change"].abs().dropna()
                            if chg_now is not None and not pd.isna(chg_now) and len(prev_abs) >= 10:
                                base = max(float(prev_abs.mean()), 100.0)
                                sbl_spike = bool(float(chg_now) >= 1000 and float(chg_now) >= 3 * base)
                # 當沖近 5 日占比（PIT）
                dt_ratio = None
                if dg is not None:
                    d_upto = dg[dg["date"] <= T].tail(5)
                    if len(d_upto) >= 3:
                        dt_sum = float(d_upto["dt_volume"].fillna(0).sum())
                        v5 = vols[max(0, p - 4): p + 1].sum()
                        if v5 > 0:
                            dt_ratio = dt_sum / v5

                rows_by_date[T].append({
                    "atr_pct": atr_pct, "ma_align": ma_align, "hit": hit,
                    "squeeze_old": (short_bal / (vma * 20)) if short_bal is not None else None,
                    "squeeze_new": ((short_bal or 0.0) + sbl_bal) / (vma * 20) if sbl_bal is not None else None,
                    "sbl_level": (sbl_bal / (vma * 20)) if sbl_bal is not None else None,
                    "sbl_mom": sbl_mom,
                    "dt_ratio": dt_ratio,
                    "sbl_spike": sbl_spike,
                })

        fold_dates = [targets[i * len(targets) // folds:(i + 1) * len(targets) // folds] for i in range(folds)]

        # ── A. 控制波動增量，逐 fold ──
        print("=" * 96)
        print("【控制波動後 高半-低半 命中差(pp)】多頭池；看 OOS 段是否維持、軋空新舊排名")
        print("=" * 96)
        print(f"{'因子':<26}" + "".join(f"{('段'+str(i+1)):>16}" for i in range(folds)))
        for fc in LABELS:
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
            print(f"{LABELS[fc]:<24}" + "".join(f"{c:>16}" for c in cells))

        # ── B. 當沖分五組命中率（浮額假說：單調遞減？）──
        print("\n" + "=" * 96)
        print("【當沖占比五分組 命中率】多頭池、全期合併；假說=高浮額壓命中（看單調性）")
        print("=" * 96)
        pool = [r for d in targets for r in rows_by_date[d] if r["ma_align"] >= 2 and r["dt_ratio"] is not None]
        if len(pool) >= 100:
            pool.sort(key=lambda r: r["dt_ratio"])
            k = len(pool) // 5
            for qi in range(5):
                grp = pool[qi * k:(qi + 1) * k] if qi < 4 else pool[qi * k:]
                lo, hi = grp[0]["dt_ratio"] * 100, grp[-1]["dt_ratio"] * 100
                print(f"  Q{qi+1}（當沖 {lo:.0f}%~{hi:.0f}%）：命中 {np.mean([r['hit'] for r in grp])*100:.1f}%（n={len(grp)}）")
        else:
            print(f"  樣本不足（{len(pool)}）")

        # ── C. 借券暴增事件研究，逐 fold（檢驗 chip_alerts 規則）──
        print("\n" + "=" * 96)
        print("【借券暴增事件】事後 20 日命中率 vs 同段全池（多頭池）；上線警示規則的直接檢驗")
        print("=" * 96)
        print(f"{'段(期間)':<30}{'全池基準':>10}{'暴增事件':>10}{'增量':>9}{'事件n':>8}{'z值':>7}")
        print("-" * 96)
        for fi in range(folds):
            base, ev = [], []
            for d in fold_dates[fi]:
                for r in rows_by_date[d]:
                    if r["ma_align"] >= 2 and r["sbl_spike"] is not None:
                        base.append(r["hit"])
                        if r["sbl_spike"]:
                            ev.append(r["hit"])
            if not base or not ev:
                print(f"段{fi+1}：事件樣本不足")
                continue
            pb, pe, ne = np.mean(base), np.mean(ev), len(ev)
            z = (pe - pb) / np.sqrt(pb * (1 - pb) / ne) if pb not in (0, 1) else float("nan")
            span = f"{fold_dates[fi][0]}~{fold_dates[fi][-1]}"
            print(f"{('段'+str(fi+1)+' '+span):<30}{pb*100:>9.1f}%{pe*100:>9.1f}%"
                  f"{(pe-pb)*100:>+8.1f}pp{ne:>8}{z:>7.2f}")

        print("\n判讀：軋空新 vs 舊看「各段增量是否更大且排名穩定」；當沖看五分組單調性；")
        print("暴增事件正增量=軋空燃料、負增量=空方資訊領先，兩者都是有用結論（決定警示怎麼標色）。")
        print("與 chip_ic_walkforward 同法可直接對照（該版軋空舊軸約 +4.9pp）。純歷史統計、非投資建議。")
    finally:
        session.close()


if __name__ == "__main__":
    main()
