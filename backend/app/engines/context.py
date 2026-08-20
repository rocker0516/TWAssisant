"""StockContext（架構③）：每檔每天打包所有資料，餵給所有規則。

規則只讀 context、不各自查 DB。ScoringEngine 批次載入後切片建 context。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from ..storage import models


@dataclass
class StockContext:
    stock: models.Stock
    date: date
    prices: pd.DataFrame  # 升冪，欄 open/high/low/close/volume，index=date
    inds: pd.DataFrame  # 升冪，indicator 欄，index=date
    inst: pd.DataFrame  # 升冪，法人欄，index=date（可能空）
    margin: pd.DataFrame | None = None  # 升冪，融資融券欄，index=date（可能空/None）
    holding: pd.DataFrame | None = None  # 升冪，集保股權分散週資料（可能空/None）
    # 長線軌資料（P1 fundamentals 之後填；先給空/None）
    valuation: pd.Series | None = None
    revenue: pd.DataFrame | None = None  # 月營收「歷史」升冪（欄 year/month/revenue/yoy/mom），已依公布日切到 ≤date
    financials: pd.DataFrame | None = None  # 季財報「歷史」升冪（欄 year/quarter/eps/三率），單季值，已依申報期限切到 ≤date
    sector: models.SectorDaily | None = None  # P3
    events: list[models.Event] | None = None  # P4（近期利空，給 NewsRiskSignal）
    # 長線軌（釣大魚）補充：類股相對量尺 + 近 60 日展望/利空事件
    fund_rel: dict | None = None  # {"yoy3m_rank": 0~1|None（近3月均YoY類股內百分位）, "pe_sector_median": float|None}
    events_60d: list[models.Event] | None = None  # 近 60 日「展望」「利空」事件（OutlookScore 用）
    # 長線分數滑落訊號（ScoreSlipSignal）用：該股 long 軌最近 5 筆 total_score（新→舊）
    long_scores: list = field(default_factory=list)
    long_passed_filter: bool | None = None  # 最近一筆 Score 的 passed_filter

    # ── 行情 / 指標 ──

    @property
    def close(self) -> float | None:
        return float(self.prices["close"].iloc[-1]) if len(self.prices) else None

    @property
    def ind(self) -> pd.Series | None:
        """最新一日指標。"""
        return self.inds.iloc[-1] if len(self.inds) else None

    def ind_ago(self, n: int) -> pd.Series | None:
        """n 個交易日前的指標列（看斜率用）。"""
        return self.inds.iloc[-1 - n] if len(self.inds) > n else None

    @property
    def n_bars(self) -> int:
        return len(self.prices)

    def recent_low(self, days: int = 20, exclude_today: bool = True) -> float | None:
        """近 days 日最低（前低，支撐用）。"""
        lows = self.prices["low"]
        if exclude_today:
            lows = lows.iloc[:-1]
        lows = lows.iloc[-days:]
        return float(lows.min()) if len(lows) else None

    # ── 籌碼 ──

    def inst_sum(self, col: str, days: int = 5) -> float:
        """近 days 日某法人欄位淨額加總（張）。"""
        if self.inst is None or self.inst.empty or col not in self.inst:
            return 0.0
        return float(self.inst[col].iloc[-days:].fillna(0).sum())

    def inst_consecutive_buy(self, col: str = "trust_net") -> int:
        """某法人欄位「連續買超天數」（由最新日往回數，>0 才算）。無資料回 0。

        投信連續買超＝主力認養的時序訊號（進場時機分數用）。
        """
        if self.inst is None or self.inst.empty or col not in self.inst:
            return 0
        n = 0
        for v in reversed(self.inst[col].fillna(0).tolist()):
            if v > 0:
                n += 1
            else:
                break
        return n

    def inst_cum_flip_recency(
        self,
        window: int = 20,
        lookback: int = 20,
        cols: tuple[str, ...] = ("foreign_net", "trust_net"),
    ) -> float | None:
        """法人「翻買近期性」：window 日累計淨額由負轉正(翻買)發生得多近，衰減成 0~1。

        進場時機分數的核心訊號——研究實證『法人 20 日累計由賣轉正當天進場』摸+10% 率
        最高。連續取最新一次翻買點、days_since 線性衰減（剛翻買≈1、滿 lookback≈0）。
        目前累計仍為負(法人非淨買)→ 0；窗內找不到翻買點(更早就翻買、非剛進場)→ 0；
        史料不足(< window+1 筆)→ None(整個分數缺料剔除，避免把『沒料』當『時機差』)。
        point-in-time：只用 self.inst（已切到 date ≤ 評分日）。
        """
        if self.inst is None or self.inst.empty:
            return None
        have = [c for c in cols if c in self.inst]
        if not have:
            return None
        ft = self.inst[have].fillna(0).sum(axis=1)
        if len(ft) < window + 1:
            return None
        cum = ft.rolling(window).sum()
        last = cum.iloc[-1]
        if pd.isna(last) or last <= 0:
            return 0.0
        cvals = cum.tolist()
        end = len(cvals) - 1
        start = max(window, end - lookback + 1)  # 只看 lookback 窗、且 cum 有效(需 ≥window)
        for i in range(end, start - 1, -1):
            prev = cvals[i - 1]
            if pd.isna(prev):
                continue
            if cvals[i] > 0 and prev <= 0:  # 由 ≤0 翻為 >0
                return max(0.0, 1.0 - (end - i) / lookback)
        return 0.0

    # ── 融資融券 ──

    def margin_change_pct(self, days: int = 20) -> float | None:
        """融資餘額近 days 日變化率（%）。史料不足或基準 0 回 None。"""
        if self.margin is None or self.margin.empty or "margin_balance" not in self.margin:
            return None
        s = self.margin["margin_balance"].dropna()
        if len(s) < 2:
            return None
        ref = s.iloc[-(days + 1)] if len(s) > days else s.iloc[0]
        now = s.iloc[-1]
        if not ref:
            return None
        return float((now - ref) / ref * 100.0)

    def short_margin_ratio(self) -> float | None:
        """券資比（融券餘額 / 融資餘額，%）。無融資回 None。"""
        if self.margin is None or self.margin.empty:
            return None
        if "margin_balance" not in self.margin or "short_balance" not in self.margin:
            return None
        mb = self.margin["margin_balance"].dropna()
        sb = self.margin["short_balance"].dropna()
        if len(mb) == 0 or len(sb) == 0 or not mb.iloc[-1]:
            return None
        return float(sb.iloc[-1] / mb.iloc[-1] * 100.0)

    # ── 集保股權分散（週資料）──

    def holding_latest(self, col: str) -> float | None:
        """最新一週某集保欄位（big_pct / over1000_pct / small_pct / holders / avg_lots）。"""
        if self.holding is None or self.holding.empty or col not in self.holding:
            return None
        v = self.holding[col].dropna()
        return float(v.iloc[-1]) if len(v) else None

    def holding_trend(self, col: str, weeks: int = 4) -> float | None:
        """近 weeks 週某集保欄位的變化量（最新 − 約 weeks 週前）。

        集保為週資料，史料不足（僅 1 筆）回 None。趨勢需累積數週快照才有值。
        """
        if self.holding is None or self.holding.empty or col not in self.holding:
            return None
        v = self.holding[col].dropna()
        if len(v) < 2:
            return None
        ref = v.iloc[-(weeks + 1)] if len(v) > weeks else v.iloc[0]
        return float(v.iloc[-1] - ref)

    # ── 基本面（長線軌「釣大魚」，月營收/季財報歷史，engine 已依公布時點切片）──

    def rev_yoy_tail(self, n: int) -> list[float]:
        """最近 n 個月的營收 YoY（升冪，略過 None）。史料不足回較短 list。"""
        if self.revenue is None or self.revenue.empty or "yoy" not in self.revenue:
            return []
        return [float(v) for v in self.revenue["yoy"].iloc[-n:] if pd.notna(v)]

    def rev_consec_growth_months(self) -> int | None:
        """由最新月往回數「連續 YoY>0」月數（月份必須連續，缺月即斷）。無史料回 None。"""
        if self.revenue is None or self.revenue.empty:
            return None
        rows = self.revenue[["year", "month", "yoy"]].dropna(subset=["yoy"]).values.tolist()
        if not rows:
            return None
        n = 0
        prev_ym: tuple[int, int] | None = None
        for y, m, yoy in reversed(rows):
            ym = (int(y), int(m))
            if prev_ym is not None:
                expect = (prev_ym[0] - 1, 12) if prev_ym[1] == 1 else (prev_ym[0], prev_ym[1] - 1)
                if ym != expect:
                    break
            if yoy <= 0:
                break
            n += 1
            prev_ym = ym
        return n

    def rev_cum_yoy(self) -> float | None:
        """當年累計營收 YoY（%）：今年至最新月 vs 去年同期間。lumpy 認列產業的替代量尺。"""
        if self.revenue is None or self.revenue.empty:
            return None
        df = self.revenue
        last = df.iloc[-1]
        y, m = int(last["year"]), int(last["month"])
        cur = df[(df["year"] == y) & (df["month"] <= m)]["revenue"].dropna()
        prev = df[(df["year"] == y - 1) & (df["month"] <= m)]["revenue"].dropna()
        if len(cur) == 0 or len(prev) < len(cur) or prev.sum() <= 0:
            return None
        return float((cur.sum() - prev.sum()) / prev.sum() * 100.0)

    def rev_new_high_months(self, window: int = 12) -> bool | None:
        """最新月營收是否為近 window 月新高。史料不足 window 回 None。"""
        if self.revenue is None or self.revenue.empty:
            return None
        s = self.revenue["revenue"].dropna()
        if len(s) < window:
            return None
        return bool(s.iloc[-1] >= s.iloc[-window:].max())

    def fin_tail(self, col: str, n: int) -> list[float]:
        """季財報某欄最近 n 季（升冪，略過 None）。"""
        if self.financials is None or self.financials.empty or col not in self.financials:
            return []
        return [float(v) for v in self.financials[col].iloc[-n:] if pd.notna(v)]

    def eps_ttm(self, quarters_ago: int = 0) -> float | None:
        """近 4 季 EPS 合計（trailing）。quarters_ago=4 → 一年前的 TTM。不足 4 季回 None。"""
        if self.financials is None or self.financials.empty or "eps" not in self.financials:
            return None
        s = self.financials["eps"]
        end = len(s) - quarters_ago
        if end < 4:
            return None
        window = s.iloc[end - 4 : end]
        if window.isna().any():
            return None
        return float(window.sum())
