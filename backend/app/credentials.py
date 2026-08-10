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
import sys
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
    out = {k: v for k, v in data.get("sources", {}).items() if isinstance(v, str) and v}
    webhook = data.get("notify", {}).get("webhook")
    if isinstance(webhook, str) and webhook:
        out["discord_webhook"] = webhook
    return out


def get_token(name: str) -> str | None:
    """取得來源 token。name 例：'fugle' / 'finmind'。"""
    key = f"{name}_token"
    return _from_keychain(key) or _from_file().get(key)


def get_discord_webhook() -> str | None:
    """Discord 通知 webhook URL（含 token，視為祕密，不入庫）。

    credentials.toml: [notify] webhook = "https://..."；或 Keychain s=discord_webhook。
    """
    return _from_keychain("discord_webhook") or _from_file().get("discord_webhook")


def _write_credentials_file(key: str, token: str) -> None:
    """非 macOS（Windows/Linux）fallback：寫 credentials.toml [sources] 區塊。"""
    data: dict = {}
    if _CREDENTIALS_FILE.exists():
        with _CREDENTIALS_FILE.open("rb") as f:
            data = tomllib.load(f)
    data.setdefault("sources", {})[key] = token
    lines: list[str] = []
    for section, values in data.items():
        lines.append(f"[{section}]")
        for k, v in values.items():
            escaped = str(v).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{k} = "{escaped}"')
        lines.append("")
    _CREDENTIALS_FILE.write_text("\n".join(lines), encoding="utf-8")


def set_token(name: str, token: str) -> None:
    """寫入 Keychain（macOS）；其他平台寫 credentials.toml。設定頁存 token 用。"""
    key = f"{name}_token"
    if sys.platform != "darwin":
        _write_credentials_file(key, token)
        _from_file.cache_clear()
        return
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
