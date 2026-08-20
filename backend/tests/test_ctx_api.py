"""情境路由矩陣端點：/api/ctx-matrix。

矩陣＝data/ctx_matrix.json（挖掘凍結產物）。純觀察層，回傳時過濾掉
tier=="fail" 的格子（保留 pass/watch/insufficient），另附全量 tier 統計。

沿用 test_strategies_api.py 的 TestClient＋登入慣例：monkeypatch auth_enabled
為 True，用 session_scope 直接建已驗證帳號、issue_token 換 session cookie
（本機開發環境 .env 已設 TWA_AUTH_PASSWORD，/api/* 預設就會擋 401，需登入態才能測）。
"""

from __future__ import annotations

import secrets
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import auth, main
from app.storage import models
from app.storage.database import init_db, session_scope

_SUFFIX = secrets.token_hex(4)
EMAIL = f"ctx-api-{_SUFFIX}@test.local"


@pytest.fixture(scope="module", autouse=True)
def _db():
    init_db()
    yield
    with session_scope() as s:
        u = s.query(models.User).filter(models.User.email == EMAIL).first()
        if u:
            s.delete(u)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "auth_enabled", lambda: True)
    return TestClient(main.app)


def _mk_user(email: str) -> tuple[int, str]:
    with session_scope() as s:
        u = s.query(models.User).filter(models.User.email == email).first()
        if u is None:
            u = models.User(email=email, password_hash=auth.hash_password("pw123456"),
                             tier="free", role="user", email_verified_at=datetime.now())
            s.add(u)
            s.flush()
        uid, sv = u.id, u.session_version
    return uid, auth.issue_token(uid, sv)


def test_ctx_matrix_endpoint(client):
    _, tok = _mk_user(EMAIL)
    client.cookies.set(auth.SESSION_COOKIE, tok)

    r = client.get("/api/ctx-matrix")
    assert r.status_code in (200, 404)  # artifact 存在時 200；缺檔時明確 404
    if r.status_code == 200:
        body = r.json()
        assert "cells" in body and "kpi" in body
        assert "x" in body["kpi"]

        counts = body["counts"]
        assert set(counts.keys()) == {"pass", "watch", "insufficient", "fail"}

        assert all(c["tier"] != "fail" for c in body["cells"])

        assert "groups" in body and "signals" in body
        assert isinstance(body["groups"], list) and isinstance(body["signals"], list)
        assert "chain_audit" in body and "generated_at" in body
