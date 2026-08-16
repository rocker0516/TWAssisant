"""爆發風格 2026 年逐日回測（每個交易日、非週抽樣；研究用，不寫DB）。

成員規則與線上 tracks.py 完全一致：
  common：非ETF、vol_ma20≥50萬股、掛牌首根距今≥90日曆天、當日量≤6×均量、近15日無處置
  爆發：atr14/close>7% 且 close>ma20 且 ma20>ma20(5根前)
對照組＝線上會噴清單：common+上揚月線+|季線乖離|<15% 且 (2·rank(atr)+rank(align))/3≥80（全市場rank）。

錨點＝隔天開盤(一般)/隔天最高(保守追高)，摸+10%只看隔天之後 30 根。
窗口未滿 30 根的近期進場日納入但標「未滿」（命中率只會低估，之後只增不減）。
用法：python scripts/pop_explosive_ytd.py [year]
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
from app.engines.scoring import _pct_ranks  # noqa: E402
from app.storage import models  # noqa: E402
from app.storage.database import SessionLocal  # noqa: E402

_H = 30
_TGT = 0.10
_MIN_VOL = 500 * 1000


def main() -> None:
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
    lo = date(year, 1, 1)
    s = SessionLocal()
    try:
        etf = {r for r in s.execute(select(models.Stock.id).where(models.Stock.is_etf)).scalars()}
        first_bar = {sid: d for sid, d in s.execute(
            select(models.DailyPrice.stock_id, func.min(models.DailyPrice.date))
            .group_by(models.DailyPrice.stock_id)).all()}
        ev = pd.DataFrame(
            s.execute(select(models.Event.stock_id, models.Event.date).where(
                models.Event.category == "處置警示", models.Event.date >= lo - timedelta(days=15))).all(),
            columns=["sid", "date"])

        ind = pd.DataFrame(s.execute(
            select(models.Indicator.stock_id, models.Indicator.date, models.Indicator.ma5,
                   models.Indicator.ma10, models.Indicator.ma20, models.Indicator.ma60,
                   models.Indicator.atr14, models.Indicator.vol_ma20, models.Indicator.bias_60)
            .where(models.Indicator.date >= lo - timedelta(days=30))
            .order_by(models.Indicator.stock_id, models.Indicator.date)).all(),
            columns=["sid", "date", "ma5", "ma10", "ma20", "ma60", "atr14", "vma", "b60"])
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
        df = df[df["date"] >= lo]
        days = sorted(df["date"].unique())
        print(f"{year} 年交易日 {len(days)} 個：{days[0]} → {days[-1]}（資料至 {px['date'].max()}）\n")

        def outcome(sid: str, T) -> dict | None:
            g = px_by_sid.get(sid)
            if g is None:
                return None
            pos = g.index[g["date"] == T]
            if len(pos) == 0:
                return None
            p = int(pos[0])
            if p + 1 >= len(g):
                return None  # 尚無隔天
            e_open, e_high = g["open"].iloc[p + 1], g["high"].iloc[p + 1]
            fh = g["high"].iloc[p + 2:p + 2 + _H].to_numpy(float)
            fl = g["low"].iloc[p + 2:p + 2 + _H].to_numpy(float)
            fh, fl = fh[~np.isnan(fh)], fl[~np.isnan(fl)]
            if len(fh) == 0 or pd.isna(e_open) or pd.isna(e_high) or e_open <= 0 or e_high <= 0:
                return None
            return {
                "hit_open": bool(fh.max() / e_open - 1 >= _TGT),
                "hit_high": bool(fh.max() / e_high - 1 >= _TGT),
                "mfe": fh.max() / e_open - 1,
                "mae": fl.min() / e_open - 1,
                "complete": len(fh) >= _H,
            }

        agg = {"exp": defaultdict(list), "pop": defaultdict(list)}
        for T in days:
            g = df[df["date"] == T]
            cands = []
            for r in g.itertuples(index=False):
                if any(pd.isna(x) for x in (r.close, r.atr14, r.ma5, r.ma10, r.ma20, r.ma60)):
                    continue
                if r.close <= 0:
                    continue
                common = (
                    r.sid not in etf
                    and not pd.isna(r.vma) and r.vma >= _MIN_VOL
                    and first_bar.get(r.sid) is not None and (T - first_bar[r.sid]).days >= 90
                    and (pd.isna(r.vol) or r.vol <= r.vma * 6)
                    and not ((not ev.empty) and ((ev["sid"] == r.sid) & (ev["date"] <= T)
                             & (ev["date"] >= T - timedelta(days=15))).any())
                )
                if not common:
                    continue
                above_rising = (not pd.isna(r.ma20_prev5)) and r.close > r.ma20 and r.ma20 > r.ma20_prev5
                cands.append({
                    "sid": r.sid,
                    "atr_pct": float(r.atr14) / float(r.close),
                    "align": int(r.ma5 > r.ma10) + int(r.ma10 > r.ma20) + int(r.ma20 > r.ma60),
                    "above_rising": above_rising,
                    "near60": (not pd.isna(r.b60)) and abs(r.b60) < 15,
                })
            if not cands:
                continue
            ar = _pct_ranks([c["atr_pct"] for c in cands])
            lr = _pct_ranks([c["align"] for c in cands])
            for c, a, l in zip(cands, ar, lr):
                c["popscore"] = (2 * a + l) / 3 * 100 if (a is not None and l is not None) else None
                c["exp"] = c["above_rising"] and c["atr_pct"] > EXPLOSIVE_ATR_MIN
                c["pop"] = (c["above_rising"] and c["near60"]
                            and c["popscore"] is not None and c["popscore"] >= 80)
            for key in ("exp", "pop"):
                for c in cands:
                    if not c[key]:
                        continue
                    o = outcome(c["sid"], T)
                    if o is not None:
                        agg[key][T.strftime("%Y-%m")].append(o)

        def table(key: str, label: str) -> None:
            print(f"── {label} ──")
            print(f"{'月':<9}{'進場筆數':>8}{'隔天開命中':>10}{'隔天高命中':>10}{'avgMFE':>9}{'avgMAE':>9}{'窗滿':>7}")
            print("-" * 62)
            tot: list[dict] = []
            for m in sorted(agg[key]):
                rs = agg[key][m]
                tot += rs
                comp = np.mean([r["complete"] for r in rs])
                mark = "" if comp > 0.99 else f" {comp*100:.0f}%*"
                print(f"{m:<10}{len(rs):>8,}{np.mean([r['hit_open'] for r in rs])*100:>9.1f}%"
                      f"{np.mean([r['hit_high'] for r in rs])*100:>9.1f}%"
                      f"{np.mean([r['mfe'] for r in rs])*100:>+8.1f}%"
                      f"{np.mean([r['mae'] for r in rs])*100:>+8.1f}%{mark:>7}")
            full = [r for r in tot if r["complete"]]
            print("-" * 62)
            print(f"{'全年':<9}{len(tot):>8,}{np.mean([r['hit_open'] for r in tot])*100:>9.1f}%"
                  f"{np.mean([r['hit_high'] for r in tot])*100:>9.1f}%"
                  f"{np.mean([r['mfe'] for r in tot])*100:>+8.1f}%"
                  f"{np.mean([r['mae'] for r in tot])*100:>+8.1f}%")
            if full and len(full) < len(tot):
                print(f"{'窗滿30日':<8}{len(full):>8,}{np.mean([r['hit_open'] for r in full])*100:>9.1f}%"
                      f"{np.mean([r['hit_high'] for r in full])*100:>9.1f}%"
                      f"{np.mean([r['mfe'] for r in full])*100:>+8.1f}%"
                      f"{np.mean([r['mae'] for r in full])*100:>+8.1f}%")
            print()

        table("exp", f"爆發（atr>{EXPLOSIVE_ATR_MIN*100:.0f}%＋上揚月線）")
        table("pop", "會噴清單（硬篩＋前20%，對照）")
        print("*＝該月部分進場日 30 日窗未走完（命中率為「至今」、只會低估）")
    finally:
        s.close()


if __name__ == "__main__":
    main()
