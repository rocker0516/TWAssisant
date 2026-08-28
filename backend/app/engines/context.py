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
    # 基本面最新一筆（出場機 _build_context 以 Series 填；scoring 已不填——長線軌移除）
    valuation: pd.Series | None = None
    revenue: pd.Series | None = None    # 最新月營收（revenue/yoy/mom），FundamentalWeakSignal 用
    financials: pd.Series | None = None  # 最新季報一筆（出場/顯示用）
    sector: models.SectorDaily | None = None  # P3
    events: list[models.Event] | None = None  # P4（近期利空，給 NewsRiskSignal）

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

