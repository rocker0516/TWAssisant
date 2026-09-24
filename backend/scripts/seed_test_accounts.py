"""建立／重置各層級測試帳號（冪等，可重跑）。

用法（backend/ 下）：
    .venv/Scripts/python.exe scripts/seed_test_accounts.py

Admin 不在此列——它由 .env 的 TWA_AUTH_USERNAME/PASSWORD 在啟動遷移時
bootstrap（email = 帳號名@local.twa），密碼以 .env 為準。

僅供開發／測試環境。上線前記得刪除這些帳號（email 都在 @test.twa 網域下，
一條 DELETE 就清得掉）。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import auth  # noqa: E402
from app.storage import models  # noqa: E402
from app.storage.database import init_db, session_scope  # noqa: E402

ACCOUNTS = [
    # email             密碼               tier    role
    ("free@test.twa", "free-test-1234", "free", "user"),
    ("pro@test.twa",  "pro-test-1234",  "pro",  "user"),
]


def main() -> None:
    init_db()
    with session_scope() as s:
        for email, pw, tier, role in ACCOUNTS:
            u = s.query(models.User).filter(models.User.email == email).first()
            if u is None:
                s.add(models.User(
                    email=email, tier=tier, role=role,
                    password_hash=auth.hash_password(pw),
                    email_verified_at=datetime.now()))
                print(f"created {email} ({tier})")
            else:
                u.tier, u.role = tier, role
                u.password_hash = auth.hash_password(pw)
                u.email_verified_at = u.email_verified_at or datetime.now()
                u.session_version += 1  # 重置密碼順便踢掉舊 session
                print(f"reset   {email} ({tier})")
        admin = s.query(models.User).filter(models.User.role == "admin").first()
        print(f"admin:  {admin.email if admin else '（尚未 bootstrap，設定 .env 後啟動一次即建立）'}")


if __name__ == "__main__":
    main()
