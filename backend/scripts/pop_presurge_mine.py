"""起漲點反推：+10% 行情「起漲前一刻」的狀態畫像 → 挖篩選條件 → 時序 OOS 驗證。

與 pop_reverse_conditions.py 的差異：那支把「未來20日會摸+10%」的每一天都算命中日，
連續命中日會重複計數、畫像被「已經在漲中」的日子污染。本腳本只取**起漲點**＝
當日構成命中而前一交易日不構成（該波行情的上車邊界），畫像才是「起漲前一刻」。

紀律（防事後諸葛）：
  表1 起漲點 vs 非命中日 特徵中位（全期，敘事用）。
  表2 只用前⅔時間(訓練段)逐特徵分桶挖 lift（挖掘段）。
  表3 訓練段挑高lift桶組 AND 條件 → 拿完全沒碰過的後⅓(測試段)驗 P(命中|條件)，
      與同測試段的現行清單/硬篩宇宙/全宇宙基準同場比。判生死只看表3。
命中口徑＝該日收盤起、未來20日最高再漲+10%（與 reverse 同）；全宇宙排除 ETF、量能≥50萬股。
用法：python scripts/pop_presurge_mine.py [n_dates] [sample]
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
_TGT = 0.10
_WARMUP = 200


def _pct_rank(v):
    return (pd.Series(v, dtype=float).rank(pct=True) * 100).to_numpy()


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    sample = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    s = SessionLocal()
    try:
        axis = s.execute(
            select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)
        ).scalars().all()
        targets = sorted(axis[: len(axis) - _H][::sample][-n:])
        date_lo = min(targets) - timedelta(days=_WARMUP)
        stocks = {st.id: st for st in s.execute(select(models.Stock)).scalars().all()}
        sids = sorted(sid for sid in stocks if not sid.startswith("00"))
        print(f"觀測日 {len(targets)}：{targets[0]} → {targets[-1]}，target 摸+10%/{_H}日")
        print("起漲點＝當日構成命中、前一交易日不構成（上車邊界）\n")

        rows = []
        for sid, pdf, ind_g, inst_g, margin_g in _iter_stock_groups(s, sids, date_lo):
            if ind_g is None or pdf is None:
                continue
            if inst_g is None:
                inst_g = pd.DataFrame(columns=_INST_COLS)
            pos_map = {d: i for i, d in enumerate(pdf["date"])}
            hi = pdf["high"].to_numpy(float)
            lo = pdf["low"].to_numpy(float)
            cl = pdf["close"].to_numpy(float)
            vo = pdf["volume"].to_numpy(float)
            with np.errstate(divide="ignore", invalid="ignore"):
                rng = (hi - lo) / np.where(cl > 0, cl, np.nan)
            ibd = {d: i for i, d in enumerate(ind_g["date"])}
            m20a = ind_g["ma20"].to_numpy(float)
            b60a = ind_g["bias_60"].to_numpy(float)
            b20a = ind_g["bias_20"].to_numpy(float)
            kda = ind_g["kd_k"].to_numpy(float)
            vma = ind_g["vol_ma20"].to_numpy(float)
            atra = ind_g["atr14"].to_numpy(float)
            ma5a = ind_g["ma5"].to_numpy(float)
            ma10a = ind_g["ma10"].to_numpy(float)
            ma60a = ind_g["ma60"].to_numpy(float)

            inst = inst_g.sort_values("date") if not inst_g.empty else inst_g
            if not inst.empty:
                idate = inst["date"].to_numpy()
                ft = (inst["foreign_net"].fillna(0) + inst["trust_net"].fillna(0)).to_numpy(float)
            mg = margin_g.sort_values("date") if (margin_g is not None and not margin_g.empty) else None
            if mg is not None:
                mdate = mg["date"].to_numpy()
                mb = mg["margin_balance"].to_numpy(float)
                sb = mg["short_balance"].to_numpy(float)

            # 全序列 forward-hit（供「前一日不構成」判定；p=0 無前一日不算起漲點）
            def fwd_hit(p: int) -> int | None:
                c = cl[p]
                if not c or c <= 0 or np.isnan(c):
                    return None
                fh = hi[p + 1:p + 1 + _H] / c - 1.0
                fh = fh[~np.isnan(fh)]
                if len(fh) == 0:
                    return None
                return 1 if fh.max() >= _TGT else 0

            def fwd_mae(p: int) -> float | None:
                c = cl[p]
                if not c or c <= 0 or np.isnan(c):
                    return None
                fl = lo[p + 1:p + 1 + _H] / c - 1.0
                fl = fl[~np.isnan(fl)]
                return float(fl.min()) if len(fl) else None

            for T in targets:
                p = pos_map.get(T)
                if p is None or p < _MIN_BARS - 1 or p + _H >= len(hi) or p < 40:
                    continue
                ii = ibd.get(T)
                if ii is None or ii < 5:
                    continue
                c0 = cl[p]
                atr14 = atra[ii]
                m20, m5, m10, m60 = m20a[ii], ma5a[ii], ma10a[ii], ma60a[ii]
                if (not c0 or c0 <= 0 or pd.isna(c0) or pd.isna(atr14)
                        or any(pd.isna(x) for x in (m5, m10, m20, m60))):
                    continue
                vm = vma[ii]
                if pd.isna(vm) or vm < 500000:
                    continue
                hit = fwd_hit(p)
                if hit is None:
                    continue
                prev = fwd_hit(p - 1)
                surge_start = bool(hit == 1 and prev == 0)

                atr_pct = float(atr14) / c0
                ma_align = int(m5 > m10) + int(m10 > m20) + int(m20 > m60)
                m20p, b60, b20, kd = m20a[ii - 5], b60a[ii], b20a[ii], kda[ii]
                above_rising = bool(c0 > m20 and m20 > m20p) if not (pd.isna(m20) or pd.isna(m20p)) else False
                near60 = bool(abs(b60) < 15) if not pd.isna(b60) else False

                volr = vo[p] / vm if vm > 0 else 0
                amp10 = np.nanmean(rng[p - 9:p + 1])
                amp40 = np.nanmean(rng[p - 39:p + 1])
                amp = amp10 / amp40 if amp40 and amp40 > 0 else 1
                hi20 = np.nanmax(hi[p - 19:p + 1])
                lo20 = np.nanmin(lo[p - 19:p + 1])
                pir = (c0 - lo20) / (hi20 - lo20) if hi20 > lo20 else 0.5
                ret5 = cl[p] / cl[p - 5] - 1 if cl[p - 5] > 0 else 0
                ret20 = cl[p] / cl[p - 20] - 1 if cl[p - 20] > 0 else 0

                netbuy = 0.0
                if not inst.empty:
                    j = int(np.searchsorted(idate, T, side="right"))
                    if j >= 20:
                        netbuy = float(ft[j - 20:j].sum())
                inst_ratio = netbuy / ((vm / 1000) * 20) if vm > 0 else 0

                srr = 0.0
                if mg is not None:
                    k = int(np.searchsorted(mdate, T, side="right")) - 1
                    if k >= 0 and not pd.isna(mb[k]) and mb[k] > 0 and not pd.isna(sb[k]):
                        srr = float(sb[k] / mb[k] * 100)

                rows.append(dict(
                    T=T, sid=sid, hit=hit, surge=surge_start, mae=fwd_mae(p),
                    atr_pct=atr_pct, ma_align=ma_align,
                    above_rising=above_rising, near60=near60,
                    volr=volr, amp=amp, pir=pir, ret5=ret5, ret20=ret20,
                    b20=(None if pd.isna(b20) else float(b20)),
                    b60=(None if pd.isna(b60) else float(b60)),
                    kd=(None if pd.isna(kd) else float(kd)),
                    inst_ratio=inst_ratio, srr=srr,
                ))

        df = pd.DataFrame(rows)
        n_all = len(df)
        n_surge = int(df["surge"].sum())
        base_hit = df["hit"].mean() * 100
        print(f"樣本 {n_all:,} 筆(股·日)，起漲點 {n_surge:,} 筆({n_surge/n_all*100:.1f}%)，"
              f"全宇宙命中率基準 {base_hit:.1f}%\n")

        cols = [("atr_pct", "日均波幅%", 100), ("ma_align", "均線多排 0~3", 1),
                ("volr", "量能/20日均量", 1), ("pir", "20日區間位階", 1),
                ("ret5", "近5日報酬%", 100), ("ret20", "近20日報酬%", 100),
                ("b20", "乖離月線%", 1), ("b60", "乖離季線%", 1), ("kd", "KD_K", 1),
                ("amp", "近10/40日振幅比", 1), ("inst_ratio", "法人20日淨買/量%", 100),
                ("srr", "券資比%", 1)]

        # ── 表1 起漲點畫像 ──
        print("=" * 78)
        print("【表1 起漲點 vs 非命中日 特徵中位（起漲前一刻的狀態）】")
        print("=" * 78)
        sg = df[df["surge"]]
        ns = df[df["hit"] == 0]
        print(f"{'特徵':<22}{'起漲點中位':>11}{'非命中中位':>12}{'差':>9}")
        print("-" * 78)
        for col, name, mult in cols:
            a, b = sg[col].median(), ns[col].median()
            if pd.isna(a) or pd.isna(b):
                continue
            print(f"{name:<22}{a * mult:>11.2f}{b * mult:>12.2f}{(a - b) * mult:>+9.2f}")
        ar = sg["above_rising"].mean() * 100
        nr = sg["near60"].mean() * 100
        print(f"\n起漲點中：站上上揚月線 {ar:.0f}%、|季線乖離|<15% {nr:.0f}%、"
              f"兩者皆過(現行硬篩) {(sg['above_rising'] & sg['near60']).mean()*100:.0f}%")

        # ── 訓練/測試切分（時序前⅔ / 後⅓）──
        udates = sorted(df["T"].unique())
        split = udates[len(udates) * 2 // 3]
        tr = df[df["T"] <= split]
        te = df[df["T"] > split]
        tr_base = tr["hit"].mean() * 100
        te_base = te["hit"].mean() * 100
        print(f"\n訓練段 ≤{split}（基準 {tr_base:.1f}%）｜測試段 >{split}（基準 {te_base:.1f}%）")

        # ── 表2 訓練段逐特徵分桶（挖掘）──
        print("\n" + "=" * 78)
        print("【表2 訓練段：逐特徵分桶 P(命中|條件) — 只在這裡挖，不看測試段】")
        print("=" * 78)
        buckets = {
            "atr_pct": ([0.02, 0.035, 0.05, 0.07], ["<2", "2~3.5", "3.5~5", "5~7", ">7"]),
            "ma_align": ([0.5, 1.5, 2.5], ["0", "1", "2", "3"]),
            "pir": ([0.3, 0.5, 0.7], ["<.3", ".3~.5", ".5~.7", ">.7"]),
            "volr": ([0.7, 1.0, 1.5, 2.5], ["<.7", ".7~1", "1~1.5", "1.5~2.5", ">2.5"]),
            "ret5": ([-0.03, 0, 0.03, 0.08], ["<-3", "-3~0", "0~3", "3~8", ">8"]),
            "b20": ([-3, 3, 8, 15], ["<-3", "-3~3", "3~8", "8~15", ">15"]),
            "kd": ([30, 50, 70, 85], ["<30", "30~50", "50~70", "70~85", ">85"]),
            "amp": ([0.8, 1.0, 1.3], ["<.8收斂", ".8~1", "1~1.3", ">1.3擴張"]),
            "inst_ratio": ([-0.02, 0, 0.02, 0.05], ["<-2", "-2~0", "0~2", "2~5", ">5"]),
            "srr": ([0.5, 1.5, 3, 6], ["<.5", ".5~1.5", "1.5~3", "3~6", ">6"]),
        }
        for col, (tiers, labels) in buckets.items():
            vals = tr[col].to_numpy(dtype=float)
            hits = tr["hit"].to_numpy()
            parts = []
            for lo_, hi_, lbl in zip([-np.inf] + tiers, tiers + [np.inf], labels):
                m = (vals > lo_) & (vals <= hi_) & ~np.isnan(vals)
                if m.sum() < 200:
                    continue
                parts.append(f"{lbl}:{hits[m].mean()*100:.0f}%")
            print(f"  {col:<12}" + "  ".join(parts))

        # ── 表3 AND 條件組合 → 測試段驗證 ──
        print("\n" + "=" * 78)
        print("【表3 條件組合 訓練段挖 → 測試段(沒碰過)驗 P(命中|條件)｜判生死只看這張】")
        print("=" * 78)
        combos = {
            "現行硬篩(對照)": lambda d: d["above_rising"] & d["near60"],
            "現行清單≈(硬篩+atr前20%)": None,  # 特殊處理
            "僅上揚月線(B對照)": lambda d: d["above_rising"],
            "高波動(atr>5%)": lambda d: d["atr_pct"] > 0.05,
            "高波動+全多排": lambda d: (d["atr_pct"] > 0.05) & (d["ma_align"] == 3),
            "高波動+上揚月線": lambda d: (d["atr_pct"] > 0.05) & d["above_rising"],
            "高波動+熱(乖離>8)": lambda d: (d["atr_pct"] > 0.05) & (d["b20"] > 8),
            "高波動+爆量(volr>2.5)": lambda d: (d["atr_pct"] > 0.05) & (d["volr"] > 2.5),
            "高波動+近5日強(>3%)": lambda d: (d["atr_pct"] > 0.05) & (d["ret5"] > 0.03),
            "極高波動(atr>7%)": lambda d: d["atr_pct"] > 0.07,
            "極高波動+上揚月線": lambda d: (d["atr_pct"] > 0.07) & d["above_rising"],
        }
        n_days_te = te["T"].nunique()
        # 三段(全期時序均分)供一致性檢查；每段列 命中/基準差
        fold_bounds = [udates[len(udates) * i // 3] for i in (1, 2)]
        fold_of = df["T"].map(lambda d: 0 if d <= fold_bounds[0] else (1 if d <= fold_bounds[1] else 2))
        fold_base = [df[fold_of == f]["hit"].mean() * 100 for f in range(3)]

        def _list_mask(d: pd.DataFrame) -> pd.Series:
            marks = []
            for _T, g in d.groupby("T"):
                pop = (2 * _pct_rank(g["atr_pct"]) + _pct_rank(g["ma_align"])) / 3.0
                marks.append(pd.Series(
                    g["above_rising"].to_numpy() & g["near60"].to_numpy() & (pop >= 80),
                    index=g.index))
            return pd.concat(marks) if marks else pd.Series(dtype=bool)

        print(f"{'條件':<26}{'訓練命中':>9}{'測試命中':>9}{'檔/日':>7}{'avgMAE':>8}"
              f"   三段 命中−基準")
        print("-" * 90)
        for name, pred in combos.items():
            mask = _list_mask(df) if pred is None else pred(df).fillna(False)
            sub = df[mask]
            sub_tr = tr[mask.reindex(tr.index, fill_value=False)]
            sub_te = te[mask.reindex(te.index, fill_value=False)]
            if len(sub_te) < 100:
                print(f"{name:<26}{'—':>9}  測試樣本不足")
                continue
            tr_hr = sub_tr["hit"].mean() * 100 if len(sub_tr) else float("nan")
            te_hr = sub_te["hit"].mean() * 100
            per_day = len(sub_te) / n_days_te
            mae = sub["mae"].mean() * 100
            segs = []
            for f in range(3):
                sf = sub[fold_of[mask] == f] if pred is not None else sub[fold_of.reindex(sub.index) == f]
                segs.append(f"{sf['hit'].mean()*100 - fold_base[f]:+.0f}pp" if len(sf) >= 50 else "—")
            print(f"{name:<26}{tr_hr:>8.1f}%{te_hr:>8.1f}%{per_day:>7.1f}{mae:>+7.1f}%"
                  f"   {'  '.join(segs)}")
        print(f"\n測試段基準 {te_base:.1f}%；三段基準 {fold_base[0]:.0f}/{fold_base[1]:.0f}/{fold_base[2]:.0f}%。"
              "\n判讀：測試 ≳ 訓練且明顯>基準、三段 命中−基準 同號同量級 → 條件真；avgMAE 看代價。")
    finally:
        s.close()


if __name__ == "__main__":
    main()
