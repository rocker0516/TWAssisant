"""每日盤後排程進入點。

launchd 每天 21:30 直接跑：`python -m app.scheduler.run`（不需後端常開）。
冪等 → 關機後補跑安全。手動回補：`python -m app.scheduler.run --date 2026-06-05`。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime

from ..storage.database import init_db
from .pipeline import DailyPipeline
from .steps import (
    AttentionStep,
    CornerStep,
    ExitStep,
    FetchStep,
    IndicatorStep,
    MLConsensusStep,
    NewsStep,
    NotifyStep,
    PoppableEfficacyStep,
    ScoringStep,
    SectorStep,
    TargetPriceStep,
)
from .trading_calendar import is_trading_day, resolve_trading_date


def build_pipeline() -> DailyPipeline:
    # 依賴順序：Fetch→Indicator→Sector→News→Scoring→Exit→Notify→PoppableEfficacy。
    # LLM 翻白話不在 pipeline：改端點首讀懶生成（llm/lazy.py），只為看的內容花呼叫。
    # 會噴成效回測放最後：純歷史回測、非必要，掛了不影響當日推薦/通知。
    return DailyPipeline(
        steps=[
            FetchStep(), IndicatorStep(), SectorStep(), NewsStep(), TargetPriceStep(),
            AttentionStep(),
            ScoringStep(), MLConsensusStep(), CornerStep(), ExitStep(), NotifyStep(),
            PoppableEfficacyStep(),
        ]
    )


def build_backfill_pipeline() -> DailyPipeline:
    """多日補洞用：跑到 Exit 為止，不含通知/回測。

    補一段缺口時，每個「非最新」交易日都跑這條（資料+指標+類股+消息+評分+出場齊全、
    推薦可用），但**不發 Discord 通知、不跑成效回測**——避免一次補 N 天就轟 N 則通知。
    最新那天才跑完整 build_pipeline()。
    """
    return DailyPipeline(
        steps=[
            FetchStep(), IndicatorStep(), SectorStep(), NewsStep(), TargetPriceStep(),
            AttentionStep(),
            ScoringStep(), MLConsensusStep(), CornerStep(), ExitStep(),
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TWAssistant 每日盤後 pipeline")
    parser.add_argument("--date", help="指定交易日 YYYY-MM-DD（回補用）")
    parser.add_argument("--force", action="store_true", help="非交易日也強制執行")
    args = parser.parse_args(argv)

    init_db()

    if args.date:
        target = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        today = date.today()
        if not is_trading_day(today) and not args.force:
            print(f"[skip] {today} 非交易日，休市跳過。")
            return 0
        target = resolve_trading_date(today)

    print(f"[run] pipeline 目標交易日 = {target}")
    result = build_pipeline().run(target)

    print(f"[done] status={result['status']} 用時={result['seconds']}s")
    for step in result["steps"]:
        print(f"   - {step['name']}: {step['status']} ({step.get('seconds')}s)")
        if step["name"] == "fetch":
            for ds, info in step.get("datasets", {}).items():
                print(f"       · {ds}: {info}")
    if result["error"]:
        print(f"[error] {result['error']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
