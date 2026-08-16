"""恐懼貪婪指數（台股版 Fear & Greed）：多組件 → 0~100 綜合（越高越貪婪）。

作法仿 CNN Fear & Greed：每個組件先算原始日序列，再對「截至當日、往回最多
252 個交易日」的自身歷史做百分位排名（0~100，方向統一為貪婪=高），
綜合分＝可用組件平均。這樣不用人工定閾值，短歷史也能自動適應。

組件與方向（皆為現有資料，無新抓取）：
- momentum   大盤動能：加權指數 / 60 日均線 − 1（高=貪婪）
- breadth    市場廣度：站上月線（MA20）家數比（高=貪婪）
- advancers  漲跌家數：上漲家數比 5 日均（高=貪婪）
- pc_ratio   選擇權 P/C 未平倉比（高=避險需求強=恐懼 → 反向）
- fut_oi     外資台指期未平倉淨口數（高=貪婪）
- inst_flow  三大法人現貨買賣超 5 日合計（高=貪婪）
- volatility 大盤 20 日年化波動（高=恐懼 → 反向）
- margin     全市場融資餘額 20 日變化率（高=散戶槓桿升溫=貪婪）

資料長度不足（<40 個交易日）的組件不入列；綜合分需 ≥3 個組件。
結果以「最新交易日」為鍵做行程內快取（各表日更後自動失效）。
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..storage import models

_LOOKBACK = 252   # 百分位排名視窗（交易日）
_MIN_OBS = 40     # 組件最少觀測數，低於此不入列
_HISTORY_N = 120  # 回傳綜合分歷史點數

_LABELS = [(25, "極度恐懼"), (45, "恐懼"), (55, "中性"), (75, "貪婪"), (101, "極度貪婪")]

_COMPONENT_META = {
    "momentum": ("大盤動能", "加權指數相對 60 日均線"),
    "breadth": ("市場廣度", "站上月線家數比"),
    "advancers": ("漲跌家數", "上漲家數比（5 日均）"),
    "pc_ratio": ("Put/Call 比", "選擇權未平倉 P/C（反向）"),
    "fut_oi": ("外資期貨", "台指期未平倉淨口數"),
    "inst_flow": ("法人買賣超", "三大法人 5 日合計"),
    "volatility": ("波動率", "大盤 20 日年化波動（反向）"),
    "margin": ("融資動向", "融資餘額 20 日變化"),
}

_cache: dict = {}


def label_of(score: float) -> str:
    for hi, name in _LABELS:
        if score < hi:
            return name
    return "極度貪婪"


def _pct_rank_tail(s: pd.Series, n: int) -> pd.Series:
    """對序列最後 n 點，各自對（含自身）往回最多 _LOOKBACK 點做百分位 0~100。"""
    vals = s.dropna()
    out: dict = {}
    for i in range(max(0, len(vals) - n), len(vals)):
        win = vals.iloc[max(0, i - _LOOKBACK + 1): i + 1]
        if len(win) < _MIN_OBS:
            continue
        out[vals.index[i]] = round(float(win.rank(pct=True).iloc[-1]) * 100, 1)
    return pd.Series(out, dtype=float)


def _series_sql(session: Session, sql: str, start: date) -> pd.Series:
    rows = session.execute(text(sql), {"start": start.isoformat()}).all()
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series({d: v for d, v in rows if v is not None}, dtype=float)
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def _raw_components(session: Session, start: date) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}

    # 大盤指數：動能 + 波動（反向）
    idx_rows = session.execute(
        select(models.MarketIndex.date, models.MarketIndex.close)
        .where(models.MarketIndex.close.is_not(None))
        .order_by(models.MarketIndex.date)
    ).all()
    if idx_rows:
        idx = pd.Series({d: c for d, c in idx_rows}, dtype=float)
        idx.index = pd.to_datetime(idx.index)
        ma60 = idx.rolling(60, min_periods=30).mean()
        out["momentum"] = (idx / ma60 - 1).dropna()
        vol = idx.pct_change().rolling(20, min_periods=15).std() * (240 ** 0.5)
        out["volatility"] = (-vol).dropna()  # 反向：波動低=貪婪

    # 廣度：站上月線家數比（%）
    out["breadth"] = _series_sql(session, """
        SELECT i.date, AVG(CASE WHEN p.close > i.ma20 THEN 1.0 ELSE 0.0 END) * 100
        FROM indicators i
        JOIN daily_prices p ON p.stock_id = i.stock_id AND p.date = i.date
        WHERE i.date >= :start AND i.ma20 IS NOT NULL AND p.close IS NOT NULL
        GROUP BY i.date
    """, start)

    # 漲跌家數：上漲家數比（%），5 日平滑
    adv = _series_sql(session, """
        SELECT date, AVG(up) * 100 FROM (
            SELECT date,
                   CASE WHEN close > LAG(close) OVER (PARTITION BY stock_id ORDER BY date)
                        THEN 1.0 ELSE 0.0 END AS up
            FROM daily_prices WHERE date >= :start AND close IS NOT NULL
        ) GROUP BY date
    """, start)
    # 窗口起點首日 LAG=NULL 全記 0，剔除首日再平滑
    out["advancers"] = adv.iloc[1:].rolling(5, min_periods=3).mean().dropna()

    # 選擇權 P/C 未平倉比（反向）與外資期貨淨口數
    der = session.execute(
        select(models.MarketDerivatives.date, models.MarketDerivatives.pc_oi_ratio,
               models.MarketDerivatives.tx_foreign_oi_net)
        .order_by(models.MarketDerivatives.date)
    ).all()
    if der:
        di = pd.to_datetime([d for d, _, _ in der])
        pc = pd.Series([v for _, v, _ in der], index=di, dtype=float).dropna()
        out["pc_ratio"] = -pc  # 反向：P/C 高=恐懼
        out["fut_oi"] = pd.Series([v for _, _, v in der], index=di, dtype=float).dropna()

    # 法人現貨買賣超 5 日合計（億元）
    inst_rows = session.execute(
        select(models.InstitutionalMarketTotal.date, models.InstitutionalMarketTotal.total_net)
        .order_by(models.InstitutionalMarketTotal.date)
    ).all()
    if inst_rows:
        inst = pd.Series({d: v for d, v in inst_rows if v is not None}, dtype=float)
        inst.index = pd.to_datetime(inst.index)
        out["inst_flow"] = inst.rolling(5, min_periods=3).sum().dropna()

    # 全市場融資餘額 20 日變化率（%）
    mg = _series_sql(session, """
        SELECT date, SUM(margin_balance) FROM margin
        WHERE date >= :start AND margin_balance IS NOT NULL
        GROUP BY date
    """, start)
    if len(mg) > 20:
        out["margin"] = (mg.pct_change(20) * 100).dropna()

    return {k: v for k, v in out.items() if len(v) >= _MIN_OBS}


def compute_fear_greed(session: Session) -> dict:
    """回傳 {date, score, label, components: [...], history: [...]}；資料不足回 score=None。"""
    latest = session.execute(select(func.max(models.DailyPrice.date))).scalar()
    if latest is None:
        return {"date": None, "score": None, "label": None, "components": [], "history": []}
    if _cache.get("key") == latest:
        return _cache["value"]

    # SQL 聚合視窗：排名窗 252 + 歷史 120 交易日 ≈ 540 日曆天，加緩衝
    start = latest - timedelta(days=620)
    raw = _raw_components(session, start)

    scores = {k: _pct_rank_tail(v, _HISTORY_N) for k, v in raw.items()}
    scores = {k: v for k, v in scores.items() if len(v) > 0}

    result: dict
    if len(scores) < 3:
        result = {"date": latest, "score": None, "label": None, "components": [], "history": []}
    else:
        df = pd.DataFrame(scores)
        composite = df.mean(axis=1, skipna=True)[df.notna().sum(axis=1) >= 3].round(1)
        if composite.empty:
            result = {"date": latest, "score": None, "label": None, "components": [], "history": []}
            _cache["key"], _cache["value"] = latest, result
            return result
        components = []
        for key, (lab, desc) in _COMPONENT_META.items():
            if key not in df.columns or pd.isna(df[key].iloc[-1]):
                continue
            rv = raw[key].iloc[-1]
            # 反向組件還原顯示值（排名時取了負號）
            shown = -rv if key in ("pc_ratio", "volatility") else rv
            components.append({
                "key": key, "label": lab, "desc": desc,
                "score": round(float(df[key].iloc[-1]), 1),
                "value": round(float(shown), 2),
            })
        result = {
            "date": latest,
            "score": float(composite.iloc[-1]),
            "label": label_of(float(composite.iloc[-1])),
            "components": components,
            "history": [
                {"date": d.date(), "score": float(v)} for d, v in composite.items()
            ],
        }

    _cache["key"] = latest
    _cache["value"] = result
    return result
