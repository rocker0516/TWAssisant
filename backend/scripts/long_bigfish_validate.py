"""長線軌「釣大魚」walk-forward 驗證（步驟③）。

每月一個評分日（2021-06 起），point-in-time 重算長線軌硬篩+六因子（直接複用
rules/long.py 的規則類，杜絕邏輯漂移），驗證：
  1. 大魚率 lift：進場後 12 個月（245 交易日）內最高價曾達 +50%/+30%/+100% 的比例，
     高分組 vs 全宇宙基準率（路徑無關，同會噴 hit 精神；進場錨=隔天開盤）。
  2. 相對報酬：12 個月 close 報酬 vs 加權指數（勝率 + 中位超額）。
  3. 單因子 IC：各 sub_score 對 12 月報酬的橫截面 Spearman rank IC（逐日→均值+t值）。
  4. 門檻掃描：50/60/70/80 分桶的大魚率與樣本數（校準 threshold 用）。

不寫任何表（Score 歷史不動）。已知限制：
  - 宇宙=現存股票（回補只涵蓋 known ids）→ 有存活者偏差，與本專案其他回測一致。
  - 展望消息無歷史（events 近期才有）→ outlook 因子回測值≈「基準 40+投信成分」，
    其消息成分的 IC 要等 events 累積一年後才能補測。
  - 處置警示無歷史 → 共用硬篩以 非ETF+均量500張+掛牌60日 近似。

用法：cd backend && python -m scripts.long_bigfish_validate [start_ym end_ym]
"""

from __future__ import annotations

import sys
import time
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.engines.context import StockContext
from app.engines.rules.long import LONG_FILTERS, LONG_SCORERS
from app.engines.scoring import _fund_relatives
from app.storage import models
from app.storage.database import SessionLocal, init_db

FWD_TDAYS = 245          # 12 個月 ≈ 245 交易日
BIGFISH_LEVELS = (1.3, 1.5, 2.0)
MIN_VOL = 500 * 1000     # 均量 500 張
MIN_BARS = 60
THRESH_BUCKETS = [(0, 50), (50, 60), (60, 70), (70, 80), (80, 101)]

_WEIGHTS = {s.category: s.default_weight for s in LONG_SCORERS}
_EMPTY = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _month_firsts(cal: np.ndarray, start: date, end: date) -> list[date]:
    """行事曆（升冪交易日）中每月第一個交易日。"""
    out, seen = [], set()
    for d in cal:
        if d < start or d > end:
            continue
        key = (d.year, d.month)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _rev_avail(df: pd.DataFrame) -> np.ndarray:
    next_m = df["month"] % 12 + 1
    next_y = df["year"] + (df["month"] == 12).astype(int)
    return pd.to_datetime(dict(year=next_y, month=next_m, day=10)).values


_FIN_DL = {1: (0, 5, 15), 2: (0, 8, 14), 3: (0, 11, 14), 4: (1, 3, 31)}


def _fin_avail(df: pd.DataFrame) -> np.ndarray:
    dl = df["quarter"].map(_FIN_DL)
    return pd.to_datetime(dict(year=df["year"] + dl.str[0], month=dl.str[1], day=dl.str[2])).values


def _spearman_ic(scores: list[float], rets: list[float]) -> float | None:
    if len(scores) < 30:
        return None
    s = pd.Series(scores).rank()
    r = pd.Series(rets).rank()
    return float(s.corr(r))


def main(argv: list[str]) -> None:
    start = date.fromisoformat(argv[1] + "-01") if len(argv) > 2 else date(2021, 6, 1)
    end = date.fromisoformat(argv[2] + "-01") if len(argv) > 2 else date(2025, 7, 31)

    init_db()
    s = SessionLocal()

    _log("載入資料…")
    stocks = {
        st.id: st for st in s.execute(select(models.Stock).options(joinedload(models.Stock.sector))).scalars()
    }
    idx = pd.read_sql("SELECT date, close FROM market_index ORDER BY date", s.get_bind(), parse_dates=["date"])
    cal = np.array([d.date() for d in idx["date"]])
    idx_close = idx["close"].to_numpy()

    px = pd.read_sql(
        "SELECT stock_id, date, open, high, close, volume FROM daily_prices ORDER BY stock_id, date",
        s.get_bind(), parse_dates=["date"],
    )
    px_g = {
        sid: (g["date"].dt.date.to_numpy(), g["open"].to_numpy(float), g["high"].to_numpy(float),
              g["close"].to_numpy(float), g["volume"].to_numpy(float))
        for sid, g in px.groupby("stock_id", sort=False)
    }
    del px

    rev = pd.read_sql(
        "SELECT stock_id, year, month, revenue, yoy, mom FROM revenue_monthly ORDER BY stock_id, year, month",
        s.get_bind(),
    )
    rev_g = {}
    for sid, g in rev.groupby("stock_id", sort=False):
        g = g.reset_index(drop=True)
        rev_g[sid] = (g, _rev_avail(g))

    fin = pd.read_sql(
        "SELECT stock_id, year, quarter, eps, gross_margin, op_margin, net_margin FROM financials_quarterly "
        "ORDER BY stock_id, year, quarter", s.get_bind(),
    )
    fin_g = {}
    for sid, g in fin.groupby("stock_id", sort=False):
        g = g.reset_index(drop=True)
        fin_g[sid] = (g, _fin_avail(g))

    val = pd.read_sql("SELECT stock_id, date, pe FROM valuation ORDER BY stock_id, date", s.get_bind(), parse_dates=["date"])
    val_g = {sid: (g["date"].dt.date.to_numpy(), g["pe"].to_numpy(float)) for sid, g in val.groupby("stock_id", sort=False)}
    del val

    inst = pd.read_sql(
        "SELECT stock_id, date, trust_net FROM institutional ORDER BY stock_id, date", s.get_bind(), parse_dates=["date"],
    )
    inst_g = {sid: (g["date"].dt.date.to_numpy(), g["trust_net"].to_numpy(float)) for sid, g in inst.groupby("stock_id", sort=False)}
    del inst

    samples = _month_firsts(cal, start, end)
    _log(f"樣本評分日 {len(samples)} 個（{samples[0]} ~ {samples[-1]}）；股票 {len(px_g)} 檔")

    recs: list[dict] = []
    for d in samples:
        d64 = np.datetime64(d)
        # 先做本日全宇宙的 PIT 切片（fund_rel 需要）
        rev_sliced: dict[str, pd.DataFrame] = {}
        for sid, (g, avail) in rev_g.items():
            k = int(np.searchsorted(avail, d64, side="right"))
            if k:
                rev_sliced[sid] = g.iloc[:k]
        val_at: dict[str, pd.Series] = {}
        for sid, (dates, pes) in val_g.items():
            k = int(np.searchsorted(dates, d, side="right"))
            if k and (d - dates[k - 1]).days <= 45 and not np.isnan(pes[k - 1]):
                val_at[sid] = pd.Series({"pe": float(pes[k - 1])})
        fund_rank, pe_median = _fund_relatives(rev_sliced, val_at, stocks)

        n_universe = 0
        for sid, (pdates, po, ph, pc, pv) in px_g.items():
            st = stocks.get(sid)
            if st is None or st.is_etf:
                continue
            i = int(np.searchsorted(pdates, d, side="right")) - 1
            if i < MIN_BARS or pdates[i] != d and (d - pdates[i]).days > 7:
                continue  # 近一週沒交易（停牌/下市邊緣）不進宇宙
            e = i + 1  # 進場=隔天
            if e + FWD_TDAYS >= len(pdates):
                continue  # 前向窗不足
            if np.nanmean(pv[max(0, i - 19): i + 1]) < MIN_VOL:
                continue
            entry = po[e]
            if not entry or np.isnan(entry):
                continue

            n_universe += 1
            fwd_high = np.nanmax(ph[e: e + FWD_TDAYS + 1])
            exit_close = pc[e + FWD_TDAYS]
            ret12 = exit_close / entry - 1
            mi0 = idx_close[int(np.searchsorted(cal, pdates[e], side="right")) - 1]
            mi1 = idx_close[int(np.searchsorted(cal, pdates[e + FWD_TDAYS], side="right")) - 1]
            mkt12 = mi1 / mi0 - 1

            fg = fin_g.get(sid)
            fin_sliced = None
            if fg is not None:
                k = int(np.searchsorted(fg[1], d64, side="right"))
                fin_sliced = fg[0].iloc[:k] if k else None
            ig = inst_g.get(sid)
            inst_df = _EMPTY
            if ig is not None:
                k = int(np.searchsorted(ig[0], d, side="right"))
                inst_df = pd.DataFrame({"trust_net": ig[1][max(0, k - 30): k]})

            ctx = StockContext(
                stock=st, date=d,
                prices=pd.DataFrame({"close": pc[max(0, i - 259): i + 1]}),  # FreshnessScore 需要 12 月基期
                inds=_EMPTY, inst=inst_df,
                valuation=val_at.get(sid), revenue=rev_sliced.get(sid), financials=fin_sliced,
                fund_rel={"yoy3m_rank": fund_rank.get(sid), "pe_sector_median": pe_median.get(st.sector_id)},
            )
            passed = all(f.passes(ctx) for f in LONG_FILTERS)
            consec = ctx.rev_consec_growth_months()  # 魚齡：成長 streak 已跑多久
            mom12 = entry / pc[e - FWD_TDAYS] - 1 if e >= FWD_TDAYS and pc[e - FWD_TDAYS] else None  # 進場前12月漲幅
            subs: dict[str, float] = {}
            num = den = 0.0
            for sc in LONG_SCORERS:
                raw = sc.score(ctx)
                if raw is None:
                    continue
                subs[sc.category] = float(raw)
                w = _WEIGHTS[sc.category]
                num += raw * w
                den += w
            total = num / den if den else None

            recs.append({
                "date": d, "stock_id": sid, "passed": passed, "total": total, **subs,
                "consec": consec, "mom12": mom12,
                "ret12": ret12, "mkt12": mkt12, "excess": ret12 - mkt12,
                **{f"fish{int((lv - 1) * 100)}": fwd_high / entry >= lv for lv in BIGFISH_LEVELS},
            })
        _log(f"{d}: 宇宙 {n_universe} 檔（硬篩過 {sum(1 for r in recs if r['date'] == d and r['passed'])}）")

    s.close()
    df = pd.DataFrame(recs)
    _log(f"總樣本 {len(df)} 檔·日")

    fish_cols = [f"fish{int((lv - 1) * 100)}" for lv in BIGFISH_LEVELS]

    def _group_stats(g: pd.DataFrame, label: str) -> None:
        if len(g) == 0:
            print(f"  {label}: 空")
            return
        fr = " ".join(f"{c}={g[c].mean() * 100:5.1f}%" for c in fish_cols)
        print(f"  {label}: n={len(g):6d} | {fr} | 12月中位報酬 {g['ret12'].median() * 100:+6.1f}% "
              f"| 中位超額 {g['excess'].median() * 100:+6.1f}% | 勝過大盤 {(g['excess'] > 0).mean() * 100:4.1f}%")

    print("\n════ 大魚率與報酬（進場=隔天開盤，前向 245 交易日，路徑無關）════")
    _group_stats(df, "全宇宙（基準）      ")
    _group_stats(df[df["passed"]], "硬篩通過            ")
    _group_stats(df[df["passed"] & (df["total"] >= 70)], "硬篩+分數≥70（現門檻）")
    top = df[df["passed"]].groupby("date", group_keys=False).apply(lambda g: g.nlargest(max(1, len(g) // 10), "total"))
    _group_stats(top, "硬篩內前 10%         ")

    print("\n════ 門檻掃描（硬篩通過者按總分分桶）════")
    for lo, hi in THRESH_BUCKETS:
        _group_stats(df[df["passed"] & (df["total"] >= lo) & (df["total"] < hi)], f"{lo:3d}~{hi:3d} 分")

    print("\n════ 單因子 IC（橫截面 Spearman vs 12月報酬 / 超額報酬，逐日→均值±t）════")
    cats = [sc.category for sc in LONG_SCORERS] + ["total"]
    for target in ("ret12", "excess"):
        print(f"  target={target}")
        for cat in cats:
            ics = []
            for _, g in df.groupby("date"):
                gg = g.dropna(subset=[cat, target]) if cat in g else pd.DataFrame()
                if len(gg) >= 30:
                    ic = _spearman_ic(gg[cat].tolist(), gg[target].tolist())
                    if ic is not None:
                        ics.append(ic)
            if not ics:
                print(f"    {cat:16s}: 樣本不足")
                continue
            arr = np.array(ics)
            t = arr.mean() / (arr.std(ddof=1) / np.sqrt(len(arr))) if len(arr) > 1 and arr.std(ddof=1) > 0 else float("nan")
            print(f"    {cat:16s}: IC均值 {arr.mean():+.4f}  t={t:+5.2f}  (n={len(arr)} 日)")

    out = "/tmp/long_bigfish_validate.parquet"
    try:
        df.to_parquet(out)
        _log(f"明細已存 {out}")
    except Exception:
        df.to_csv("/tmp/long_bigfish_validate.csv", index=False)
        _log("明細已存 /tmp/long_bigfish_validate.csv")


if __name__ == "__main__":
    main(sys.argv)
