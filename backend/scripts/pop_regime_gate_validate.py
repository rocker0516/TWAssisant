"""regime 閘門正確性驗證（輕量，吃 pop_regime_gate.py 的 date-level CSV）。

閘門會直接決定「今天出不出手」，上線前要過三關：
  1. 參數敏感度：MA {40,60,120} × 遲滯帶 {1%,2%,3%} 九宮格 —— 若只有 MA60+2%
     有效、鄰格全翻面，就是過擬合；真訊號應該整片同號。
  2. 日層級顯著性：同日個股命中高度相關，個股層級 n 是假的 —— 以「每個進場日
     的清單命中率」為一個觀測做 Welch t。
  3. circular-shift 安慰劑：把 held/defense 狀態序列在時間軸上整段平移 K 次，
     每次算「持有-防禦」命中率差 —— 保留 regime 的自相關結構但打斷與市場的
     對齊。觀察差距若沒贏過平移分布，閘門就只是巧合。
另附：防禦段落長度分布 + 「事後看其實正常」的防禦日比例（誠實文案用）。

前置：先跑 REGIME_GATE_CSV=xxx.csv python scripts/pop_regime_gate.py
用法：python scripts/pop_regime_gate_validate.py <csv> [n_shifts]
"""
from __future__ import annotations

import csv
import sys
from datetime import date as _date

import numpy as np
from sqlalchemy import select

sys.path.insert(0, __file__.replace("\\", "/").rsplit("/scripts/", 1)[0])
from app.storage import models  # noqa: E402
from app.storage.database import SessionLocal  # noqa: E402


def _held_series(closes: np.ndarray, ma_n: int, gap: float) -> np.ndarray:
    """MA 遲滯狀態機：跌破 MA×(1-gap) 出、站回 MA 進（與 market_regime.py 同規則）。"""
    n = len(closes)
    ma = np.full(n, np.nan)
    if n >= ma_n:
        c = np.cumsum(closes)
        ma[ma_n - 1:] = (c[ma_n - 1:] - np.concatenate(([0.0], c[:-ma_n]))) / ma_n
    held = np.ones(n, dtype=bool)
    h = True
    for i in range(n):
        if not np.isnan(ma[i]):
            h = (closes[i] > ma[i] * (1.0 - gap)) if h else (closes[i] > ma[i])
        held[i] = h
    return held


def _welch_t(a: np.ndarray, b: np.ndarray) -> float:
    va, vb = a.var(ddof=1), b.var(ddof=1)
    se = np.sqrt(va / len(a) + vb / len(b))
    return float((a.mean() - b.mean()) / se) if se > 0 else float("nan")


def main() -> None:
    csv_path = sys.argv[1]
    n_shifts = int(sys.argv[2]) if len(sys.argv) > 2 else 2000

    # date-level 清單結果（與 regime 定義無關，可重複套不同閘門）
    day_n: dict = {}
    day_hits: dict = {}
    with open(csv_path) as fh:
        for r in csv.DictReader(fh):
            d = _date.fromisoformat(r["date"])
            day_n[d] = int(r["n"])
            day_hits[d] = int(r["hits"])
    sel_dates = sorted(d for d in day_n if day_n[d] > 0)
    frac = {d: day_hits[d] / day_n[d] for d in sel_dates}

    session = SessionLocal()
    try:
        mkt = session.execute(
            select(models.MarketIndex.date, models.MarketIndex.close)
            .order_by(models.MarketIndex.date)
        ).all()
    finally:
        session.close()
    mdates = [r[0] for r in mkt]
    mclose = np.array([float(r[1]) for r in mkt])
    mpos = {d: i for i, d in enumerate(mdates)}
    sel_idx = np.array([mpos[d] for d in sel_dates if d in mpos])
    sel_dates = [d for d in sel_dates if d in mpos]
    frac_arr = np.array([frac[d] for d in sel_dates])
    n_arr = np.array([day_n[d] for d in sel_dates], dtype=float)
    hits_arr = np.array([day_hits[d] for d in sel_dates], dtype=float)
    print(f"date-level 樣本：{len(sel_dates)} 個進場日 "
          f"{sel_dates[0]} → {sel_dates[-1]}，共 {int(n_arr.sum())} 筆個股觀測\n")

    # ── 1. 參數敏感度九宮格 ──
    print("── 1. 參數敏感度（日層級：持有均命中 vs 防禦均命中，diff=持有−防禦 pp）──")
    print(f"{'':>10} " + " ".join(f"{f'gap={g:.0%}':>26}" for g in (0.01, 0.02, 0.03)))
    for ma_n in (40, 60, 120):
        cells = []
        for gap in (0.01, 0.02, 0.03):
            held = _held_series(mclose, ma_n, gap)[sel_idx]
            a, b = frac_arr[held], frac_arr[~held]
            if len(b) < 5:
                cells.append(f"{'防禦日<5':>26}")
                continue
            t = _welch_t(a, b)
            cells.append(f"{a.mean()*100:5.1f}/{b.mean()*100:5.1f} "
                         f"диff={(a.mean()-b.mean())*100:+4.1f} t={t:4.1f}"
                         .replace("диff", "diff"))
        print(f"{f'MA{ma_n}':>10} " + " ".join(cells))

    # ── 2. 基準組合（MA60, 2%）日層級 + 個股層級對照 ──
    held60 = _held_series(mclose, 60, 0.02)
    hsel = held60[sel_idx]
    a, b = frac_arr[hsel], frac_arr[~hsel]
    pooled_a = hits_arr[hsel].sum() / n_arr[hsel].sum()
    pooled_b = hits_arr[~hsel].sum() / n_arr[~hsel].sum()
    obs_diff = a.mean() - b.mean()
    print(f"\n── 2. 基準 MA60+2%：日層級 持有 {a.mean()*100:.1f}% ({len(a)}日) vs "
          f"防禦 {b.mean()*100:.1f}% ({len(b)}日)，diff={obs_diff*100:+.1f}pp，"
          f"Welch t={_welch_t(a, b):.2f}")
    print(f"   個股層級(參考) 持有 {pooled_a*100:.1f}% vs 防禦 {pooled_b*100:.1f}%")

    # ── 3. circular-shift 安慰劑 ──
    rng = np.random.default_rng(42)
    n_m = len(mclose)
    diffs = []
    for _ in range(n_shifts):
        k = int(rng.integers(30, n_m - 30))
        hs = np.roll(held60, k)[sel_idx]
        if hs.sum() < 5 or (~hs).sum() < 5:
            continue
        diffs.append(frac_arr[hs].mean() - frac_arr[~hs].mean())
    diffs = np.array(diffs)
    p = float((diffs >= obs_diff).mean())
    print(f"\n── 3. circular-shift 安慰劑（{len(diffs)} 次平移）──")
    print(f"   隨機閘門 diff 分布：mean={diffs.mean()*100:+.1f}pp  "
          f"p5={np.percentile(diffs,5)*100:+.1f}  p95={np.percentile(diffs,95)*100:+.1f}")
    print(f"   觀察 diff={obs_diff*100:+.1f}pp → 單尾 p={p:.3f} "
          f"（p<0.05 = 贏過 95% 的隨機平移閘門）")

    # ── 4. 防禦段落 + 誠實文案素材 ──
    spells = []
    run = 0
    for h in held60:
        if not h:
            run += 1
        elif run:
            spells.append(run)
            run = 0
    if run:
        spells.append(run)
    share = (~held60).mean() * 100
    overall = frac_arr.mean()
    fine = (frac_arr[~hsel] >= overall).mean() * 100 if (~hsel).sum() else float("nan")
    print(f"\n── 4. 防禦狀態描述（全歷史 {mdates[0]} → {mdates[-1]}）──")
    print(f"   防禦日占比={share:.1f}%  段落數={len(spells)}  "
          f"段長 中位={np.median(spells):.0f}日 / p90={np.percentile(spells,90):.0f}日 / "
          f"最長={max(spells) if spells else 0}日")
    print(f"   防禦日中事後命中率≥全期均值({overall*100:.1f}%)的比例={fine:.0f}% "
          f"（閘門=保守偏誤而非預知，文案不可誇大）")


if __name__ == "__main__":
    main()
