"""登入牆回歸測試：Accept header 不得成為授權依據。

歷史漏洞：`_require_login` 以 `"text/html" in Accept` 放行所有 GET，
使得 `curl -H "Accept: text/html" /api/recommendations` 可在無 session 下
讀取全部 API 資料（start.bat 綁 0.0.0.0 → 同網段任何裝置皆可）。
修法：改以「路徑非 /api」判斷 SPA 殼，Accept 只是輔助條件。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import auth, main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "auth_enabled", lambda: True)
    return TestClient(main.app)


# 涵蓋數個 router，避免只修好單一路徑就通過。
API_PATHS = ["/api/recommendations", "/api/holdings", "/api/settings", "/api/flow/alerts"]

# 客戶端可任意偽造的 Accept 值 —— 每一種都不該換到資料。
ACCEPT_HEADERS = [
    {"Accept": "text/html"},
    {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9"},
    {"Accept": "*/*"},
    {},
]


@pytest.mark.parametrize("headers", ACCEPT_HEADERS)
@pytest.mark.parametrize("path", API_PATHS)
def test_api_requires_session_regardless_of_accept(client, path, headers):
    assert client.get(path, headers=headers).status_code == 401


@pytest.mark.parametrize("headers", ACCEPT_HEADERS)
@pytest.mark.parametrize("path", API_PATHS)
def test_api_blocked_even_in_spa_mode(client, monkeypatch, path, headers):
    """打包模式下殼放行生效，但仍不得外溢到 /api/*。"""
    monkeypatch.setattr(main, "_SPA_MODE", True)
    assert client.get(path, headers=headers).status_code == 401


def test_spa_shell_reachable_without_session(client, monkeypatch):
    """非 /api 的 GET 導覽仍須放行，前端 router 才能自己導去 /login。

    未打包時該路徑沒有 handler（404），打包時回 index.html（200）—— 兩者都證明
    登入牆放行了；只要不是 401 即可，不綁定是否已 build 前端。
    """
    monkeypatch.setattr(main, "_SPA_MODE", True)
    assert client.get("/login", headers={"Accept": "text/html"}).status_code != 401


def test_login_endpoint_stays_exempt(client):
    """/auth/* 必須維持免驗證，否則沒人登得進來。"""
    resp = client.post("/api/auth/login", json={"username": "x", "password": "y"})
    assert resp.status_code != 401 or resp.json().get("reason")
