"""回填歷史 wave Score 列：四因子總分 + 新風格標記（2026-07-28 標籤制上線）。

上線前的評分日仍是舊二因子總分、passed_styles 只有(或沒有)explosive 標記 →
回看月曆/清單與新制不一致。本腳本對每個 wave 評分日重算：
  total_score = (2×rank(atr_pct)+rank(ma_align)+rank(pos_52w)+rank(pb))/5×100
                （與 scoring.finalize_wave_pop 同式；pos/pb 缺值中性 0.5）
  passed      = 既存 passed_filter(遲滯狀態，不動) 且 total ≥ 100−top_pct
  passed_styles = explosive / strong / story / crash（crash 含當日大盤 bias60≤−2.3 市場閘）
common 篩近似與 backfill_explosive_styles.py 相同：非ETF、vol_ma20≥50萬股、
掛牌≥90日曆天、當日量≤6×均量、近15日無處置警示。冪等可重跑。

用法：python scripts/backfill_rank_and_styles.py [--dry-run]
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, __file__.replace("\\", "/").rsplit("/scripts/", 1)[0])
from app.engines.rules.wave import (  # noqa: E402
    CRASH_ATR_MIN, CRASH_MKT_BIAS60, CRASH_PX_MIN, CRASH_TURNOVER_MIN,
    EXPLOSIVE_ATR_MIN,
    STORY_ATR_MIN, STORY_PB_MIN, STORY_PE_MIN, STRONG_OVER_MA20, STRONG_POS_MIN,
)

_DB = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0] + "/data/twa.db"
_MIN_VOL = 500 * 1000


def _pct_rank(s: pd.Series) -> pd.Series:
    return s.rank(pct=True)


def main() -> None:
    dry = "--dry-run" in sys.argv
    con = sqlite3.connect(_DB, timeout=60)
    cur = con.cursor()

    score_dates = [r[0] for r in cur.execute(
        "SELECT DISTINCT date FROM scores WHERE track='wave' ORDER BY date")]
    if not score_dates:
        print("無 wave 評分日")
        return
    lo, hi = score_dates[0], score_dates[-1]
    print(f"評分日 {len(score_dates)} 個：{lo} → {hi}")

    row = cur.execute("SELECT value FROM settings WHERE key='scoring'").fetchone()
    top_pct = 20.0
    if row:
        try:
            top_pct = float((json.loads(row[0]).get("wave") or {}).get("top_pct", 20.0))
        except Exception:  # noqa: BLE001
            pass
    cutoff = 100.0 - top_pct
    print(f"cutoff = {cutoff}（top_pct={top_pct}）")

    lo_hist = (pd.Timestamp(lo) - timedelta(days=420)).strftime("%Y-%m-%d")  # pos_52w 需 240 根
    px = pd.read_sql_query(
        f"SELECT stock_id sid, date, high, low, close, volume vol FROM daily_prices "
        f"WHERE date>='{lo_hist}' ORDER BY stock_id, date", con)
    g = px.groupby("sid", sort=False)
    px["hi240"] = g["high"].transform(lambda s: s.rolling(240, 60).max())
    px["lo240"] = g["low"].transform(lambda s: s.rolling(240, 60).min())
    px["pos_52w"] = np.where(px["hi240"] > px["lo240"],
                             (px["close"] - px["lo240"]) / (px["hi240"] - px["lo240"]), np.nan)

    ind = pd.read_sql_query(
        f"SELECT stock_id sid, date, ma5, ma10, ma20, ma60, atr14, vol_ma20 vma FROM indicators "
        f"WHERE date>='{(pd.Timestamp(lo) - timedelta(days=30)).strftime('%Y-%m-%d')}' "
        f"ORDER BY stock_id, date", con)
    ind["ma20_prev5"] = ind.groupby("sid", sort=False)["ma20"].shift(5)

    val = pd.read_sql_query(
        f"SELECT stock_id sid, date, pe, pb FROM valuation "
        f"WHERE date>='{(pd.Timestamp(lo) - timedelta(days=30)).strftime('%Y-%m-%d')}' "
        f"ORDER BY stock_id, date", con)

    etf = {r[0] for r in cur.execute("SELECT id FROM stocks WHERE is_etf=1")}
    first_bar = dict(cur.execute("SELECT stock_id, min(date) FROM daily_prices GROUP BY stock_id"))
    ev = pd.read_sql_query(
        f"SELECT stock_id sid, date FROM events WHERE category='處置警示' "
        f"AND date>='{(pd.Timestamp(lo) - timedelta(days=15)).strftime('%Y-%m-%d')}'", con)

    # 乾淨池（同 scoring._apply_crash_style）：注意近 5 個交易日、處置近 10 個交易日不掛 crash
    att = pd.read_sql_query(
        f"SELECT stock_id sid, date, kind FROM attention_listings "
        f"WHERE date>='{(pd.Timestamp(lo) - timedelta(days=30)).strftime('%Y-%m-%d')}'", con)
    all_days = [r[0] for r in cur.execute("SELECT date FROM market_index ORDER BY date")]
    _day_pos = {d: i for i, d in enumerate(all_days)}
    dirty: dict[str, set] = {}
    for sid, d0, kind in att.itertuples(index=False):
        p0 = _day_pos.get(d0)
        if p0 is None:
            continue
        for k in range(5 if kind == "notice" else 10):
            if p0 + k < len(all_days):
                dirty.setdefault(all_days[p0 + k], set()).add(sid)

    mkt = pd.read_sql_query("SELECT date, close FROM market_index ORDER BY date", con)
    mkt["bias60"] = (mkt["close"] / mkt["close"].rolling(60).mean() - 1.0) * 100
    mkt_bias = dict(zip(mkt["date"], mkt["bias60"]))

    df = px[["sid", "date", "close", "vol", "pos_52w"]].merge(
        ind, on=["sid", "date"], how="inner")
    # pb/pe：合併後逐檔 ffill（引擎語意=最近一筆 ≤ 當日）
    df = df.merge(val, on=["sid", "date"], how="left")
    df = df.sort_values(["sid", "date"])
    df[["pe", "pb"]] = df.groupby("sid", sort=False)[["pe", "pb"]].ffill()
    df = df[df["date"].isin(set(score_dates))]

    total_rows = 0
    for d in score_dates:
        gday = df[df["date"] == d].copy()
        if gday.empty:
            continue
        sids_scored = {r[0] for r in cur.execute(
            "SELECT stock_id FROM scores WHERE track='wave' AND date=?", (d,))}
        gday = gday[gday["sid"].isin(sids_scored)]
        if gday.empty:
            continue
        disposed = set(ev[(ev["date"] <= d)
                          & (ev["date"] >= (pd.Timestamp(d) - timedelta(days=15)).strftime("%Y-%m-%d"))]["sid"])

        # ── 四因子 rank（與 finalize_wave_pop 同式；rank 範圍=當日全部 scored 股）──
        gday["atr_pct"] = gday["atr14"] / gday["close"]
        gday["ma_align"] = ((gday["ma5"] > gday["ma10"]).astype(float)
                            + (gday["ma10"] > gday["ma20"]).astype(float)
                            + (gday["ma20"] > gday["ma60"]).astype(float))
        core_ok = gday["atr_pct"].notna() & gday[["ma5", "ma10", "ma20", "ma60"]].notna().all(axis=1)
        ra = _pct_rank(gday.loc[core_ok, "atr_pct"])
        rl = _pct_rank(gday.loc[core_ok, "ma_align"])
        rp = _pct_rank(gday.loc[core_ok, "pos_52w"]).fillna(0.5)
        rb = _pct_rank(gday.loc[core_ok, "pb"]).fillna(0.5)
        comp = (2.0 * ra + rl + rp + rb) / 5.0
        total = (_pct_rank(comp) * 100.0).round(2)  # 合成再重排名 → 真百分位（與線上同式）

        # ── 風格 ──
        c = gday["close"]
        common = (~gday["sid"].isin(etf)
                  & gday["vma"].notna() & (gday["vma"] >= _MIN_VOL)
                  & gday["sid"].map(lambda s_: first_bar.get(s_) is not None
                                    and (pd.Timestamp(d) - pd.Timestamp(first_bar[s_])).days >= 90)
                  & (gday["vol"].isna() | (gday["vol"] <= gday["vma"] * 6))
                  & ~gday["sid"].isin(disposed))
        above_rising = (c > gday["ma20"]) & gday["ma20_prev5"].notna() & (gday["ma20"] > gday["ma20_prev5"])
        explosive = common & (gday["atr_pct"] > EXPLOSIVE_ATR_MIN) & above_rising
        strong = (common & gday["pos_52w"].notna() & (gday["pos_52w"] > STRONG_POS_MIN)
                  & gday["ma20"].notna() & (c / gday["ma20"] - 1.0 > STRONG_OVER_MA20))
        story = (common & (gday["pb"] > STORY_PB_MIN) & (gday["pe"] > STORY_PE_MIN)
                 & (gday["atr_pct"] > STORY_ATR_MIN))
        deep = (mkt_bias.get(d) is not None and not pd.isna(mkt_bias.get(d))
                and mkt_bias[d] <= CRASH_MKT_BIAS60)
        # crash 2026-08-24 改版（wave.crash_cand_ok 同式）：去掉 (strong|story) 閘、
        # ATR 6%→9%、加流動篩。這裡不再借 strong/story 的 common，要自己併上。
        crash = ((common & (gday["atr_pct"] > CRASH_ATR_MIN) & (c >= CRASH_PX_MIN)
                  & gday["vol"].notna() & (c * gday["vol"] >= CRASH_TURNOVER_MIN)
                  & ~gday["sid"].isin(dirty.get(d, ())))
                 if deep else pd.Series(False, index=gday.index))

        styles = pd.DataFrame({"explosive": explosive.fillna(False), "strong": strong.fillna(False),
                               "story": story.fillna(False), "crash": crash.fillna(False)})
        updates = []
        for i, r in gday.iterrows():
            st = [name for name in ("explosive", "strong", "story", "crash") if styles.at[i, name]]
            t = float(total.at[i]) if i in total.index else None
            updates.append((t, json.dumps(st), int(t is not None and t >= cutoff), r["sid"], d))
        if not dry:
            # passed = 既存 passed_filter(不動) AND 新總分達標
            cur.executemany(
                "UPDATE scores SET total_score=?, passed_styles=?, "
                "passed=(passed_filter AND ?) WHERE track='wave' AND stock_id=? AND date=?",
                updates)
        total_rows += len(updates)
        n_st = {k: int(styles[k].sum()) for k in styles.columns}
        print(f"{d}: 更新{len(updates):>5} 列  styles={n_st}  "
              f"新總分達標 {sum(u[2] for u in updates)} 檔  {'(深跌日)' if deep else ''}")
    if not dry:
        con.commit()
    print(f"\n{'DRY-RUN ' if dry else ''}完成：共處理 {total_rows} 列")
    con.close()


if __name__ == "__main__":
    main()
