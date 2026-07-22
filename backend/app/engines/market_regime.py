"""大盤 regime 燈（MA60 遲滯）——會噴推薦的市場閘門。

規則（與 scripts/pop_regime_gate.py 驗證同一套）：
  持有中：加權指數收盤跌破 MA60 逾 2% → 轉防禦（空手）
  防禦中：收盤站回 MA60 → 轉持有
狀態從全歷史 point-in-time 推演（遲滯有路徑依賴，不能只看當天）。

驗證（2021~2026 walk-forward、260 週頻進場日）：
  日層級（每進場日=1觀測，同日個股命中相關、此為誠實口徑）：
    防禦日清單摸+10% 42.1% vs 持有日 46.7%（Welch t=1.83，邊緣顯著）
  期望值小幅為正的弱訊號：約半數防禦日事後看正常（左尾拖出差距），
  閘門定位=保守偏誤而非預知；文案務必用日層級數字、不可誇大確定性。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.storage import models

_GAP = 0.02          # 遲滯帶：跌破 MA60 逾 2% 才出、站回 MA60 才進
_MA_N = 60
# 驗證常數（scripts/pop_regime_gate.py 2026-07-22，日層級口徑）：兩態的清單摸+10% 機率
_HOLD_HIT = 0.467
_DEFENSE_HIT = 0.421


def wave_market_regime(session: Session) -> dict | None:
    """回傳 {state, date, since, close, ma60, gap_pct, hold_hit_rate, defense_hit_rate}；資料不足回 None。"""
    rows = session.execute(
        select(models.MarketIndex.date, models.MarketIndex.close)
        .order_by(models.MarketIndex.date)
    ).all()
    series = [(d, float(c)) for d, c in rows if c is not None]
    if len(series) <= _MA_N:
        return None

    closes = [c for _, c in series]
    run = sum(closes[:_MA_N])
    held = True
    since = series[_MA_N - 1][0]
    ma60 = run / _MA_N
    for i in range(_MA_N, len(series)):
        run += closes[i] - closes[i - _MA_N]
        ma60 = run / _MA_N
        c = closes[i]
        new = (c > ma60 * (1.0 - _GAP)) if held else (c > ma60)
        if new != held:
            held = new
            since = series[i][0]
    d, c = series[-1]
    return {
        "state": "hold" if held else "defense",
        "date": d,
        "since": since,
        "close": round(c, 2),
        "ma60": round(ma60, 2),
        "gap_pct": round((c / ma60 - 1.0) * 100.0, 2),
        "hold_hit_rate": _HOLD_HIT,
        "defense_hit_rate": _DEFENSE_HIT,
    }
