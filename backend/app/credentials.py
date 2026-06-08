"""資料來源憑證讀取。

設計（架構補強定案）：token 不混進 settings 表，存 macOS Keychain，
備份 DB 也不會洩漏。讀取優先序：
    1. macOS Keychain（service=twassistant, account=<name>_token）
    2. backend/credentials.toml（已被 .gitignore 排除）
    3. None（呼叫端 fallback：FetchStep 標來源未設定、不中斷其他來源）

設定頁（P6）會透過 PUT /settings/sources 寫入 Keychain；P0 先手動填
credentials.toml 或用 `security add-generic-password` 寫 Keychain。
"""

from __future__ import annotations

import subprocess
import tomllib
from functools import lru_cache

from .config import BACKEND_DIR

_KEYCHAIN_SERVICE = "twassistant"
_KEYCHAIN_ACCOUNT = "twassistant"
_CREDENTIALS_FILE = BACKEND_DIR / "credentials.toml"


def _from_keychain(key: str) -> str | None:
    """從 macOS Keychain 讀 generic password。非 mac 或不存在回 None。"""
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-a",
                _KEYCHAIN_ACCOUNT,
                "-s",
                key,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    token = result.stdout.strip()
    return token or None


@lru_cache
def _from_file() -> dict[str, str]:
    if not _CREDENTIALS_FILE.exists():
        return {}
    with _CREDENTIALS_FILE.open("rb") as f:
        data = tomllib.load(f)
    sources = data.get("sources", {})
    return {k: v for k, v in sources.items() if isinstance(v, str) and v}


def get_token(name: str) -> str | None:
    """取得來源 token。name 例：'fugle' / 'finmind'。"""
    key = f"{name}_token"
    return _from_keychain(key) or _from_file().get(key)


def set_token(name: str, token: str) -> None:
    """寫入 Keychain（覆寫既有）。設定頁存 token 用。"""
    key = f"{name}_token"
    subprocess.run(
        [
            "security",
            "add-generic-password",
            "-a",
            _KEYCHAIN_ACCOUNT,
            "-s",
            key,
            "-w",
            token,
            "-U",  # update if exists
        ],
        check=True,
        timeout=5,
    )
    _from_file.cache_clear()
