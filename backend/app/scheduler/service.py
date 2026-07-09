"""後端內建排程（架構⑤備援）：FastAPI 常開時自動跑每日 pipeline。

設計目標 = 「都在瀏覽器」：使用者開後端、用網頁，不再進終端機跑 script。
- 啟動 catch-up：若「應已完成的最近交易日」沒有成功紀錄 → 背景立刻補跑。
- 每天設定時間（預設 21:30、可在設定頁改）以 APScheduler cron 觸發，非交易日跳過。
- 全程共用一把鎖：手動觸發 / 排程 / 補跑互斥，避免 SQLite 並寫打架。

冪等性照舊（增量補缺 + upsert），重跑安全。
"""

from __future__ import annotations

import threading
from datetime import date, datetime, time as dtime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func, select

from ..storage import models
from ..storage.database import session_scope
from .run import build_backfill_pipeline, build_pipeline
from .trading_calendar import is_trading_day, previous_trading_day, resolve_trading_date

_JOB_ID = "daily_pipeline"
_lock = threading.Lock()
_BACKFILL_CAP = 15  # 一次補洞最多回溯交易日數（安全上限，避免久未開機一次暴衝）


def is_running() -> bool:
    """目前是否有 pipeline 在跑（手動/排程/補跑任一）。"""
    if _lock.acquire(blocking=False):
        _lock.release()
        return False
    return True


def run_pipeline_guarded(target: date | None = None, *, trigger: str = "manual") -> dict:
    """加鎖跑一次 pipeline；若已有人在跑則略過（回 skipped）。"""
    if not _lock.acquire(blocking=False):
        return {"status": "skipped", "reason": "already_running"}
    try:
        tgt = target or resolve_trading_date(date.today())
        print(f"[scheduler] pipeline 開始（{trigger}）target={tgt}")
        result = build_pipeline().run(tgt)
        print(f"[scheduler] pipeline 結束（{trigger}）status={result['status']}")
        return result
    finally:
        _lock.release()


def _current_sched_time() -> dtime:
    """讀目前排程時間（決定 _expected_ready_date 是否把『今天』算進可抓範圍）。"""
    from ..services.settings_service import SettingsService

    with session_scope() as s:
        g = SettingsService().get(s, "general")
    return _parse_time(((g or {}).get("schedule") or {}).get("time", "21:30"))


def _last_score_date() -> date | None:
    with session_scope() as s:
        return s.execute(select(func.max(models.Score.date))).scalar()


def _missing_trading_days(target: date, last_done: date | None) -> list[date]:
    """(last_done, target] 之間的所有交易日，升冪；冷啟動(last_done=None)只回最新一天。

    取最近 _BACKFILL_CAP 天為安全上限。target 本身一定是交易日（_expected_ready_date 保證）。
    """
    if last_done is None:
        return [target]
    days: list[date] = []
    d = last_done + timedelta(days=1)
    while d <= target:
        if is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)
    return days[-_BACKFILL_CAP:]


def backfill_to_latest(*, trigger: str = "manual") -> dict:
    """補齊「所有缺的交易日（含分數）」到最新——設定頁「立即載入」用。

    - target = 目前理應已完成的最近交易日（盤前/未到排程時間 → 上一交易日，不抓還沒齊的當天）。
    - 逐日（升冪）跑 pipeline：最新那天跑完整（含通知/回測），其餘天跑精簡版（到 Exit、
      不重複通知）。每天各寫一筆 PipelineRun（設定頁可見各步驟燈號）。
    - 沒有缺口時 → 仍重跑 target 一次當刷新。fetch 為增量、indicator 全量重算，故整段冪等可重跑。
    - 全程持鎖一次，與排程/補跑互斥。
    """
    if not _lock.acquire(blocking=False):
        return {"status": "skipped", "reason": "already_running"}
    try:
        target = _expected_ready_date(datetime.now(), _current_sched_time())
        last = _last_score_date()
        days = _missing_trading_days(target, last) if (last is None or last < target) else []
        # 沒缺口 → 仍重跑 target 一次當「刷新」，但走精簡版（不重發通知）。
        refresh_only = not days
        if refresh_only:
            days = [target]
        print(
            f"[scheduler] backfill（{trigger}）target={target} "
            f"{'刷新' if refresh_only else '待補'}={[d.isoformat() for d in days]}"
        )
        per_day: list[dict] = []
        for i, d in enumerate(days):
            # 只有「真的新補進來的最新交易日」才跑完整版（含通知/回測）；
            # 其餘日與純刷新走精簡版，避免補多天/重按時轟 Discord。
            full = (i == len(days) - 1) and not refresh_only
            r = (build_pipeline() if full else build_backfill_pipeline()).run(d)
            per_day.append({"date": d.isoformat(), "status": r["status"], "seconds": r["seconds"]})
        ok = sum(1 for r in per_day if r["status"] == "success")
        return {
            "status": "ok", "trigger": trigger, "target": target.isoformat(),
            "refresh_only": refresh_only, "days": per_day, "count": len(days), "succeeded": ok,
        }
    finally:
        _lock.release()


def _has_success(target: date) -> bool:
    with session_scope() as s:
        row = s.execute(
            select(models.PipelineRun.id)
            .where(
                models.PipelineRun.trading_date == target,
                models.PipelineRun.status == "success",
            )
            .limit(1)
        ).first()
        return row is not None


def _parse_time(s: str | None) -> dtime:
    try:
        hh, mm = str(s).split(":")
        return dtime(int(hh), int(mm))
    except Exception:
        return dtime(21, 30)


def _expected_ready_date(now: datetime, sched: dtime) -> date:
    """目前時間點「理應已完成」的最近交易日：
    今天是交易日且已過排程時間 → 今天；否則 → 上一個交易日。
    （避免在盤中開後端就去抓當天還沒齊的資料。）
    """
    today = now.date()
    if is_trading_day(today) and now.time() >= sched:
        return today
    return previous_trading_day(today)


class PipelineScheduler:
    def __init__(self) -> None:
        self._sched = BackgroundScheduler(timezone="Asia/Taipei")
        self._enabled = True
        self._time = dtime(21, 30)

    def _cfg_from_general(self, general: dict) -> tuple[bool, dtime]:
        sc = (general or {}).get("schedule") or {}
        return bool(sc.get("enabled", True)), _parse_time(sc.get("time", "21:30"))

    def _load_cfg(self) -> tuple[bool, dtime]:
        from ..services.settings_service import SettingsService

        with session_scope() as s:
            g = SettingsService().get(s, "general")
        return self._cfg_from_general(g)

    def _apply_job(self) -> None:
        try:
            self._sched.remove_job(_JOB_ID)
        except Exception:  # noqa: BLE001 — job 不存在時 remove 會丟，忽略即可
            pass
        if self._enabled:
            self._sched.add_job(
                self._scheduled_run,
                "cron",
                hour=self._time.hour,
                minute=self._time.minute,
                id=_JOB_ID,
                replace_existing=True,
                misfire_grace_time=3600,  # 排程時間點若卡住，1 小時內補觸發
            )
            print(f"[scheduler] 每日排程已設定 {self._time.strftime('%H:%M')}")
        else:
            print("[scheduler] 每日排程已停用")

    def _scheduled_run(self) -> None:
        today = date.today()
        if not is_trading_day(today):
            print(f"[scheduler] {today} 非交易日，排程跳過")
            return
        run_pipeline_guarded(resolve_trading_date(today), trigger="schedule")

    def _catch_up(self) -> None:
        target = _expected_ready_date(datetime.now(), self._time)
        if _has_success(target):
            print(f"[scheduler] catch-up：{target} 已有成功紀錄，略過")
            return
        print(f"[scheduler] catch-up：{target} 尚無資料，背景補跑")
        run_pipeline_guarded(target, trigger="catchup")

    def start(self) -> None:
        self._enabled, self._time = self._load_cfg()
        self._sched.start()
        self._apply_job()
        if self._enabled:
            threading.Thread(target=self._catch_up, daemon=True).start()

    def reschedule(self, general: dict | None = None) -> dict:
        """設定頁改排程後呼叫並重排。

        傳入剛算好的 general（含尚未 commit 的新值）以避免讀到舊交易；
        未傳則自 DB 重讀（如啟動或外部呼叫）。
        """
        self._enabled, self._time = (
            self._cfg_from_general(general) if general is not None else self._load_cfg()
        )
        self._apply_job()
        return {"enabled": self._enabled, "time": self._time.strftime("%H:%M")}

    def shutdown(self) -> None:
        try:
            self._sched.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            pass


_scheduler: PipelineScheduler | None = None


def get_scheduler() -> PipelineScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = PipelineScheduler()
    return _scheduler
