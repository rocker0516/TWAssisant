"""每日盤後 pipeline（架構⑤）。

PipelineStep 抽象（name/required/run），DailyPipeline runner 逐 step try：
  - required 失敗 → 中斷 + 記錄（通知於 NotifyStep，P2 接）
  - 非 required 失敗 → 續跑
每次執行寫一筆 PipelineRun log（設定頁顯示 / 啟動 catch-up 判斷資料是否齊）。

冪等性是核心：整條可重跑（增量補缺 + upsert 覆寫），故關機補跑安全。
後續階段只需把新 Step（Indicator/Sector/News/Scoring/Exit/LLM/Notify）加進清單。
"""

from __future__ import annotations

import time
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.orm import Session

from ..storage.database import session_scope
from ..storage.models import PipelineRun


@dataclass
class PipelineContext:
    """step 之間共享狀態。"""

    trading_date: date
    session: Session
    shared: dict = field(default_factory=dict)  # 如 universe 股票清單，供後續 step 共用


class PipelineStep(ABC):
    name: str = "step"
    required: bool = True

    @abstractmethod
    def run(self, ctx: PipelineContext) -> dict:
        """執行並回 summary dict（寫入 PipelineRun.steps）。失敗請 raise。"""


class DailyPipeline:
    def __init__(self, steps: list[PipelineStep]) -> None:
        self.steps = steps

    def run(self, trading_date: date) -> dict:
        started = time.time()
        step_results: list[dict] = []
        overall_status = "success"
        error: str | None = None

        with session_scope() as session:
            run_row = PipelineRun(
                trading_date=trading_date, status="running", started_at=datetime.now()
            )
            session.add(run_row)
            session.flush()
            ctx = PipelineContext(trading_date=trading_date, session=session)

            # 每個 step 結束後 commit：釋放 SQLite writer 鎖，讓使用者「買進/賣出」
            # 等其他寫入能在 step 之間擠進來；step 本身就是冪等 upsert/增量補缺，
            # 中途失敗也不會壞資料（檔頭設計目標明示「整條可重跑」）。
            session.flush()  # 先把 PipelineRun(running) 落地
            session.commit()
            for step in self.steps:
                t0 = time.time()
                try:
                    summary = step.run(ctx)
                    step_results.append(
                        {
                            "name": step.name,
                            "status": "ok",
                            "seconds": round(time.time() - t0, 1),
                            **(summary or {}),
                        }
                    )
                except Exception as exc:  # noqa: BLE001 — runner 要吞例外決定中斷與否
                    msg = f"{exc.__class__.__name__}: {exc}"
                    step_results.append(
                        {
                            "name": step.name,
                            "status": "failed",
                            "seconds": round(time.time() - t0, 1),
                            "error": msg,
                            "required": step.required,
                        }
                    )
                    traceback.print_exc()
                    session.rollback()  # 把失敗 step 的部份寫入退掉，避免半成品
                    if step.required:
                        overall_status = "failed"
                        error = f"required step '{step.name}' 失敗：{msg}"
                        break  # required 失敗 → 中斷
                else:
                    session.commit()  # 釋放 writer 鎖，使用者交易能擠入

            # session_scope 的 sessionmaker 設 expire_on_commit=False，
            # commit 後 ORM 屬性仍可賦值；最後一次 commit 由 session_scope 收尾。
            run_row.steps = step_results
            run_row.status = overall_status
            run_row.error = error
            run_row.finished_at = datetime.now()

        return {
            "trading_date": trading_date.isoformat(),
            "status": overall_status,
            "seconds": round(time.time() - started, 1),
            "steps": step_results,
            "error": error,
        }
