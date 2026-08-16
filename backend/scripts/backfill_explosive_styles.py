"""回填歷史 Score 列的 passed_styles（爆發風格標記）。

爆發風格 2026-07-23 上線（wave.explosive_ok），舊評分日的 passed_styles=NULL →
回看月曆/清單查不到歷史成員。本腳本對每個 wave 評分日重算爆發標記（與
tracks.py/WaveTrack.evaluate 同一規則）並覆寫 passed_styles，冪等可重跑：

  common：非ETF、vol_ma20≥50萬股、掛牌≥60根、當日量≤6×均量、近15日無處置警示
  explosive：atr14/close > EXPLOSIVE_ATR_MIN 且 close>ma20 且 ma20>ma20(5根前)

用法：python scripts/backfill_explosive_styles.py [--dry-run]
"""
from __future__ import annotations

import sys
from datetime import timedelta

import pandas as pd
from sqlalchemy import select, update

sys.path.insert(0, __file__.replace("\\", "/").rsplit("/scripts/", 1)[0])

from app.engines.rules.wave import EXPLOSIVE_ATR_MIN  # noqa: E402
from app.storage import models  # noqa: E402
from app.storage.database import SessionLocal  # noqa: E402

_MIN_VOL = 500 * 1000
_MIN_BARS = 60


def main() -> None:
    dry = "--dry-run" in sys.argv
    s = SessionLocal()
    try:
        score_dates = s.execute(
            select(models.Score.date).where(models.Score.track == "wave")
            .group_by(models.Score.date).order_by(models.Score.date)
        ).scalars().all()
        if not score_dates:
            print("無 wave 評分日")
            return
        lo = min(score_dates)
        print(f"評分日 {len(score_dates)} 個：{lo} → {max(score_dates)}")

        etf_ids = {r for r in s.execute(select(models.Stock.id).where(models.Stock.is_etf)).scalars()}

        # 指標：需 ma20 前 5 根 → 多抓 30 日曆天暖身
        ind_rows = s.execute(
            select(models.Indicator.stock_id, models.Indicator.date, models.Indicator.ma20,
                   models.Indicator.atr14, models.Indicator.vol_ma20)
            .where(models.Indicator.date >= lo - timedelta(days=30))
            .order_by(models.Indicator.stock_id, models.Indicator.date)
        ).all()
        ind = pd.DataFrame(ind_rows, columns=["sid", "date", "ma20", "atr14", "vma"])
        ind["ma20_prev5"] = ind.groupby("sid")["ma20"].shift(5)

        px_rows = s.execute(
            select(models.DailyPrice.stock_id, models.DailyPrice.date,
                   models.DailyPrice.close, models.DailyPrice.volume)
            .where(models.DailyPrice.date >= lo - timedelta(days=30))
        ).all()
        px = pd.DataFrame(px_rows, columns=["sid", "date", "close", "vol"])

        # 掛牌長度：n_bars≥60 用「首根價格日距評分日 ≥90 日曆天」保守近似
        # （逐檔逐日累計根數太重；90 天 ≈ 60 交易日，誤差只在掛牌滿兩三個月的新股邊緣）
        from sqlalchemy import func as _f
        first_bar = {
            sid: d for sid, d in s.execute(
                select(models.DailyPrice.stock_id, _f.min(models.DailyPrice.date))
                .group_by(models.DailyPrice.stock_id)
            ).all()
        }

        # 處置警示事件（回看窗 15 日）
        ev_rows = s.execute(
            select(models.Event.stock_id, models.Event.date)
            .where(models.Event.category == "處置警示", models.Event.date >= lo - timedelta(days=15))
        ).all()
        ev = pd.DataFrame(ev_rows, columns=["sid", "date"]) if ev_rows else pd.DataFrame(columns=["sid", "date"])

        df = ind.merge(px, on=["sid", "date"], how="inner")
        total_upd = total_exp = 0
        for d in score_dates:
            g = df[df["date"] == d]
            if g.empty:
                continue
            disposed = set(ev[(ev["date"] <= d) & (ev["date"] >= d - timedelta(days=15))]["sid"]) if not ev.empty else set()
            flags: dict[str, list[str]] = {}
            for r in g.itertuples(index=False):
                ok_common = (
                    r.sid not in etf_ids
                    and r.vma is not None and not pd.isna(r.vma) and r.vma >= _MIN_VOL
                    and first_bar.get(r.sid) is not None and (d - first_bar[r.sid]).days >= 90
                    and (r.vol is None or pd.isna(r.vol) or pd.isna(r.vma) or r.vol <= r.vma * 6)
                    and r.sid not in disposed
                )
                explosive = (
                    ok_common
                    and r.atr14 is not None and not pd.isna(r.atr14)
                    and r.close is not None and not pd.isna(r.close) and r.close > 0
                    and float(r.atr14) / float(r.close) > EXPLOSIVE_ATR_MIN
                    and r.ma20 is not None and not pd.isna(r.ma20) and r.close > r.ma20
                    and r.ma20_prev5 is not None and not pd.isna(r.ma20_prev5) and r.ma20 > r.ma20_prev5
                )
                flags[r.sid] = ["explosive"] if explosive else []
            n_exp = sum(1 for v in flags.values() if v)
            total_exp += n_exp
            if not dry:
                # 逐日兩批 UPDATE（有標記/無標記），避免逐列 round-trip
                exp_ids = [sid for sid, v in flags.items() if v]
                empty_ids = [sid for sid, v in flags.items() if not v]
                if exp_ids:
                    s.execute(update(models.Score).where(
                        models.Score.track == "wave", models.Score.date == d,
                        models.Score.stock_id.in_(exp_ids),
                    ).values(passed_styles=["explosive"]))
                if empty_ids:
                    s.execute(update(models.Score).where(
                        models.Score.track == "wave", models.Score.date == d,
                        models.Score.stock_id.in_(empty_ids),
                    ).values(passed_styles=[]))
            total_upd += len(flags)
            print(f"{d}: 爆發 {n_exp} 檔 / 更新 {len(flags)} 列")
        if not dry:
            s.commit()
        print(f"\n{'DRY-RUN ' if dry else ''}完成：共更新 {total_upd} 列、爆發標記 {total_exp} 筆")
    finally:
        s.close()


if __name__ == "__main__":
    main()
