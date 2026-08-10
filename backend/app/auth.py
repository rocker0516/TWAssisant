"""登入驗證（單人網站版）。

改成對外網站後需要登入。設計最小可靠：
  - 帳密存 .env（TWA_AUTH_USERNAME / TWA_AUTH_PASSWORD），start.bat 首次啟動自動產生
  - Session = HMAC-SHA256 簽章 token（user:expiry:sig），存 HttpOnly cookie
  - 簽章密鑰持久化在 data/session_secret（重啟不掉線）
  - 登入失敗鎖定：同 IP 連錯 5 次 → 鎖 60 秒（擋暴力猜密碼）

密碼未設定（TWA_AUTH_PASSWORD 空）→ 視為關閉登入（保留純本機開發模式）。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from .config import settings

SESSION_COOKIE = "twa_session"

_MAX_FAILURES = 5
_LOCKOUT_SECONDS = 60
_failures: dict[str, list[float]] = {}  # ip -> 失敗時間戳


def auth_enabled() -> bool:
    return bool(settings.auth_password)


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


def issue_token(username: str) -> str:
    expiry = int(time.time()) + settings.auth_session_days * 86400
    payload = f"{username}:{expiry}"
    return f"{payload}:{_sign(payload)}"


def verify_token(token: str | None) -> bool:
    if not token:
        return False
    parts = token.rsplit(":", 1)
    if len(parts) != 2:
        return False
    payload, sig = parts
    if not hmac.compare_digest(_sign(payload), sig):
        return False
    try:
        expiry = int(payload.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return False
    return time.time() < expiry


def check_credentials(username: str, password: str) -> bool:
    ok_user = hmac.compare_digest(username, settings.auth_username)
    ok_pass = hmac.compare_digest(password, settings.auth_password)
    return ok_user and ok_pass


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
