"""使用者資料的 scoped 存取層（分層設計 5.2）。

設計原則：讓「不帶 user_id 的查詢」在型別上不可能寫出來。
  - 建構子強制吃 user_id——沒有身分就建構不出這個物件。
  - 所有使用者資料（holdings / transactions / watchlists / watchlist_items）
    的查詢由此組裝，route 層不得對這四張表下裸查詢。
  - 查無資料回 None，route 層translate成 404 而非 403——403 等於告訴攻擊者
    「這個 ID 存在，只是不是你的」。

與 repositories.py 的分工：那邊是 pipeline 的全站表 CRUD（無 per-user 概念），
這邊是使用者資料的 ownership 邊界。兩者刻意不共用基底——「忘了帶 user_id」
在這裡必須是建構失敗，不能是繼承鏈裡的一個可選參數。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models


class UserData:
    def __init__(self, session: Session, user_id: int) -> None:
        if not user_id:
            raise ValueError("UserData 需要 user_id——不存在匿名的使用者資料查詢")
        self.session = session
        self.user_id = user_id

    # ── holdings ─────────────────────────────────────────

    def holdings(self, status: str | None = None) -> list[models.Holding]:
        stmt = select(models.Holding).where(models.Holding.user_id == self.user_id)
        if status is not None:
            stmt = stmt.where(models.Holding.status == status)
        return list(self.session.execute(stmt).scalars().all())

    def holding(self, holding_id: int) -> models.Holding | None:
        return self.session.execute(
            select(models.Holding).where(
                models.Holding.id == holding_id,
                models.Holding.user_id == self.user_id)
        ).scalars().first()

    # ── watchlists ───────────────────────────────────────

    def watchlists(self) -> list[models.Watchlist]:
        return list(self.session.execute(
            select(models.Watchlist).where(models.Watchlist.user_id == self.user_id)
            .order_by(models.Watchlist.id)
        ).scalars().all())

    def watchlist(self, wl_id: int) -> models.Watchlist | None:
        return self.session.execute(
            select(models.Watchlist).where(
                models.Watchlist.id == wl_id,
                models.Watchlist.user_id == self.user_id)
        ).scalars().first()

    def watchlist_item(self, item_id: int) -> models.WatchlistItem | None:
        return self.session.execute(
            select(models.WatchlistItem).where(
                models.WatchlistItem.id == item_id,
                models.WatchlistItem.user_id == self.user_id)
        ).scalars().first()

    # ── strategies（回測實驗室）─────────────────────────

    def strategies(self) -> list[models.UserStrategy]:
        return list(self.session.execute(
            select(models.UserStrategy)
            .where(models.UserStrategy.user_id == self.user_id)
            .order_by(models.UserStrategy.id)
        ).scalars().all())

    def strategy(self, sid: int) -> models.UserStrategy | None:
        return self.session.execute(
            select(models.UserStrategy).where(
                models.UserStrategy.id == sid,
                models.UserStrategy.user_id == self.user_id)
        ).scalars().first()

    def active_strategy(self) -> models.UserStrategy | None:
        return self.session.execute(
            select(models.UserStrategy).where(
                models.UserStrategy.user_id == self.user_id,
                models.UserStrategy.is_active == True)  # noqa: E712
        ).scalars().first()

    def create_strategy(self, **fields) -> models.UserStrategy:
        st = models.UserStrategy(user_id=self.user_id, **fields)
        self.session.add(st)
        self.session.flush()  # 讓呼叫端立刻拿到 id
        return st

    def set_active_strategy(self, sid: int) -> models.UserStrategy | None:
        """啟用 sid、同 user 其他全關。回 None＝非本人策略。"""
        target = self.strategy(sid)
        if target is None:
            return None
        for st in self.strategies():
            st.is_active = st.id == sid
        return target

    def delete_strategy(self, sid: int) -> bool:
        st = self.strategy(sid)
        if st is None:
            return False
        self.session.delete(st)
        return True
