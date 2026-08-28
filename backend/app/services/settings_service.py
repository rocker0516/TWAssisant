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
        # 長線軌評分已移除（2026-08-28）；存量 JSON 的 scoring.long 讀取時忽略。
    },
    "sector": {"weights": {"momentum": 35, "fund": 30, "tech": 35}},
    "exit": {
        # 波段軌＝論點式出場（Task 4）：目標/期限/停損由 wave_defaults 供新倉套用，
        # reaudit_max＝到期未達標可重審幾次。舊鍵 stop_cap/trail_trigger/trail_pullback
        # 不再出現在此 schema；存量 JSON 若還有這些鍵，讀取時單純忽略（set_config 只認列出的鍵）。
        # stop_pct=None＝波段軌預設**不設停損**（2026-08-24；見 engines/stoploss.py docstring）：
        # −8% 停損實測讓命中率掉 25pp，風控改由 horizon_days 到期承擔。想要停損就在這裡填數字。
        "wave_defaults": {"target_pct": 10.0, "horizon_days": 10, "stop_pct": None},
        "reaudit_max": 2,
        # long：只服務既有 track='long' 持倉的出場照顧（推薦/評分已移除）。
        # score_slip_warn 已刪——ScoreSlipSignal 依賴的每日長線 Score 停產。
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
