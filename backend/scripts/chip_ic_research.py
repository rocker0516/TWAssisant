"""籌碼因子 vs「會噴」單因子預測力回測（point-in-time，研究用，不寫入 DB）。

問題：法人/融資券/大戶等籌碼因子，對「未來 20 交易日內摸到 +10%」(會噴 MFE) 有沒有
橫截面預測力？用與 PoppabilityEfficacyEngine 完全相同的 MFE 定義與 _iter_stock_groups
載入器（PIT 安全），對每個籌碼因子算：
  1. 每日橫截面 rank-IC（因子 rank vs 會噴 hit / 連續 MFE 的 Spearman），跨日平均 + t 值
  2. 因子五分位的「摸 +10%」命中率（Q1 最低 → Q5 最高），看單調性與頭尾差
同時在「會噴宇宙」(均線多排≥2，近似線上硬篩的多頭池) 內再測一次——這才是推薦真正
運作的池子，回答「在已篩出的會噴候選裡，籌碼能不能再分出贏家」。
對照組：atr_pct(波動)、ma_align(均線多排)——線上會噴分數真正在用的兩個因子。

用法：python scripts/chip_ic_research.py            # 預設窗
      python scripts/chip_ic_research.py 150 4     # 150 個進場日、每 4 交易日取樣
"""

from __future__ import annotations

import sys
from datetime import timedelta

import numpy as np
import pandas as pd
from sqlalchemy import distinct, select

# 允許從 backend/ 直接執行
sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])

from app.engines.calibration import _INST_COLS, _MIN_BARS, _iter_stock_groups  # noqa: E402
from app.storage import models  # noqa: E402
from app.storage.database import SessionLocal  # noqa: E402

_H = 20
_POP_TARGET = 0.10
_WARMUP = 160


def _spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    """Spearman rank 相關（= rank 後的 Pearson）。"""
    if len(a) < 8:
        return None
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def _tstat(vals: list[float]) -> tuple[float, float, int]:
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
        eligible = axis[: len(axis) - _H]                       # 必須有 ≥H 個未來交易日
        targets = sorted(eligible[::sample][-n_dates:])
        if not targets:
            print("資料不足")
            return
        print(f"進場日 {len(targets)} 個：{targets[0]} → {targets[-1]}（每 {sample} 交易日取樣）\n")

        stocks = {s.id: s for s in session.execute(select(models.Stock)).scalars().all()}
        # ETF 排除（會噴宇宙慣例）：代號 00 開頭
        stock_ids = sorted(sid for sid in stocks if not sid.startswith("00"))
        date_lo = min(targets) - timedelta(days=_WARMUP)
        tset = set(targets)

        # 每個進場日累積每檔的 {因子們 + outcome}
        rows_by_date: dict = {t: [] for t in targets}

        for sid, pdf, ind_g, inst_g, margin_g in _iter_stock_groups(session, stock_ids, date_lo):
            if ind_g is None or pdf is None:
                continue
            if inst_g is None:
                inst_g = pd.DataFrame(columns=_INST_COLS)
            pos = {d: i for i, d in enumerate(pdf["date"])}
            highs = pdf["high"].to_numpy(dtype=float)
            closes = pdf["close"].to_numpy(dtype=float)
            ind_by_date = {d: i for i, d in enumerate(ind_g["date"])}
            inst_dates = inst_g["date"].to_numpy() if not inst_g.empty else np.array([])
            mg = margin_g if margin_g is not None and not margin_g.empty else None
            mg_dates = mg["date"].to_numpy() if mg is not None else np.array([])

            for T in targets:
                p = pos.get(T)
                if p is None or p < _MIN_BARS - 1 or p + _H >= len(closes):
                    continue
                ii = ind_by_date.get(T)
                if ii is None:
                    continue
                ind = ind_g.iloc[ii]
                close_T = closes[p]
                atr14 = ind.get("atr14")
                if not close_T or close_T <= 0 or atr14 is None or pd.isna(atr14):
                    continue
                atr_pct = float(atr14) / close_T
                ma = [ind.get(c) for c in ("ma5", "ma10", "ma20", "ma60")]
                if any(m is None or pd.isna(m) for m in ma):
                    continue
                ma_align = int(ma[0] > ma[1]) + int(ma[1] > ma[2]) + int(ma[2] > ma[3])

                vol_ma20 = ind.get("vol_ma20")
                vma_lots = (float(vol_ma20) / 1000.0) if vol_ma20 and not pd.isna(vol_ma20) else 0.0

                # ── 籌碼因子（PIT：只用 date ≤ T 的列）──
                chip5 = chip20 = foreign20 = None
                if len(inst_dates):
                    upto = inst_g[inst_g["date"] <= T]
                    if len(upto):
                        f = upto["foreign_net"].fillna(0).to_numpy(dtype=float)
                        tr = upto["trust_net"].fillna(0).to_numpy(dtype=float)
                        ft = f + tr
                        foreign20 = float(f[-20:].sum())  # 外資 20 日累計（張，絕對）
                        if vma_lots > 0:
                            chip5 = float(ft[-5:].sum()) / (vma_lots * 5)    # 近 5 日法人買超佔量
                            chip20 = float(ft[-20:].sum()) / (vma_lots * 20)  # 近 20 日法人買超佔量

                margin_chg = short_ratio = None
                if mg is not None and len(mg_dates):
                    mu = mg[mg["date"] <= T]
                    mb = mu["margin_balance"].dropna()
                    if len(mb) >= 2:
                        ref = mb.iloc[-(_H + 1)] if len(mb) > _H else mb.iloc[0]
                        if ref:
                            margin_chg = float((mb.iloc[-1] - ref) / ref * 100.0)
                    sb = mu["short_balance"].dropna()
                    if len(mb) and len(sb) and mb.iloc[-1]:
                        short_ratio = float(sb.iloc[-1] / mb.iloc[-1] * 100.0)

                # ── outcome：保守進場=當天最高價，看之後 H 根 MFE ──
                c0 = highs[p]
                fhi = highs[p + 1 : p + 1 + _H]
                fhi = fhi[~np.isnan(fhi)]
                if not c0 or c0 <= 0 or len(fhi) == 0:
                    continue
                mfe = float(fhi.max()) / c0 - 1.0
                hit = 1 if mfe >= _POP_TARGET else 0

                rows_by_date[T].append({
                    "atr_pct": atr_pct, "ma_align": ma_align,
                    "chip5": chip5, "chip20": chip20, "foreign20": foreign20,
                    "margin_chg": margin_chg, "short_ratio": short_ratio,
                    "mfe": mfe, "hit": hit,
                })

        factors = ["atr_pct", "ma_align", "chip5", "chip20", "foreign20", "margin_chg", "short_ratio"]
        labels = {
            "atr_pct": "波動度 ATR%（對照/線上在用）",
            "ma_align": "均線多排 0-3（對照/線上在用）",
            "chip5": "法人近5日買超佔量（ChipScore近）",
            "chip20": "法人近20日買超佔量（ChipScore累）",
            "foreign20": "外資20日累計買超(張,絕對)",
            "margin_chg": "融資20日變化%（升=鬆動,反向）",
            "short_ratio": "券資比%（高=軋空潛力）",
        }

        def analyze(universe_filter, title: str) -> None:
            print("=" * 78)
            print(title)
            print("=" * 78)
            # 各日該宇宙的基礎 hit 率
            base_hits, base_ns = [], []
            for t in targets:
                rs = [r for r in rows_by_date[t] if universe_filter(r)]
                if rs:
                    base_ns.append(len(rs))
                    base_hits.append(np.mean([r["hit"] for r in rs]))
            print(f"宇宙：平均每日 {np.mean(base_ns):.0f} 檔，整體摸+10%率 {np.mean(base_hits)*100:.1f}%\n")
            print(f"{'因子':<30}{'IC vs hit':>12}{'t值':>8}{'IC vs MFE':>12}{'Q1命中%':>9}{'Q5命中%':>9}{'Q5-Q1':>8}")
            print("-" * 88)
            for fc in factors:
                ic_hit, ic_mfe = [], []
                q1, q5 = [], []  # 各日最低五分位 / 最高五分位的 hit 率
                for t in targets:
                    rs = [r for r in rows_by_date[t] if universe_filter(r) and r[fc] is not None]
                    if len(rs) < 20:
                        continue
                    fv = np.array([r[fc] for r in rs], dtype=float)
                    hv = np.array([r["hit"] for r in rs], dtype=float)
                    mv = np.array([r["mfe"] for r in rs], dtype=float)
                    ic_hit.append(_spearman(fv, hv))
                    ic_mfe.append(_spearman(fv, mv))
                    order = np.argsort(fv)
                    k = max(1, len(rs) // 5)
                    q1.append(hv[order[:k]].mean())
                    q5.append(hv[order[-k:]].mean())
                m_hit, t_hit, n = _tstat([x for x in ic_hit if x is not None])
                m_mfe, _, _ = _tstat([x for x in ic_mfe if x is not None])
                q1m = np.mean(q1) * 100 if q1 else float("nan")
                q5m = np.mean(q5) * 100 if q5 else float("nan")
                print(f"{labels[fc]:<28}{m_hit:>12.4f}{t_hit:>8.2f}{m_mfe:>12.4f}"
                      f"{q1m:>9.1f}{q5m:>9.1f}{q5m-q1m:>8.1f}")
            print()

        analyze(lambda r: True, "【全市場宇宙】所有可評分個股（排除 ETF）")
        analyze(lambda r: r["ma_align"] >= 2, "【會噴池近似】均線多排≥2 的多頭候選（推薦真正運作的池子）")

        print("說明：IC 為每日橫截面 Spearman rank-IC 的跨日平均；|t|>2 才算統計上顯著。")
        print("Q5-Q1 = 因子最高20%組 vs 最低20%組的摸+10%命中率差（正且大=高因子值更會噴）。\n")

        # ── 增量價值把關：控制波動度後，籌碼因子還剩多少？──
        # 每日先把多頭池按 atr_pct 切 5 組，組內再按候選因子切高/低半，比命中率差。
        # 若控制波動後差距坍縮 → 該因子只是波動的影子（共線），無新資訊。
        def double_sort(fc: str) -> None:
            within_diffs = []  # 各 atr 組內：高半 - 低半 的 hit 差
            hi_hits, lo_hits = [], []
            for t in targets:
                rs = [r for r in rows_by_date[t]
                      if r["ma_align"] >= 2 and r[fc] is not None]
                if len(rs) < 50:
                    continue
                rs.sort(key=lambda r: r["atr_pct"])
                k = len(rs) // 5
                for qi in range(5):
                    grp = rs[qi * k:(qi + 1) * k] if qi < 4 else rs[qi * k:]
                    if len(grp) < 10:
                        continue
                    grp2 = sorted(grp, key=lambda r: r[fc])
                    half = len(grp2) // 2
                    lo = np.mean([r["hit"] for r in grp2[:half]])
                    hi = np.mean([r["hit"] for r in grp2[-half:]])
                    lo_hits.append(lo)
                    hi_hits.append(hi)
                    within_diffs.append(hi - lo)
            if within_diffs:
                m, t_, n = _tstat(within_diffs)
                print(f"  {labels[fc]:<30} 控制波動後 高半-低半命中差 = "
                      f"{m*100:+5.1f}pp (t={t_:5.2f}, 低半{np.mean(lo_hits)*100:.1f}%→高半{np.mean(hi_hits)*100:.1f}%)")

        print("=" * 78)
        print("【增量把關】在多頭池內、先按波動度分5組，組內再看籌碼因子的高半vs低半命中差")
        print("（控制波動後仍有正向差距=真有獨立資訊；坍縮到~0=只是波動的影子）")
        print("=" * 78)
        for fc in ("short_ratio", "margin_chg", "chip20", "chip5", "foreign20", "ma_align"):
            double_sort(fc)
    finally:
        session.close()


if __name__ == "__main__":
    main()
