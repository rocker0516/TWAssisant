"""爆發風格 × 大盤 regime 閘門：防禦期到底該不該空手（PIT，研究用，不寫DB）。

已定案的地基（[[project-market-regime-defense]]）：躲單日暴跌不可行；MA60 遲滯 2%
趨勢濾網對波段清單有效（MDD 砍半、三段穩）。本腳本量化同一閘門套在**爆發清單**上：

  閘門A（線上版）：持有中 收盤跌破大盤MA60逾2%→防禦；防禦中 站回MA60→持有
  閘門B（快版，探索性）：大盤 close>MA20 且 MA20>MA20(5日前)→持有，否則防禦
                （對稱個股硬篩的上揚月線；更快翻面、代價=更多假訊號）

對每個交易日的爆發成員（規則與線上一致）量隔天開/隔天高錨的摸+10%/30日與 MAE，
按閘門狀態分桶：全期、逐年、2022 熊市段、2026-06~07（本輪回檔）。
硬閘門損益：防禦期不進場 → 錯過的命中筆數 vs 躲掉的未命中/深回撤。
用法：python scripts/pop_explosive_regime.py [from_year]
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import func, select

sys.path.insert(0, __file__.replace("\\", "/").rsplit("/scripts/", 1)[0])

from app.engines.rules.wave import EXPLOSIVE_ATR_MIN  # noqa: E402
from app.storage import models  # noqa: E402
from app.storage.database import SessionLocal  # noqa: E402

_H = 30
_TGT = 0.10
_MIN_VOL = 500 * 1000
_GAP = 0.02


def _regime_states(rows: list[tuple], ma_n: int = 60) -> dict:
    """閘門A：MA60 遲滯（與 app.engines.market_regime 同規則）；回 {date: held}。"""
    closes = [c for _, c in rows]
    out = {}
    run = sum(closes[:ma_n])
    held = True
    for i in range(ma_n, len(rows)):
        run += closes[i] - closes[i - ma_n]
        ma = run / ma_n
        c = closes[i]
        held = (c > ma * (1 - _GAP)) if held else (c > ma)
        out[rows[i][0]] = held
    return out


def _regime_states_fast(rows: list[tuple]) -> dict:
    """閘門B：大盤站上上揚MA20（close>ma20 且 ma20>ma20[5日前]）。"""
    closes = pd.Series([c for _, c in rows], dtype=float)
    ma20 = closes.rolling(20).mean()
    out = {}
    for i in range(25, len(rows)):
        out[rows[i][0]] = bool(closes[i] > ma20[i] and ma20[i] > ma20[i - 5])
    return out


def main() -> None:
    y0 = int(sys.argv[1]) if len(sys.argv) > 1 else 2022
    lo = date(y0, 1, 1)
    s = SessionLocal()
    try:
        idx = s.execute(
            select(models.MarketIndex.date, models.MarketIndex.close)
            .where(models.MarketIndex.close.is_not(None))
            .order_by(models.MarketIndex.date)
        ).all()
        idx = [(d, float(c)) for d, c in idx]
        gateA = _regime_states(idx)
        gateB = _regime_states_fast(idx)

        etf = {r for r in s.execute(select(models.Stock.id).where(models.Stock.is_etf)).scalars()}
        first_bar = {sid: d for sid, d in s.execute(
            select(models.DailyPrice.stock_id, func.min(models.DailyPrice.date))
            .group_by(models.DailyPrice.stock_id)).all()}
        ev = pd.DataFrame(
            s.execute(select(models.Event.stock_id, models.Event.date).where(
                models.Event.category == "處置警示", models.Event.date >= lo - timedelta(days=15))).all(),
            columns=["sid", "date"])

        ind = pd.DataFrame(s.execute(
            select(models.Indicator.stock_id, models.Indicator.date, models.Indicator.ma20,
                   models.Indicator.atr14, models.Indicator.vol_ma20)
            .where(models.Indicator.date >= lo - timedelta(days=30))
            .order_by(models.Indicator.stock_id, models.Indicator.date)).all(),
            columns=["sid", "date", "ma20", "atr14", "vma"])
        ind["ma20_prev5"] = ind.groupby("sid")["ma20"].shift(5)

        px = pd.DataFrame(s.execute(
            select(models.DailyPrice.stock_id, models.DailyPrice.date, models.DailyPrice.open,
                   models.DailyPrice.high, models.DailyPrice.low, models.DailyPrice.close,
                   models.DailyPrice.volume)
            .where(models.DailyPrice.date >= lo - timedelta(days=30))
            .order_by(models.DailyPrice.stock_id, models.DailyPrice.date)).all(),
            columns=["sid", "date", "open", "high", "low", "close", "vol"])
        px_by_sid = {sid: g.reset_index(drop=True) for sid, g in px.groupby("sid", sort=False)}

        df = ind.merge(px[["sid", "date", "close", "vol"]], on=["sid", "date"], how="inner")
        df = df[(df["date"] >= lo) & df["date"].isin(gateA)]
        days = sorted(df["date"].unique())
        n_def = sum(1 for d in days if not gateA[d])
        print(f"進場日 {len(days)} 個：{days[0]} → {days[-1]}；閘門A防禦日 {n_def} 個"
              f"({n_def/len(days)*100:.0f}%)、閘門B防禦日 {sum(1 for d in days if not gateB.get(d, True))} 個\n")

        entries = []
        for T in days:
            g = df[df["date"] == T]
            for r in g.itertuples(index=False):
                if any(pd.isna(x) for x in (r.close, r.atr14, r.ma20, r.ma20_prev5)):
                    continue
                if r.close <= 0 or float(r.atr14) / float(r.close) <= EXPLOSIVE_ATR_MIN:
                    continue
                if not (r.close > r.ma20 and r.ma20 > r.ma20_prev5):
                    continue
                if (r.sid in etf or pd.isna(r.vma) or r.vma < _MIN_VOL
                        or first_bar.get(r.sid) is None or (T - first_bar[r.sid]).days < 90
                        or (not pd.isna(r.vol) and r.vol > r.vma * 6)):
                    continue
                if (not ev.empty) and ((ev["sid"] == r.sid) & (ev["date"] <= T)
                                       & (ev["date"] >= T - timedelta(days=15))).any():
                    continue
                gp = px_by_sid.get(r.sid)
                pos = gp.index[gp["date"] == T]
                if len(pos) == 0:
                    continue
                p = int(pos[0])
                if p + 1 + _H >= len(gp):  # 只取窗滿（誠實比較，不混「至今」）
                    continue
                e_open = gp["open"].iloc[p + 1]
                e_high = gp["high"].iloc[p + 1]
                fh = gp["high"].iloc[p + 2:p + 2 + _H].to_numpy(float)
                fl = gp["low"].iloc[p + 2:p + 2 + _H].to_numpy(float)
                fh, fl = fh[~np.isnan(fh)], fl[~np.isnan(fl)]
                if len(fh) < _H or pd.isna(e_open) or e_open <= 0 or pd.isna(e_high) or e_high <= 0:
                    continue
                entries.append({
                    "T": T, "y": T.year, "m": T.strftime("%Y-%m"),
                    "hold_a": gateA[T], "hold_b": gateB.get(T, True),
                    "hit": bool(fh.max() / e_open - 1 >= _TGT),
                    "hit_high": bool(fh.max() / e_high - 1 >= _TGT),
                    "mfe": fh.max() / e_open - 1,
                    "mae": fl.min() / e_open - 1,
                })
        E = pd.DataFrame(entries)
        print(f"爆發進場（窗滿）共 {len(E):,} 筆\n")

        def bucket(label: str, sub: pd.DataFrame) -> None:
            if len(sub) < 20:
                print(f"{label:<26}n<20 樣本不足")
                return
            print(f"{label:<26}{len(sub):>7,}{sub['hit'].mean()*100:>9.1f}%"
                  f"{sub['hit_high'].mean()*100:>9.1f}%{sub['mfe'].mean()*100:>+8.1f}%"
                  f"{sub['mae'].mean()*100:>+8.1f}%")

        for gate, col in (("閘門A(MA60遲滯,線上版)", "hold_a"), ("閘門B(上揚MA20,快版)", "hold_b")):
            print(f"══ {gate} ══")
            print(f"{'桶':<26}{'n':>7}{'隔天開命中':>9}{'隔天高':>9}{'avgMFE':>8}{'avgMAE':>8}")
            print("-" * 70)
            bucket("持有期進場", E[E[col]])
            bucket("防禦期進場(閘門要擋的)", E[~E[col]])
            for y in sorted(E["y"].unique()):
                Ey = E[E["y"] == y]
                bucket(f"  {y} 持有", Ey[Ey[col]])
                bucket(f"  {y} 防禦", Ey[~Ey[col]])
            sub67 = E[E["m"].isin(["2026-05", "2026-06"])]
            bucket("2026-05~06 持有", sub67[sub67[col]])
            bucket("2026-05~06 防禦", sub67[~sub67[col]])
            print()

        # 硬閘門總帳（閘門A）：防禦期全空手 → 躲掉什麼、錯過什麼
        d_ = E[~E["hold_a"]]
        print("── 硬閘門A總帳：防禦期空手會… ──")
        print(f"錯過 {len(d_):,} 筆進場，其中 {int(d_['hit'].sum()):,} 筆本來會命中"
              f"（{d_['hit'].mean()*100:.1f}%）；躲掉 {int((~d_['hit']).sum()):,} 筆未命中，"
              f"防禦期平均MAE {d_['mae'].mean()*100:+.1f}%（持有期 {E[E['hold_a']]['mae'].mean()*100:+.1f}%）")
    finally:
        s.close()


if __name__ == "__main__":
    main()
