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


def _lag_ratio(n: int, pct: bool) -> Loader:
    """個股 n 個交易日前的比值：pct=True 回 (今/前−1)×100（報酬率），否則回 今/前（倍數）。

    往前多載 n×3 個日曆日當交易日緩衝（同 _inst_roll 的粗放作法）。value_fn 從
    (atr14, close) 算出當日值，讓「n 日報酬」與「波動擴張倍數」共用同一段位移邏輯。
    """
    def make(value_sql, value_fn) -> Loader:
        def load(session: Session, dates: list[date]) -> Series:
            since = min(dates) - timedelta(days=n * 3)
            rows = session.execute(
                value_sql.where(models.DailyPrice.date >= since,
                                models.DailyPrice.date <= max(dates))
                .order_by(models.DailyPrice.date)
            ).all()
            by_sid: dict[str, list[tuple[date, float]]] = {}
            for r in rows:
                v = value_fn(r)
                if v:                      # 0 與 None 都不能當分母
                    by_sid.setdefault(r[0], []).append((r[1], v))
            want = set(dates)
            out: Series = {}
            for sid, seq in by_sid.items():
                for i, (d, v) in enumerate(seq):
                    if d in want and i >= n:
                        prev = seq[i - n][1]
                        out[(sid, d)] = (v / prev - 1.0) * 100 if pct else v / prev
            return out
        return load
    return make


_ret_n = _lag_ratio(20, pct=True)(
    select(models.DailyPrice.stock_id, models.DailyPrice.date, models.DailyPrice.close),
    lambda r: r[2])

_atr_chg20 = _lag_ratio(20, pct=False)(
    select(models.DailyPrice.stock_id, models.DailyPrice.date,
           models.Indicator.atr14, models.DailyPrice.close)
    .join(models.Indicator,
          (models.Indicator.stock_id == models.DailyPrice.stock_id)
          & (models.Indicator.date == models.DailyPrice.date)),
    lambda r: (r[2] / r[3]) if r[2] and r[3] else None)


def _mkt_bias60(session: Session, dates: list[date]) -> Series:
    """大盤收盤距季線（60 日均）%——**市場層欄位**，同一天對所有個股同值。

    這是 crash 深跌反攻的市場端閘（wave.CRASH_MKT_BIAS60=-2.3），也是
    scripts/wave_hit_challenge.py 全部結論的橫軸；放進註冊表使用者才能在實驗室
    自己重建那條 ATR 階梯。求值器是 (stock_id, date) 索引，故需展開到當日每一檔。
    """
    idx = session.execute(
        select(models.MarketIndex.date, models.MarketIndex.close)
        .where(models.MarketIndex.date <= max(dates))
        .order_by(models.MarketIndex.date)
    ).all()
    closes = [float(c) for _, c in idx]
    bias: dict[date, float] = {}
    for i in range(59, len(idx)):
        ma = sum(closes[i - 59:i + 1]) / 60
        if ma:
            bias[idx[i][0]] = (closes[i] / ma - 1.0) * 100
    want = [d for d in dates if d in bias]
    if not want:
        return {}
    rows = session.execute(
        select(models.DailyPrice.stock_id, models.DailyPrice.date)
        .where(models.DailyPrice.date.in_(want))
    ).all()
    return {(sid, d): bias[d] for sid, d in rows}


def _attention_window(session: Session, dates: list[date]) -> Series:
    """注意/處置公告窗內＝1、窗外＝0（窗外也要有值，否則 `<1` 的條件會把全部濾掉）。

    窗長用日曆日近似交易日（注意 7 天、處置 14 天），與 /recommendations/signal-decay
    的徽章同口徑。研究的「乾淨池」就是排除這一段——被列注意/處置的高波動股在崩盤段
    是最典型的落刀，不排掉會把命中率整整拉低十幾個百分點（見策略室『波段命中挑戰』）。
    """
    lo = min(dates) - timedelta(days=20)
    rows = session.execute(
        select(models.AttentionListing.stock_id, models.AttentionListing.date,
               models.AttentionListing.kind, models.AttentionListing.end_date)
        .where(models.AttentionListing.date >= lo,
               models.AttentionListing.date <= max(dates))
    ).all()
    windows: dict[str, list[tuple[date, date]]] = {}
    for sid, d0, kind, end in rows:
        if kind == "punish":
            hi = end if end and end > d0 + timedelta(days=14) else d0 + timedelta(days=14)
        else:
            hi = d0 + timedelta(days=7)
        windows.setdefault(sid, []).append((d0, hi))
    px = session.execute(
        select(models.DailyPrice.stock_id, models.DailyPrice.date)
        .where(models.DailyPrice.date.in_(dates))
    ).all()
    return {(sid, d): float(any(b <= d <= e for b, e in windows.get(sid, ())))
            for sid, d in px}


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
    "ret20":        Field("20日報酬", "技術", "%", _ret_n),
    "atr_chg20":    Field("波動擴張(ATR/20日前)", "技術", "倍", _atr_chg20),
    # 市場層：同一天對所有個股同值。crash 風格的市場端閘就是這一欄（≤-2.3）
    "mkt_bias60":   Field("大盤距季線", "市場", "%", _mkt_bias60),
    # 事件層：設 <1 就等於研究用的「乾淨池」（排除注意/處置窗內）
    "att_window":   Field("注意/處置窗內", "市場", "1=是", _attention_window),
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


def _is_number(x) -> bool:
    # bool 是 int 的子類別，排除掉避免 True/False 被當成 1/0 混進數值條件
    return isinstance(x, (int, float)) and not isinstance(x, bool)


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
            n = v["n"]
            if not isinstance(n, int) or isinstance(n, bool) or n < 1:
                raise ValueError("streak op 的 n 需為 ≥1 的整數")
            if not _is_number(v["threshold"]):
                raise ValueError("streak op 的 threshold 需為數值")
        else:
            if not _is_number(c.get("value")):
                raise ValueError(f"{c['op']} 的 value 需為數值")


def validate_strategy(conditions: list[dict], sort_field: str) -> None:
    """建立/修改策略前的單一驗證入口——條件欄位/運算子＋排序欄位皆需存在於
    FIELD_REGISTRY，否則之後 evaluate/run_backtest/FIELD_REGISTRY[...] 下標
    會晚在 backtest 或 active/daily 才炸（ValueError/KeyError），不如寫入前
    就擋掉。呼叫端把 ValueError 轉 400。
    """
    _validate(conditions)
    if sort_field not in FIELD_REGISTRY:
        raise ValueError(f"未知排序欄位：{sort_field}")


def evaluate(session: Session, conditions: list[dict],
             dates: list[date]) -> dict[date, list[str]]:
    """AND 求值。streak op 自動往前擴載 n-1 個資料日（以 DailyPrice 日曆近似）。

    前提：dates 必須是連續交易日清單；有洞會使 streak 誤判相鄰日距。"""
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


# ─────────────────────────── 回測引擎 ───────────────────────────


_EPISODE_GAP_DAYS = 15  # 訊號日相隔 > 這麼多日曆日 ＝ 換一段行情


def _episodes(rows: list[tuple[date, bool]]) -> list[dict]:
    """把 (訊號日, 命中) 依「相隔 >15 個日曆日」切成段，逐段算命中率。

    為什麼要段級：條件型策略（尤其崩勢類）同一段行情裡每天選到的是同一批股票，
    以「筆」或「日」算信賴區間會嚴重低估不確定性——有效樣本數其實是**段數**。
    這是 docs/wave-hit-challenge.md §5 的結論，回測結果一律附上段級離散度。
    """
    if not rows:
        return []
    rows = sorted(rows)
    eps: list[list[tuple[date, bool]]] = [[rows[0]]]
    for d, hit in rows[1:]:
        if (d - eps[-1][-1][0]).days > _EPISODE_GAP_DAYS:
            eps.append([])
        eps[-1].append((d, hit))
    out = []
    for e in eps:
        n = len(e)
        out.append({"start": e[0][0].isoformat(), "end": e[-1][0].isoformat(),
                    "days": len({d for d, _ in e}), "samples": n,
                    "hits": sum(1 for _, h in e if h),
                    "hit_rate": round(sum(1 for _, h in e if h) / n, 3)})
    return out


@dataclass
class BacktestResult:
    samples: int
    hits: int
    hit_rate: float | None
    base_rate: float | None
    lift: float | None
    avg_max_drawdown: float | None
    monthly: list[dict]
    episodes: list[dict]
    episode_median: float | None   # 段級中位命中率（只算 samples≥10 的段）
    episode_worst: float | None
    recent: list[dict]
    warn_loose: bool
    signal_days: int


def _load_bars(session: Session, sids: set[str], start: date, end: date):
    """{sid: (dates升冪, [(high, low)])}。end 之後多抓 horizon 由呼叫端控制。"""
    rows = session.execute(
        select(models.DailyPrice.stock_id, models.DailyPrice.date,
               models.DailyPrice.high, models.DailyPrice.low)
        .where(models.DailyPrice.stock_id.in_(sids),
               models.DailyPrice.date >= start, models.DailyPrice.date <= end)
        .order_by(models.DailyPrice.date)
    ).all()
    px: dict[str, tuple[list[date], list[tuple]]] = {}
    for sid, d, hi, lo in rows:
        dates, bars = px.setdefault(sid, ([], []))
        dates.append(d)
        bars.append((hi, lo))
    return px


def _judge(dates: list[date], bars: list[tuple], signal: date,
           target_pct: float, horizon: int, stop_pct: float | None):
    """單一樣本：回 (entry, hit, stopped, max_gain, max_dd) 或 None（無隔日資料）。

    進場錨＝訊號隔一交易日的 high；停損先碰記失敗、同日皆碰保守記失敗。
    """
    try:
        i0 = dates.index(signal)
    except ValueError:
        return None
    if i0 + 1 >= len(dates) or bars[i0 + 1][0] is None:
        return None
    entry = bars[i0 + 1][0]
    tgt = entry * (1 + target_pct / 100)
    stp = entry * (1 - stop_pct / 100) if stop_pct is not None else None
    hit = stopped = False
    max_gain = max_dd = 0.0
    for j in range(i0 + 1, min(i0 + 1 + horizon, len(dates))):
        hi, lo = bars[j]
        if hi is None or lo is None:
            continue
        max_gain = max(max_gain, (hi / entry - 1) * 100)
        max_dd = min(max_dd, (lo / entry - 1) * 100)
        if stp is not None and lo <= stp:
            stopped = True   # 同日 hi 也達標時保守記失敗 → 先判停損
            break
        if hi >= tgt:
            hit = True
            break
    return entry, hit, stopped, round(max_gain, 2), round(max_dd, 2)


def run_backtest(session: Session, conditions: list[dict], sort_field: str,
                 sort_desc: bool, top_n: int, target_pct: float,
                 horizon_days: int, stop_pct: float | None,
                 start: date, end: date) -> BacktestResult:
    sig_dates = trading_dates(session, start, end)
    per_day = evaluate(session, conditions, sig_dates)

    # 排序值：沿用註冊表 loader（排序欄不一定在條件裡）
    sort_series = FIELD_REGISTRY[sort_field].loader(session, sig_dates)
    picks: list[tuple[date, str]] = []
    warn_loose = False
    for d in sig_dates:
        cands = per_day.get(d, [])
        if len(cands) > 200:
            warn_loose = True
        cands = sorted(cands, key=lambda sid: sort_series.get((sid, d), float("-inf")),
                       reverse=sort_desc)[:top_n]
        picks.extend((d, sid) for sid in cands)

    sids = {sid for _, sid in picks}
    horizon_pad = timedelta(days=horizon_days * 2 + 14)
    px = _load_bars(session, sids, start, end + horizon_pad)

    names = dict(session.execute(
        select(models.Stock.id, models.Stock.name).where(models.Stock.id.in_(sids))
    ).all()) if sids else {}

    hits = 0
    dds: list[float] = []
    monthly: dict[str, list[int]] = {}
    details: list[dict] = []
    judged_rows: list[tuple[date, bool]] = []
    for d, sid in picks:
        if sid not in px:
            continue
        judged = _judge(*px[sid], d, target_pct, horizon_days, stop_pct)
        if judged is None:
            continue
        entry, hit, stopped, mg, mdd = judged
        hits += int(hit)
        dds.append(mdd)
        judged_rows.append((d, hit))
        m = d.strftime("%Y-%m")
        monthly.setdefault(m, [0, 0])
        monthly[m][0] += 1
        monthly[m][1] += int(hit)
        details.append({"date": d.isoformat(), "stock_id": sid,
                        "name": names.get(sid, sid), "entry": entry, "hit": hit,
                        "stopped": stopped, "max_gain_pct": mg, "max_dd_pct": mdd})
    samples = len(dds)

    # 基率對照：每 5 個訊號日抽 1 日、全市場同口徑（控制同步延遲）
    base_rate = None
    base_days = sig_dates[::5]
    if base_days:
        all_sids = set(session.execute(
            select(models.DailyPrice.stock_id).distinct()
            .where(models.DailyPrice.date.in_(base_days))
        ).scalars().all())
        bpx = _load_bars(session, all_sids, start, end + horizon_pad)
        bn = bh = 0
        for d in base_days:
            for sid, (dts, bars) in bpx.items():
                j = _judge(dts, bars, d, target_pct, horizon_days, stop_pct)
                if j is not None:
                    bn += 1
                    bh += int(j[1])
        base_rate = round(bh / bn, 3) if bn else None

    hit_rate = round(hits / samples, 3) if samples else None
    episodes = _episodes(judged_rows)
    solid = sorted(e["hit_rate"] for e in episodes if e["samples"] >= 10)
    return BacktestResult(
        samples=samples, hits=hits, hit_rate=hit_rate, base_rate=base_rate,
        lift=(round(hit_rate / base_rate, 2)
              if hit_rate is not None and base_rate else None),
        avg_max_drawdown=round(sum(dds) / len(dds), 2) if dds else None,
        monthly=[{"month": m, "samples": v[0], "hits": v[1]}
                 for m, v in sorted(monthly.items())],
        episodes=episodes,
        episode_median=(solid[len(solid) // 2] if solid else None),
        episode_worst=(solid[0] if solid else None),
        recent=details[-60:],
        warn_loose=warn_loose, signal_days=len(sig_dates),
    )
