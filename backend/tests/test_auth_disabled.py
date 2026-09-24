"""TWA_AUTH_DISABLED 開關：有密碼也能明確關掉登入牆（本機自用模式）。

開關存在的理由：清空 TWA_AUTH_PASSWORD 在 PowerShell 裡等於刪變數，
pydantic-settings 會回頭讀 .env 的密碼，登入牆關不掉。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import auth, main


@pytest.mark.parametrize(
    ("password", "disabled", "expected"),
    [
        ("secret", False, True),   # 正常網站模式
        ("secret", True, False),   # 本 feature：有密碼仍可關
        ("", False, False),        # 舊行為：沒密碼 = 關
        ("", True, False),
    ],
)
def test_auth_enabled_truth_table(monkeypatch, password, disabled, expected):
    monkeypatch.setattr(auth.settings, "auth_password", password)
    monkeypatch.setattr(auth.settings, "auth_disabled", disabled)
    assert auth.auth_enabled() is expected


@pytest.fixture
def open_client(monkeypatch):
    monkeypatch.setattr(auth.settings, "auth_password", "secret")
    monkeypatch.setattr(auth.settings, "auth_disabled", True)
    return TestClient(main.app)


def test_api_open_without_cookie(open_client):
    me = open_client.get("/api/auth/me").json()
    assert me == {"authenticated": True, "auth_enabled": False}
    assert open_client.get("/api/holdings").status_code == 200


@pytest.mark.parametrize("path", ["/login", "/signup", "/forgot"])
def test_entry_forms_redirect_to_app(open_client, path):
    r = open_client.get(path, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/app"


def test_public_sidebar_links_to_app(open_client):
    html = open_client.get("/stocks").text
    assert 'href="/app">進入 App' in html
    assert 'href="/login"' not in html
