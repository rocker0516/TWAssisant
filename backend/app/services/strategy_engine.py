"""回測實驗室的規則引擎（spec 2026-08-20-backtest-lab ②③）。

兩條路徑共用：歷史回測逐日掃、推薦頁自訂軌當日清單。
欄位註冊表是前端下拉的唯一真相——新增欄位只改這裡。

loader 契約：loader(session, dates) -> {(stock_id, date): float}
  - 日頻表直接撈；週/月頻（大戶占比、營收）forward-fill 到查詢日；
  - 衍生欄（如 收盤/MA20-1）在 loader 內算好，求值器一律拿現成數字比大小。
streak op 需要往前 n-1 個交易日的歷史，evaluate 會自動擴大載入範圍。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..storage import models

Series = dict[tuple[str, date], float]
Loader = Callable[[Session, list[date]], Series]


@dataclass(frozen=True)
class Field:
    label: str
    group: str   # 技術 / 籌碼 / 基本面 / 市場
    unit: str
    loader: Loader


def _daily(col_map: Callable[[], tuple]) -> Loader:
    """日頻直取：col_map() 回 (model欄位, 取值函式)。"""
    def load(session: Session, dates: list[date]) -> Series:
        model_cols, fn = col_map()
        rows = session.execute(
            select(*model_cols).where(model_cols[1].in_(dates))
        ).all()
        out: Series = {}
        for r in rows:
            v = fn(r)
            if v is not None:
                out[(r[0], r[1])] = float(v)
        return out
    return load


def _price(attr: str) -> Loader:
    def cols():
        c = getattr(models.DailyPrice, attr)
        return ((models.DailyPrice.stock_id, models.DailyPrice.date, c),
                lambda r: r[2])
    return _daily(cols)


def _indicator_gap(ma_attr: str) -> Loader:
    """收盤/MA - 1（%）。close 與 MA 任一缺就略過該筆。"""
    def load(session: Session, dates: list[date]) -> Series:
        ma = getattr(models.Indicator, ma_attr)
        rows = session.execute(
            select(models.Indicator.stock_id, models.Indicator.date, ma,
                   models.DailyPrice.close)
            .join(models.DailyPrice,
                  (models.DailyPrice.stock_id == models.Indicator.stock_id)
                  & (models.DailyPrice.date == models.Indicator.date))
            .where(models.Indicator.date.in_(dates))
        ).all()
        return {(sid, d): (cl / m - 1.0) * 100
                for sid, d, m, cl in rows if m and cl}
    return load


def _indicator(attr: str) -> Loader:
    def load(session: Session, dates: list[date]) -> Series:
        col = getattr(models.Indicator, attr)
        rows = session.execute(
            select(models.Indicator.stock_id, models.Indicator.date, col)
            .where(models.Indicator.date.in_(dates))
        ).all()
        return {(sid, d): float(v) for sid, d, v in rows if v is not None}
    return load


def _atr_pct(session: Session, dates: list[date]) -> Series:
    rows = session.execute(
        select(models.Indicator.stock_id, models.Indicator.date,
               models.Indicator.atr14, models.DailyPrice.close)
        .join(models.DailyPrice,
              (models.DailyPrice.stock_id == models.Indicator.stock_id)
              & (models.DailyPrice.date == models.Indicator.date))
        .where(models.Indicator.date.in_(dates))
    ).all()
    return {(sid, d): atr / cl * 100 for sid, d, atr, cl in rows if atr and cl}


def _table_col(model, attr: str) -> Loader:
    """泛化日頻表取值：select stock_id/date/attr、過濾 None。"""
    def load(session: Session, dates: list[date]) -> Series:
        col = getattr(model, attr)
        rows = session.execute(
            select(model.stock_id, model.date, col)
            .where(model.date.in_(dates))
        ).all()
        return {(sid, d): float(v) for sid, d, v in rows if v is not None}
    return load


def _inst_roll(attr: str, n: int) -> Loader:
    """法人 n 日累計（張）。往前多載 n-1 個交易日再滾動加總。"""
    def load(session: Session, dates: list[date]) -> Series:
        since = min(dates) - timedelta(days=n * 3)  # 交易日緩衝，粗放無妨
        col = getattr(models.Institutional, attr)
        rows = session.execute(
            select(models.Institutional.stock_id, models.Institutional.date, col)
            .where(models.Institutional.date >= since,
                   models.Institutional.date <= max(dates))
            .order_by(models.Institutional.date)
        ).all()
        by_sid: dict[str, list[tuple[date, float]]] = {}
        for sid, d, v in rows:
            if v is not None:
                by_sid.setdefault(sid, []).append((d, float(v)))
        want = set(dates)
        out: Series = {}
        for sid, seq in by_sid.items():
            for i, (d, _) in enumerate(seq):
                if d in want and i + 1 >= n:
                    out[(sid, d)] = sum(v for _, v in seq[i - n + 1: i + 1])
        return out
    return load


def _ffill(model, value_attr: str, date_expr: Callable) -> Loader:
    """週/月頻資料 forward-fill：查詢日取「≤該日最近一筆」。

    date_expr(row) -> date：月營收沒有 date 欄，用 (year, month) 折算成
    次月 10 日（公告時點的保守近似，避免前視）。
    """
    def load(session: Session, dates: list[date]) -> Series:
        rows = [r for r in session.execute(select(model)).scalars().all()
                if getattr(r, value_attr) is not None]
        by_sid: dict[str, list[tuple[date, float]]] = {}
        for r in rows:
            by_sid.setdefault(r.stock_id, []).append(
                (date_expr(r), float(getattr(r, value_attr))))
        out: Series = {}
        for sid, seq in by_sid.items():
            seq.sort()
            for d in dates:
                latest = None
                for ed, v in seq:
                    if ed <= d:
                        latest = v
                    else:
                        break
                if latest is not None:
                    out[(sid, d)] = latest
        return out
    return load


def _rev_date(r) -> date:
    y, m = (r.year, r.month + 1) if r.month < 12 else (r.year + 1, 1)
    return date(y, m, 10)


FIELD_REGISTRY: dict[str, Field] = {
    # 技術
    "close":        Field("收盤價", "技術", "元", _price("close")),
    "turnover":     Field("成交金額", "技術", "元", _price("turnover")),
    "ma20_gap":     Field("收盤/月線乖離", "技術", "%", _indicator_gap("ma20")),
    "ma60_gap":     Field("收盤/季線乖離", "技術", "%", _indicator_gap("ma60")),
    "bias_20":      Field("20日乖離", "技術", "%", _indicator("bias_20")),
    "bias_60":      Field("60日乖離", "技術", "%", _indicator("bias_60")),
    "kd_k":         Field("KD K值", "技術", "", _indicator("kd_k")),
    "atr_pct":      Field("波動度 ATR", "技術", "%", _atr_pct),
    # 籌碼（class 名以 models.py 實名為準）
    "foreign_net_5": Field("外資5日累計買超", "籌碼", "張", _inst_roll("foreign_net", 5)),
    "trust_net_5":   Field("投信5日累計買超", "籌碼", "張", _inst_roll("trust_net", 5)),
    "foreign_net_1": Field("外資當日買超", "籌碼", "張", _inst_roll("foreign_net", 1)),
    "trust_net_1":   Field("投信當日買超", "籌碼", "張", _inst_roll("trust_net", 1)),
    "big_pct": Field("大戶占比", "籌碼", "%",
                     _ffill(models.ShareholdingDistribution, "big_pct",
                            lambda r: r.date)),
    "margin_chg": Field("融資增減", "籌碼", "張", _table_col(models.Margin, "margin_change")),
    "sbl_chg":    Field("借券餘額變化", "籌碼", "張", _table_col(models.ShortLending, "sbl_change")),
    # 基本面
    "rev_yoy": Field("月營收YoY", "基本面", "%",
                     _ffill(models.RevenueMonthly, "yoy", _rev_date)),
    "rev_mom": Field("月營收MoM", "基本面", "%",
                     _ffill(models.RevenueMonthly, "mom", _rev_date)),
}


_OPS = {"gt", "lt", "gte", "lte", "streak_gt", "streak_lt"}
_CMP = {"gt": lambda a, b: a > b, "lt": lambda a, b: a < b,
        "gte": lambda a, b: a >= b, "lte": lambda a, b: a <= b}


def registry_meta() -> list[dict]:
    return [{"key": k, "label": f.label, "group": f.group, "unit": f.unit}
            for k, f in FIELD_REGISTRY.items()]


def trading_dates(session: Session, start: date, end: date) -> list[date]:
    rows = session.execute(
        select(models.MarketIndex.date)
        .where(models.MarketIndex.date >= start, models.MarketIndex.date <= end)
        .order_by(models.MarketIndex.date)
    ).scalars().all()
    return list(rows)


def _validate(conditions: list[dict]) -> None:
    for c in conditions:
        if c.get("field") not in FIELD_REGISTRY:
            raise ValueError(f"未知欄位：{c.get('field')}")
        if c.get("op") not in _OPS:
            raise ValueError(f"未知運算子：{c.get('op')}")
        if c["op"].startswith("streak"):
            v = c.get("value")
            if not isinstance(v, dict) or "n" not in v or "threshold" not in v:
                raise ValueError("streak op 的 value 需為 {n, threshold}")


def evaluate(session: Session, conditions: list[dict],
             dates: list[date]) -> dict[date, list[str]]:
    """AND 求值。streak op 自動往前擴載 n-1 個資料日（以 DailyPrice 日曆近似）。"""
    _validate(conditions)
    if not conditions or not dates:
        return {d: [] for d in dates}

    max_streak = max((c["value"]["n"] for c in conditions
                      if c["op"].startswith("streak")), default=1)
    load_dates = dates
    if max_streak > 1:
        # 擴大載入視窗：取 dates 之前的交易日補足 streak 歷史
        earliest = min(dates)
        prior = session.execute(
            select(models.DailyPrice.date).distinct()
            .where(models.DailyPrice.date < earliest)
            .order_by(models.DailyPrice.date.desc()).limit(max_streak - 1)
        ).scalars().all()
        load_dates = sorted(set(dates) | set(prior))

    series = {f: FIELD_REGISTRY[f].loader(session, load_dates)
              for f in {c["field"] for c in conditions}}  # 每欄只載一次

    ordered = sorted(load_dates)
    idx = {d: i for i, d in enumerate(ordered)}
    out: dict[date, list[str]] = {}
    universe = {sid for s in series.values() for (sid, _) in s}
    for d in dates:
        hit: list[str] = []
        for sid in universe:
            ok = True
            for c in conditions:
                s = series[c["field"]]
                if c["op"] in _CMP:
                    v = s.get((sid, d))
                    if v is None or not _CMP[c["op"]](v, float(c["value"])):
                        ok = False
                        break
                else:  # streak_gt / streak_lt
                    n, th = c["value"]["n"], float(c["value"]["threshold"])
                    cmp = _CMP["gt" if c["op"] == "streak_gt" else "lt"]
                    i = idx[d]
                    if i + 1 < n:
                        ok = False
                        break
                    window = [s.get((sid, ordered[j])) for j in range(i - n + 1, i + 1)]
                    if any(v is None or not cmp(v, th) for v in window):
                        ok = False
                        break
            if ok:
                hit.append(sid)
        out[d] = sorted(hit)
    return out
