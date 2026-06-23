"""SettingsService（架構⑥）：key-value JSON 設定，含預設值與「即時生效」重算。

配分自由給分、系統自動換算比例（不強制加總 100）。改配分/門檻 → 觸發 Sector+Scoring
當日輕量重算（不重抓資料，秒級）。出場參數即時反映於持股頁（讀取時計算）。
"""

from __future__ import annotations

import copy
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..storage import models

# 預設值（與各引擎內建預設一致）。恢復預設 = 刪該 key 或覆寫成此。
DEFAULTS: dict = {
    "scoring": {
        # 波段軌＝會噴：分數為當天全市場橫截面 rank(2×波動+均線)，無配分可調；
        # top_pct = 進推薦的前 N%（門檻分數 = 100 − top_pct）。
        "wave": {"top_pct": 20},
        "long": {"threshold": 70, "weights": {"profit": 25, "growth": 25, "valuation": 20, "quality": 20, "trend_aux": 10}},
    },
    "sector": {"weights": {"momentum": 35, "fund": 30, "tech": 35}},
    "exit": {
        "wave": {"stop_cap": 8, "trail_trigger": 10, "trail_pullback": 10, "break_ma_exit": True},
        "long": {"stop_cap": 15, "trail_trigger": 20, "trail_pullback": 20, "break_ma_exit": True},
    },
    "layout": {"widgets": ["holdings", "recommendations", "sectors", "events"]},
    "general": {
        "theme": "dark",
        "home": "overview",
        "schedule": {"enabled": True, "time": "21:30"},  # 後端內建每日載入排程
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class SettingsService:
    def get(self, session: Session, key: str) -> dict:
        row = session.get(models.Setting, key)
        stored = row.value if row and isinstance(row.value, dict) else {}
        return _deep_merge(DEFAULTS.get(key, {}), stored)

    def all_effective(self, session: Session) -> dict:
        return {k: self.get(session, k) for k in DEFAULTS}

    def update(self, session: Session, key: str, partial: dict) -> dict:
        row = session.get(models.Setting, key)
        current = row.value if row and isinstance(row.value, dict) else {}
        merged = _deep_merge(current, partial)
        if row is None:
            session.add(models.Setting(key=key, value=merged))
        else:
            row.value = merged
        session.flush()
        return self.get(session, key)

    def reset(self, session: Session, key: str) -> dict:
        row = session.get(models.Setting, key)
        if row is not None:
            session.delete(row)
            session.flush()
        return self.get(session, key)

    def recompute(self, session: Session) -> dict:
        """配分/門檻改動後當日輕量重算（不重抓資料）：Sector → Scoring。"""
        from ..engines.scoring import ScoringEngine
        from ..engines.sector_engine import SectorEngine

        td = session.execute(select(func.max(models.DailyPrice.date))).scalar()
        if td is None:
            return {"status": "no_data"}
        sec = SectorEngine().run(session, td)
        sco = ScoringEngine().run(session, td)
        return {"status": "ok", "date": td.isoformat(), "sector": sec, "scoring": sco}
