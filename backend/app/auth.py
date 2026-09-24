"""登入驗證（多用戶版，分層設計 7.2）。

單人版 → 多用戶版的三個升級：
  - 密碼：明文存 .env → scrypt 雜湊存 users 表。選 scrypt 而非 bcrypt 是因為
    它在 hashlib 標準庫裡（記憶體硬、抗 GPU），專案零新依賴的慣例得以維持。
    .env 帳密仍在：啟動時 bootstrap 成第一個 admin（database._migrate_multiuser）。
  - Session：token 從「user:expiry:sig」改「uid:sv:expiry:sig」。sv=簽發當下的
    users.session_version，驗證時與 DB 比對——改密碼／登出全部裝置把 sv+1，
    所有舊 token 立即失效。這是可撤銷 session 的最小實作：不用存 token 名單。
  - 鎖定：in-memory per-IP（第一道，擋單點暴力）＋ DB per-account（第二道，
    擋分散 IP、重啟不歸零；設計 7.2-3）。

TWA_AUTH_PASSWORD 未設 = 關閉登入（純本機開發模式），所有請求以 bootstrap
的 dev admin 身分行動——scoped repository 仍拿得到 user_id，程式碼不分岔。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings

SESSION_COOKIE = "twa_session"

_MAX_FAILURES = 5
_LOCKOUT_SECONDS = 60          # per-IP（in-memory）
_ACCOUNT_LOCK_MINUTES = 15     # per-account（DB）
_failures: dict[str, list[float]] = {}  # ip -> 失敗時間戳

# scrypt 參數：n=2^14 為互動式登入的常見建議值（~16MB 記憶體、數十 ms）
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 16384, 8, 1


def auth_enabled() -> bool:
    return bool(settings.auth_password)


# ── 密碼雜湊 ──────────────────────────────────────────────


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt,
                            n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                                n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, AttributeError):
        return False


# ── Session token ────────────────────────────────────────


def _secret() -> bytes:
    """簽章密鑰：data/session_secret，無則生成。"""
    path = settings.data_dir / "session_secret"
    if path.exists():
        return path.read_bytes()
    key = secrets.token_bytes(32)
    path.write_bytes(key)
    return key


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()


def issue_token(user_id: int, session_version: int) -> str:
    expiry = int(time.time()) + settings.auth_session_days * 86400
    payload = f"{user_id}:{session_version}:{expiry}"
    return f"{payload}:{_sign(payload)}"


def parse_token(token: str | None) -> tuple[int, int] | None:
    """驗簽 + 時效。回 (user_id, session_version)；session_version 是否仍有效
    要再對 DB（resolve_user）——簽章只證明「本站簽發過」，不證明「還沒撤銷」。
    """
    if not token:
        return None
    parts = token.rsplit(":", 1)
    if len(parts) != 2:
        return None
    payload, sig = parts
    if not hmac.compare_digest(_sign(payload), sig):
        return None
    try:
        uid, sv, expiry = payload.split(":")
        if time.time() >= int(expiry):
            return None
        return int(uid), int(sv)
    except ValueError:
        return None  # 舊版 token（user:expiry）也落在這：一律重新登入


def resolve_user(session: Session, token: str | None):
    """token → User（含撤銷檢查）。回 None = 未登入/已失效。"""
    from .storage import models

    parsed = parse_token(token)
    if parsed is None:
        return None
    uid, sv = parsed
    user = session.get(models.User, uid)
    if user is None or user.session_version != sv:
        return None
    return user


def dev_user(session: Session):
    """登入關閉（開發模式）時的行動身分：第一個帳號（bootstrap 的 dev admin）。"""
    from .storage import models

    return session.execute(
        select(models.User).order_by(models.User.id).limit(1)
    ).scalars().first()


# ── 鎖定：per-IP（in-memory 第一道）──────────────────────


def is_locked_out(ip: str) -> int:
    """回傳剩餘鎖定秒數；0 = 未鎖。"""
    now = time.time()
    recent = [t for t in _failures.get(ip, []) if now - t < _LOCKOUT_SECONDS]
    _failures[ip] = recent
    if len(recent) >= _MAX_FAILURES:
        return int(_LOCKOUT_SECONDS - (now - recent[0])) + 1
    return 0


def record_failure(ip: str) -> None:
    _failures.setdefault(ip, []).append(time.time())


def clear_failures(ip: str) -> None:
    _failures.pop(ip, None)


# ── 鎖定：per-account（DB 第二道）────────────────────────


def account_locked_minutes(user) -> int:
    """剩餘鎖定分鐘數；0 = 未鎖。"""
    if user.locked_until and user.locked_until > datetime.now():
        return int((user.locked_until - datetime.now()).total_seconds() // 60) + 1
    return 0


def record_account_failure(user) -> None:
    user.failed_logins = (user.failed_logins or 0) + 1
    if user.failed_logins >= _MAX_FAILURES:
        user.locked_until = datetime.now() + timedelta(minutes=_ACCOUNT_LOCK_MINUTES)
        user.failed_logins = 0


def clear_account_failures(user) -> None:
    user.failed_logins = 0
    user.locked_until = None
