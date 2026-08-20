"""狀態變化事件引擎（架構③）：把 scores.passed 的日對日差異寫成 signal_log。

為什麼不讓消費端各自比對兩日快照
  通知、每日盤後、公開戰績要的都是同一件事——「今天誰上榜、誰掉榜」。三方各自
  比對就是三份會各自長 bug 的邏輯。這裡算一次、寫下來、三方共讀。

冪等與 append-only 的取捨（刻意，不是 bug）
  重跑走 insert-ignore：已寫下的事件不會被改寫。若上游資料事後修訂、重跑時的
  名單與當初不同，log 保留的是**當初宣告的內容**。那正是公開戰績需要的語意
  （「我們那天說了什麼」），而不是「用今天的資料回頭看那天該說什麼」。
  代價是 log 可能與重算後的 scores 不一致——這是這張表存在的理由，不是缺陷。
  scores 本身是 upsert 覆寫，重跑會改寫歷史，所以它不能當戰績依據。

首日的處理
  沒有前一個評分日就無法區分「新上榜」與「這是我們第一次看到」。此時寫 0 筆並在
  summary 說明，而不是把全體當成上榜——後者會在回填時的第一天灌出一千筆假事件。

回填的誠實標記
  run(backfilled=True) 寫下的列會標記起來。公開戰績只有 backfilled=False 的部分
  能宣稱「我們事前就說了」；回填段落是用今天的 scores 回推的，只能當背景參考。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..storage import models
from ..storage.repositories import BaseRepository
from .base import BaseEngine

_KEY = ["date", "kind", "stock_id", "track"]

LISTED = "listed"
DELISTED = "delisted"

_NO_CHANGE = {"inserted": 0, "listed": 0, "delisted": 0, "already_logged": 0}


class SignalLogEngine(BaseEngine):
    name = "signal_log"

    def run(self, session: Session, trading_date: date,
            backfilled: bool = False) -> dict:
        prev_date = self._prev_scored_date(session, trading_date)
        if prev_date is None:
            return {**_NO_CHANGE, "note": "無前一個評分日 → 首次觀測，不視為全體上榜"}

        today = self._passed(session, trading_date)
        prev = self._passed(session, prev_date)
        listed = today.keys() - prev.keys()
        delisted = prev.keys() - today.keys()
        if not listed and not delisted:
            return {**_NO_CHANGE, "prev_date": str(prev_date)}

        # 只查受影響的股票。掃全日 scores/daily_prices（每日各數千列）在單日跑無感，
        # 但回填一百多天就會變成分鐘級——而受影響的通常只有數十檔。
        affected = {sid for sid, _ in listed | delisted}
        gone = {sid for sid, _ in delisted}
        closes = self._closes(session, trading_date, affected)
        prev_closes = self._closes(session, prev_date, gone)
        # 掉榜當日若仍有評分列（只是沒過門檻），把當日分數一併記下——「掉到幾分」比
        # 「掉了」有用得多，而這個數字重算 scores 後就會變，只能在事件當下抓。
        scores_now = {
            (r.stock_id, r.track): r.total_score
            for r in session.execute(
                select(models.Score.stock_id, models.Score.track, models.Score.total_score)
                .where(models.Score.date == trading_date,
                       models.Score.stock_id.in_(gone))
            ).all()
        } if gone else {}

        rows: list[dict] = []
        for key in listed:
            stock_id, track = key
            rows.append(self._row(
                trading_date, LISTED, stock_id, track, backfilled,
                self._listed_payload(today[key], closes.get(stock_id))))
        for key in delisted:
            stock_id, track = key
            rows.append(self._row(
                trading_date, DELISTED, stock_id, track, backfilled, {
                    "last_score": prev[key].total_score,      # 掉榜前一日的分數
                    "score": scores_now.get(key),             # 當日分數（沒過門檻；無列則 None）
                    "close": closes.get(stock_id, prev_closes.get(stock_id)),
                    "prev_close": prev_closes.get(stock_id),  # 停牌/下市時 close 會退回這個
                }))

        inserted = BaseRepository(models.SignalLog).insert_ignore_many(
            session, rows, index_elements=_KEY)
        return {
            "inserted": inserted,
            "listed": len(listed),
            "delisted": len(delisted),
            "prev_date": str(prev_date),
            "already_logged": len(rows) - inserted,
        }

    # ── internals ──────────────────────────────────────────────────

    @staticmethod
    def _prev_scored_date(session: Session, trading_date: date) -> date | None:
        """前一個「有評分列」的日期，而非日曆上的前一交易日。

        用評分列而非交易日曆：中間若有某天 pipeline 沒跑完，拿日曆的前一天會查到
        空集合，於是整份名單被判成「今天全部上榜」。以實際有資料的日期為準，
        缺口只會讓變化跨得久一點，不會憑空生出事件。
        """
        return session.execute(
            select(func.max(models.Score.date)).where(models.Score.date < trading_date)
        ).scalar_one_or_none()

    @staticmethod
    def _passed(session: Session, d: date) -> dict[tuple[str, str], models.Score]:
        """該日過門檻的評分列，key = (stock_id, track)。"""
        return {
            (r.stock_id, r.track): r
            for r in session.execute(
                select(models.Score).where(
                    models.Score.date == d, models.Score.passed.is_(True))
            ).scalars()
        }

    @staticmethod
    def _closes(session: Session, d: date, stock_ids: set[str]) -> dict[str, float]:
        if not stock_ids:
            return {}
        return dict(session.execute(
            select(models.DailyPrice.stock_id, models.DailyPrice.close)
            .where(models.DailyPrice.date == d,
                   models.DailyPrice.stock_id.in_(stock_ids))
        ).all())

    @staticmethod
    def _row(d: date, kind: str, stock_id: str, track: str,
             backfilled: bool, payload: dict) -> dict:
        return {"date": d, "kind": kind, "stock_id": stock_id, "track": track,
                "payload": payload, "backfilled": backfilled}

    @staticmethod
    def _listed_payload(score: models.Score, close: float | None) -> dict:
        """上榜當下的推薦卡快照。

        存的是「那天我們給了什麼建議」——買進區間與停損之後重算會變，戰績要對照的
        是當時說的那組數字，所以必須在事件當下凍結。
        """
        return {
            "score": score.total_score,
            "close": close,
            "buy_low": score.buy_low,
            "buy_high": score.buy_high,
            "stop_loss": score.stop_loss,
            "loss_pct": score.loss_pct,
            "passed_styles": score.passed_styles,
            "reasons": score.reasons,
        }
