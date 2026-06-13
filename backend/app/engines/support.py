"""支撐/壓力偵測（架構③，個股 on-demand）。

不靠 LLM 生數字 —— 純從量價歷史算出「客觀價位」，LLM 只負責解讀。三種互補來源：

1. 均線群        月線/季線/半年線/年線（動態支撐；長均權重高＝結構性強）
2. 波段轉折      pivot low/high（分形法找轉折，近期權重高）
3. 量價套牢區    成交量依價格分箱，高量價位＝最多人套牢/解套，台股最看這個

鄰近候選（預設 ±1.5%）合併成「帶」，強度 = 各來源權重和 + 多來源重疊加成（confluence）。
最後依現價分成支撐（下方）/壓力（上方），各取最近數條。

回傳純資料（dataclass），由 routes 包成 API、KLineChart 疊水平線、LLM 解讀引用。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# ── 可調參數（日後可進 settings 'support'）──
_PIVOT_K = 10  # 分形半窗：左右各 K 根都不破才算轉折
_CLUSTER_TOL = 0.015  # 合併容差（相對現價的 1.5%）
_VP_BINS = 50  # 量價分箱數
_VP_TOP_FRAC = 0.18  # 量能前 18% 的價格箱視為套牢區
_NEAR_EPS = 0.003  # 與現價差 < 0.3% 視為貼著（不分上下，歸最近側）

# 各來源基礎權重（confluence 之外的單獨貢獻）
_MA_WEIGHT = {"ma20": 1.0, "ma60": 1.6, "ma120": 2.2, "ma240": 2.8}
_MA_LABEL = {"ma20": "月線", "ma60": "季線", "ma120": "半年線", "ma240": "年線"}
_PIVOT_BASE = 1.2  # 轉折基礎權重（再乘近期係數 0.5~1.5）
_VP_WEIGHT = 2.6  # 套牢區權重（再乘量能占比）
_CONFLUENCE_BONUS = 1.5  # 每多一種來源重疊，額外加成


@dataclass
class _Cand:
    price: float
    weight: float
    method: str  # 來源標籤（給人看 / LLM 解讀）


@dataclass
class Level:
    price: float
    kind: str  # "support" | "resistance"
    strength: int  # 0~100（同檔相對強弱）
    methods: list[str] = field(default_factory=list)
    distance_pct: float = 0.0  # 相對現價（負=下方支撐、正=上方壓力）


# ── 候選產生 ──

def _ma_candidates(mas: dict[str, float | None]) -> list[_Cand]:
    out: list[_Cand] = []
    for key, w in _MA_WEIGHT.items():
        v = mas.get(key)
        if v is not None and v > 0:
            out.append(_Cand(float(v), w, _MA_LABEL[key]))
    return out


def _pivot_candidates(prices: pd.DataFrame) -> list[_Cand]:
    """分形轉折低/高。近期權重高（線性 0.5→1.5）。"""
    n = len(prices)
    if n < 2 * _PIVOT_K + 1:
        return []
    low = prices["low"].to_numpy(dtype=float)
    high = prices["high"].to_numpy(dtype=float)
    dates = prices["date"].tolist()
    out: list[_Cand] = []
    for i in range(_PIVOT_K, n - _PIVOT_K):
        win = slice(i - _PIVOT_K, i + _PIVOT_K + 1)
        recency = 0.5 + 1.0 * (i / (n - 1))  # 越新越重
        tag = getattr(dates[i], "year", None)
        ym = dates[i].strftime("%y/%m") if hasattr(dates[i], "strftime") else str(tag)
        if low[i] == low[win].min():
            out.append(_Cand(float(low[i]), _PIVOT_BASE * recency, f"前低 {ym}"))
        if high[i] == high[win].max():
            out.append(_Cand(float(high[i]), _PIVOT_BASE * recency, f"前高 {ym}"))
    return out


def _volume_profile_candidates(prices: pd.DataFrame) -> list[_Cand]:
    """量價分箱：高量價位＝套牢/解套密集區。權重 ∝ 量能占比。"""
    if prices.empty:
        return []
    typical = ((prices["high"] + prices["low"] + prices["close"]) / 3).to_numpy(dtype=float)
    vol = prices["volume"].fillna(0).to_numpy(dtype=float)
    lo, hi = float(np.nanmin(prices["low"])), float(np.nanmax(prices["high"]))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo or vol.sum() <= 0:
        return []
    edges = np.linspace(lo, hi, _VP_BINS + 1)
    hist, _ = np.histogram(typical, bins=edges, weights=vol)
    centers = (edges[:-1] + edges[1:]) / 2
    total = hist.sum()
    if total <= 0:
        return []
    share = hist / total
    # 取量能前段的箱；門檻＝該分位
    thresh = np.quantile(hist[hist > 0], 1 - _VP_TOP_FRAC) if (hist > 0).any() else np.inf
    out: list[_Cand] = []
    for c, h, s in zip(centers, hist, share):
        if h >= thresh and s > 0:
            out.append(_Cand(float(c), _VP_WEIGHT * float(min(s * 4, 1.0)), "套牢區"))
    return out


# ── 合併 / 評分 ──

def _cluster(cands: list[_Cand], ref_price: float) -> list[tuple[float, float, list[str]]]:
    """鄰近候選合併成帶。回 (價, 強度原始值, methods)。"""
    if not cands:
        return []
    tol = ref_price * _CLUSTER_TOL
    cands = sorted(cands, key=lambda c: c.price)
    clusters: list[list[_Cand]] = [[cands[0]]]
    for c in cands[1:]:
        if abs(c.price - clusters[-1][-1].price) <= tol:
            clusters[-1].append(c)
        else:
            clusters.append([c])

    out: list[tuple[float, float, list[str]]] = []
    for grp in clusters:
        wsum = sum(c.weight for c in grp)
        price = sum(c.price * c.weight for c in grp) / wsum if wsum else grp[0].price
        # 每種來源各自封頂：主貢獻＝該來源最大權重，額外觸碰遞減加成、上限 1.5。
        # 避免「兩年間某價帶累積數十個前低」把強度灌爆成假的主力位。
        by_kind: dict[str, list[float]] = defaultdict(list)
        for c in grp:
            by_kind[_method_kind(c.method)].append(c.weight)
        strength = 0.0
        for ws in by_kind.values():
            ws.sort(reverse=True)
            strength += ws[0] + min(sum(ws[1:]) * 0.3, 1.5)
        # 跨來源重疊（均線×前低×套牢量）才是真強訊號 → 額外加成
        strength += _CONFLUENCE_BONUS * (len(by_kind) - 1)
        methods = _dedup_methods([c.method for c in grp])
        out.append((round(price, 2), strength, methods))
    return out


def _method_kind(label: str) -> str:
    if label.startswith("前低") or label.startswith("前高"):
        return "pivot"
    if label == "套牢區":
        return "volume"
    return "ma"


def _dedup_methods(labels: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for lbl in labels:
        seen.setdefault(lbl, None)
    return list(seen.keys())


def detect_levels(
    prices: pd.DataFrame,
    close: float | None,
    mas: dict[str, float | None],
    *,
    max_support: int = 4,
    max_resistance: int = 3,
) -> list[Level]:
    """主入口。prices: 升冪 OHLCV（近 1~2 年）；close: 現價；mas: 最新均線值。"""
    if close is None or close <= 0 or prices is None or prices.empty:
        return []

    cands = _ma_candidates(mas) + _pivot_candidates(prices) + _volume_profile_candidates(prices)
    clusters = _cluster(cands, close)
    if not clusters:
        return []

    # 先帶「原始強度」（float）建 Level；選位後再正規化，讓強度條對應的是使用者
    # 實際看到的那幾條（而非被場外遠位壓低）。
    levels: list[Level] = []
    for price, strength, methods in clusters:
        dist = (price / close - 1) * 100
        if dist < -_NEAR_EPS * 100:
            kind = "support"
        elif dist > _NEAR_EPS * 100:
            kind = "resistance"
        else:
            kind = "support" if price <= close else "resistance"
        levels.append(
            Level(price=price, kind=kind, strength=strength, methods=methods,
                  distance_pct=round(dist, 2))
        )

    supports = _select(
        [l for l in levels if l.kind == "support"], by_near=lambda l: -l.distance_pct, n=max_support
    )
    resists = _select(
        [l for l in levels if l.kind == "resistance"], by_near=lambda l: l.distance_pct, n=max_resistance
    )

    selected = supports + resists
    mx = max((l.strength for l in selected), default=1.0) or 1.0
    for l in selected:
        l.strength = int(round(min(l.strength / mx, 1.0) * 100))

    supports.sort(key=lambda l: -l.price)
    resists.sort(key=lambda l: l.price)
    return supports + resists


_MAX_DIST_PCT = 25.0  # 超過此距離視為非可用支撐/壓力（過遠雜訊）


def _select(levels: list[Level], by_near, n: int) -> list[Level]:
    """挑「最近 1 條」＋「最強若干條」聯集：兼顧下一個落點與主要結構位。

    by_near 給「越近越小」的排序鍵。限制在 ±_MAX_DIST_PCT 內（過遠的歷史底部對
    當下無意義）；若全被濾掉則至少留最近一條。strength 此時仍為原始 float。
    """
    if not levels:
        return []
    kept = [l for l in levels if abs(l.distance_pct) <= _MAX_DIST_PCT]
    if not kept:
        kept = sorted(levels, key=by_near)[:1]
    nearest = min(kept, key=by_near)
    chosen = [nearest]
    for l in sorted(kept, key=lambda x: -x.strength):
        if len(chosen) >= n:
            break
        if l is not nearest:
            chosen.append(l)
    return chosen


# ── DB 便利層（routes / LLM 共用；engine 主體仍為純函式）──

_LEVELS_WINDOW = 500  # 取近 ~2 年（涵蓋年線、長期套牢區）


def levels_for_stock(session, stock_id: str, window: int = _LEVELS_WINDOW) -> list[Level]:
    """載入個股近窗量價 + 最新均線，回支撐/壓力清單。空資料回 []。"""
    from ..storage import models  # 延遲匯入避免循環

    rows = session.execute(
        select_recent_prices(models, stock_id, window)
    ).scalars().all()
    if not rows:
        return []
    df = pd.DataFrame(
        [
            {"date": r.date, "open": r.open, "high": r.high, "low": r.low,
             "close": r.close, "volume": r.volume}
            for r in reversed(rows)  # query 取最新在前 → 反轉成升冪
        ]
    )
    close = float(df["close"].iloc[-1]) if not df.empty and df["close"].iloc[-1] else None

    ind = session.execute(select_latest_indicator(models, stock_id)).scalars().first()
    mas = {
        "ma20": getattr(ind, "ma20", None) if ind else None,
        "ma60": getattr(ind, "ma60", None) if ind else None,
        "ma120": getattr(ind, "ma120", None) if ind else None,
        "ma240": getattr(ind, "ma240", None) if ind else None,
    }
    return detect_levels(df, close, mas)


def select_recent_prices(models, stock_id: str, window: int):
    from sqlalchemy import select

    return (
        select(models.DailyPrice)
        .where(models.DailyPrice.stock_id == stock_id)
        .order_by(models.DailyPrice.date.desc())
        .limit(window)
    )


def select_latest_indicator(models, stock_id: str):
    from sqlalchemy import select

    return (
        select(models.Indicator)
        .where(models.Indicator.stock_id == stock_id)
        .order_by(models.Indicator.date.desc())
        .limit(1)
    )
