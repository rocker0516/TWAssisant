"""一次性：用最新關鍵字規則重新分類既有 events.category。

NewsEngine 落庫採 on_conflict_do_nothing（重跑不更新既有列），故新增「展望」
類別後，舊列仍停在原本的 題材/中性。本腳本對「非利空」列重跑 classify()，
把命中展望關鍵字者升級為展望。

刻意只動 is_risk=False 的列：利空標記供 ExitEngine 的 NewsRiskSignal 使用，
不重新判定以免擾動出場訊號（負向前瞻如「財測下修」本就該留在利空）。

用法：
    cd backend
    python -m scripts.reclassify_events           # 實際寫入
    python -m scripts.reclassify_events --dry      # 只統計不寫入
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from app.engines.news_engine import classify
from app.storage import models
from app.storage.database import SessionLocal


def main(dry: bool) -> None:
    session = SessionLocal()
    try:
        rows = session.execute(
            select(models.Event).where(models.Event.is_risk.is_(False))
        ).scalars().all()

        changed = 0
        by_target: dict[str, int] = {}
        for ev in rows:
            cat, is_risk = classify(ev.title, ev.summary)
            # 安全網：classify 若回利空（理論上 is_risk=False 不會），跳過不動標記
            if is_risk or cat == ev.category:
                continue
            by_target[cat] = by_target.get(cat, 0) + 1
            if not dry:
                ev.category = cat
            changed += 1

        if not dry:
            session.commit()
        verb = "可更新" if dry else "已更新"
        print(f"掃描非利空事件 {len(rows)} 筆，{verb} {changed} 筆")
        for cat, n in sorted(by_target.items(), key=lambda kv: -kv[1]):
            print(f"  → {cat}: {n}")
    finally:
        session.close()


if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
