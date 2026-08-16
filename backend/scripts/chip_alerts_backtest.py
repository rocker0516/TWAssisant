"""籌碼異動警示回測（PIT 事件研究，研究用，不寫 DB）。

背景：chip_alerts（c3cd85b）上線四種警示，其中「借券暴增」已由
chip_squeeze_daytrade_ic 檢驗（結論：空方領先、偏空配色正確）。
本腳本補齊其餘三種的事後檢驗：

  1. 投信首買：60 日內首度買超 ≥100 張（法人史 2020 起、可完整 walk-forward）
  2. 投信連買：連 ≥3 日且累計 ≥300 張（同上；只取「首次觸發日」去重）
  3. 大戶連增：集保大戶占比連 3 週升且累計 ≥+0.5pp（集保史僅 2025-06 起、
     短樣本單段報告，僅供方向參考）

方法：警示是盤後資訊 → 進場錨=隔天追高（c0=highs[p+1]），命中=之後 20 日
（p+2..p+21）最高 ≥ +10%（與會噴清單同口徑）；基準=同段全池（非 ETF、
20 日均量 ≥500 張，同警示的母體，不加多頭池濾網因警示本身不濾趨勢）；
另做 atr 五桶配對期望（會噴由波動主導，不配對會把「投信買高波動股」
誤認成訊號）。方向補充：20 日收盤報酬（警示 vs 全池）。
規則實作逐條對齊 flow_engine.chip_alerts（含流動性/ETF 排除）。

用法：.venv/bin/python scripts/chip_alerts_backtest.py [folds=3]
"""

from __future__ import annotations

import sys
from bisect import bisect_right
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select

sys.path.insert(0, __file__.replace("\\", "/").rsplit("/scripts/", 1)[0])

from app.engines.calibration import _iter_stock_groups  # noqa: E402
from app.storage import models  # noqa: E402
from app.storage.database import SessionLocal  # noqa: E402

_H = 20
_POP_TARGET = 0.10
_ATR_EDGES = [0.02, 0.035, 0.05, 0.07]  # 五桶：<2 / 2-3.5 / 3.5-5 / 5-7 / ≥7 %
_KINDS = ["trust_first_buy", "trust_streak", "big_up_weeks"]
_KIND_LABELS = {
    "trust_first_buy": "投信首買",
    "trust_streak": "投信連買(首次觸發)",
    "big_up_weeks": "大戶連增(首次觸發)",
}


def _bucket(atr_pct: float) -> int:
    for i, e in enumerate(_ATR_EDGES):
        if atr_pct < e:
            return i
    return len(_ATR_EDGES)


def main() -> None:
    folds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    session = SessionLocal()
    try:
        etf_ids = set(
            session.execute(select(models.Stock.id).where(models.Stock.is_etf.is_(True))).scalars().all()
        )
        stocks = session.execute(select(models.Stock.id)).scalars().all()
        stock_ids = sorted(sid for sid in stocks if sid not in etf_ids)

        # 集保週序列（全表小，直接載）
        hold_rows = session.execute(
            select(
                models.ShareholdingDistribution.stock_id,
                models.ShareholdingDistribution.date,
                models.ShareholdingDistribution.big_pct,
            ).order_by(models.ShareholdingDistribution.stock_id, models.ShareholdingDistribution.date)
        ).all()
        hold_by_sid: dict[str, list[tuple[date, float]]] = {}
        for sid, d, v in hold_rows:
            if v is not None:
                hold_by_sid.setdefault(sid, []).append((d, float(v)))

        date_lo = date(2020, 1, 2)
        # fold 邊界：以法人可回測範圍（暖身 60 交易日後）等分
        axis = sorted(
            session.execute(
                select(models.DailyPrice.date).distinct().order_by(models.DailyPrice.date)
            ).scalars().all()
        )
        eligible_axis = axis[60 : len(axis) - (_H + 1)]
        fold_bounds = [eligible_axis[i * len(eligible_axis) // folds] for i in range(folds)] + [
            eligible_axis[-1] + timedelta(days=1)
        ]
        def fold_of(d: date) -> int:
            fi = bisect_right(fold_bounds, d) - 1
            return min(max(fi, 0), folds - 1)
        spans = [f"{fold_bounds[i]}~{fold_bounds[i+1] - timedelta(days=1)}" for i in range(folds)]
        print(f"可回測進場日 {eligible_axis[0]} → {eligible_axis[-1]}，切 {folds} 段")
        for i, sp in enumerate(spans):
            print(f"  段{i+1}: {sp}")
        print()

        # 基準累積器：(fold, atr桶) → [hits, n, ret_sum, ret_n]
        base = np.zeros((folds, len(_ATR_EDGES) + 1, 4))
        events: list[dict] = []

        n_done = 0
        for sid, pdf, ind_g, inst_g, _mg in _iter_stock_groups(session, stock_ids, date_lo):
            n_done += 1
            if n_done % 400 == 0:
                print(f"  …{n_done}/{len(stock_ids)} 檔", flush=True)
            if ind_g is None or pdf is None or len(pdf) < 90:
                continue
            dates = list(pdf["date"])
            pos = {d: i for i, d in enumerate(dates)}
            highs = pdf["high"].to_numpy(dtype=float)
            closes = pdf["close"].to_numpy(dtype=float)
            vols = pdf["volume"].fillna(0).to_numpy(dtype=float) / 1000.0  # 張
            n = len(highs)
            vol20 = pd.Series(vols).rolling(20).mean().to_numpy()
            # fwd20max[p] = max(highs[p+2 .. p+21])；進場錨 c0 = highs[p+1]
            fmax = pd.Series(highs[::-1]).rolling(_H, min_periods=_H).max().to_numpy()[::-1]
            atr_pct = np.full(n, np.nan)
            for d, a in zip(ind_g["date"], ind_g["atr14"]):
                p = pos.get(d)
                if p is not None and a is not None and not pd.isna(a) and closes[p] > 0:
                    atr_pct[p] = float(a) / closes[p]

            def _metrics(p: int):
                """進場錨=隔天追高；回傳 (hit, ret20, bucket) 或 None（不可評）。"""
                if p + _H + 1 >= n or np.isnan(atr_pct[p]) or np.isnan(vol20[p]) or vol20[p] < 500:
                    return None
                c0 = highs[p + 1]
                if not c0 or np.isnan(c0) or c0 <= 0 or np.isnan(fmax[p + 2]):
                    return None
                hit = 1 if fmax[p + 2] / c0 - 1.0 >= _POP_TARGET else 0
                ret = closes[p + _H + 1] / closes[p + 1] - 1.0 if closes[p + 1] > 0 else np.nan
                return hit, ret, _bucket(atr_pct[p])

            # ── 基準：全池 stock-day ──
            for p in range(60, n - (_H + 1)):
                m = _metrics(p)
                if m is None:
                    continue
                hit, ret, bk = m
                fi = fold_of(dates[p])
                base[fi, bk, 0] += hit
                base[fi, bk, 1] += 1
                if not np.isnan(ret):
                    base[fi, bk, 2] += ret
                    base[fi, bk, 3] += 1

            # ── 投信首買 / 連買（規則同 chip_alerts，連買取首次觸發日）──
            if inst_g is not None and not inst_g.empty:
                tn = inst_g["trust_net"].to_numpy(dtype=float)
                tn0 = np.nan_to_num(tn, nan=0.0)
                idates = list(inst_g["date"])
                streak = np.zeros(len(tn0), dtype=int)
                cum = np.zeros(len(tn0))
                for i in range(len(tn0)):
                    if tn0[i] > 0:
                        streak[i] = streak[i - 1] + 1 if i else 1
                        cum[i] = cum[i - 1] + tn0[i] if streak[i] > 1 else tn0[i]
                trig = (streak >= 3) & (cum >= 300)
                for i, d in enumerate(idates):
                    p = pos.get(d)
                    if p is None:
                        continue
                    kind = None
                    if tn0[i] >= 100 and i >= 40 and np.all(tn0[max(0, i - 60):i] <= 0):
                        kind = "trust_first_buy"
                    elif trig[i] and not trig[i - 1]:
                        kind = "trust_streak"
                    if kind is None:
                        continue
                    m = _metrics(p)
                    if m is None:
                        continue
                    hit, ret, bk = m
                    events.append({"kind": kind, "fold": fold_of(d), "bucket": bk, "hit": hit, "ret": ret})

            # ── 大戶連增（週資料，嚴格連升 3 週且 ≥+0.5pp，首次觸發；
            #    集保記錄日=週五、進場=其後首個交易日之隔天追高）──
            hw = hold_by_sid.get(sid)
            if hw and len(hw) >= 4:
                bigs = [v for _, v in hw]
                for w in range(3, len(hw)):
                    l4 = bigs[w - 3 : w + 1]
                    ok = all(b > a for a, b in zip(l4, l4[1:])) and l4[-1] - l4[0] >= 0.5
                    if not ok:
                        continue
                    if w >= 4:  # 首次觸發去重
                        p4 = bigs[w - 4 : w]
                        if all(b > a for a, b in zip(p4, p4[1:])) and p4[-1] - p4[0] >= 0.5:
                            continue
                    rec_d = hw[w][0]
                    later = [q for q in range(n) if dates[q] >= rec_d]
                    if not later:
                        continue
                    p = later[0]  # 記錄日(或其後首個交易日)；_metrics 內以 p+1 追高進場
                    m = _metrics(p)
                    if m is None:
                        continue
                    hit, ret, bk = m
                    events.append({"kind": "big_up_weeks", "fold": fold_of(dates[p]), "bucket": bk, "hit": hit, "ret": ret})

        # ── 報表 ──
        print(f"\n事件總數：" + "、".join(f"{_KIND_LABELS[k]} {sum(1 for e in events if e['kind'] == k)}" for k in _KINDS))
        print("=" * 104)
        print("【事件 vs 全池】命中=隔天追高後 20 日最高 ≥+10%；配對=同段同 atr 桶期望（波動主導、必看配對欄）")
        print("=" * 104)
        print(f"{'警示':<22}{'段':>4}{'事件n':>7}{'事件命中':>9}{'全池':>8}{'配對期望':>9}{'Δ配對':>8}{'z':>6}{'事件20日報酬':>12}{'全池報酬':>9}")
        print("-" * 104)
        for kind in _KINDS:
            for fi in range(folds):
                ev = [e for e in events if e["kind"] == kind and e["fold"] == fi]
                pool_h, pool_n = base[fi, :, 0].sum(), base[fi, :, 1].sum()
                if not pool_n:
                    continue
                if len(ev) < 5:
                    print(f"{_KIND_LABELS[kind]:<22}{fi+1:>4}{len(ev):>7}{'—（樣本不足）':>18}")
                    continue
                ph = pool_h / pool_n
                eh = np.mean([e["hit"] for e in ev])
                # 配對期望：每事件用其 (fold, atr桶) 的全池命中率
                exp_ps = []
                for e in ev:
                    b_h, b_n = base[fi, e["bucket"], 0], base[fi, e["bucket"], 1]
                    exp_ps.append(b_h / b_n if b_n else ph)
                exp = float(np.mean(exp_ps))
                var = sum(p_ * (1 - p_) for p_ in exp_ps)
                z = (sum(e["hit"] for e in ev) - sum(exp_ps)) / np.sqrt(var) if var > 0 else float("nan")
                er = np.nanmean([e["ret"] for e in ev])
                pr = base[fi, :, 2].sum() / base[fi, :, 3].sum() if base[fi, :, 3].sum() else float("nan")
                print(
                    f"{_KIND_LABELS[kind]:<22}{fi+1:>4}{len(ev):>7}{eh*100:>8.1f}%{ph*100:>7.1f}%"
                    f"{exp*100:>8.1f}%{(eh-exp)*100:>+7.1f}pp{z:>6.1f}{er*100:>+11.1f}%{pr*100:>+8.1f}%"
                )
        # 大戶連增樣本全落在近一年，另給合併行
        ev = [e for e in events if e["kind"] == "big_up_weeks"]
        if ev:
            eh = np.mean([e["hit"] for e in ev])
            exp_ps = []
            for e in ev:
                b_h, b_n = base[e["fold"], e["bucket"], 0], base[e["fold"], e["bucket"], 1]
                exp_ps.append(b_h / b_n if b_n else np.nan)
            exp = float(np.nanmean(exp_ps))
            var = np.nansum([p_ * (1 - p_) for p_ in exp_ps])
            z = (sum(e["hit"] for e in ev) - np.nansum(exp_ps)) / np.sqrt(var) if var > 0 else float("nan")
            er = np.nanmean([e["ret"] for e in ev])
            print("-" * 104)
            print(f"{'大戶連增(全期合併)':<22}{'—':>4}{len(ev):>7}{eh*100:>8.1f}%{'':>8}{exp*100:>8.1f}%{(eh-exp)*100:>+7.1f}pp{z:>6.1f}{er*100:>+11.1f}%")
            print("  ⚠️ 集保史僅 2025-06 起（單一年、含 2026-07 崩盤段），只供方向參考、不足以下定論")

        print("\n判讀：Δ配對>0 且逐段同號、z 穩定 → 警示有事後資訊量（值得往評分/排序想）；")
        print("Δ配對≈0 → 警示只是「發生了值得看」的儀表、無方向宣稱（維持現狀即正確）；")
        print("Δ配對<0 → 警示反向（如借券暴增=空方領先），配色/文案應偏警戒。純歷史統計、非投資建議。")
    finally:
        session.close()


if __name__ == "__main__":
    main()
