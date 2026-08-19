"""backend/tests/test_strategies_api.py — CRUD/啟用/404 語意。

沿用 test_multiuser.py 的 TestClient＋登入慣例：monkeypatch auth_enabled
為 True，用 session_scope 直接建已驗證帳號、issue_token 換 session cookie。

核心斷言（五條）：
  1) POST / 建策略 → 200，GET / 看得到
  2) 用戶 B PATCH 用戶 A 的策略 → 404（不是 403）
  3) POST /{sid}/activate 後 GET /active/daily 的 strategy.id == sid
  4) POST /{sid}/backtest body {start,end} 範圍 > 366 天 → 422 或 400
  5) GET /fields 至少含 close/turnover/rev_yoy 三鍵
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
EMAIL_A = f"lab-a-{_SUFFIX}@test.local"
EMAIL_B = f"lab-b-{_SUFFIX}@test.local"

_BASE = "/api/lab/strategies"


@pytest.fixture(scope="module", autouse=True)
def _db():
    init_db()
    yield
    with session_scope() as s:
        users = s.query(models.User).filter(
            models.User.email.in_([EMAIL_A, EMAIL_B])).all()
        ids = [u.id for u in users]
        if ids:
            s.query(models.UserStrategy).filter(
                models.UserStrategy.user_id.in_(ids)).delete(synchronize_session=False)
            for u in users:
                s.delete(u)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "auth_enabled", lambda: True)
    return TestClient(main.app)


def _mk_user(email: str) -> tuple[int, str]:
    """建已驗證帳號，回 (id, session_cookie)。"""
    with session_scope() as s:
        u = s.query(models.User).filter(models.User.email == email).first()
        if u is None:
            u = models.User(email=email, password_hash=auth.hash_password("pw123456"),
                            tier="free", role="user", email_verified_at=datetime.now())
            s.add(u)
            s.flush()
        uid, sv = u.id, u.session_version
    return uid, auth.issue_token(uid, sv)


@pytest.fixture
def two_users():
    a_id, a_tok = _mk_user(EMAIL_A)
    b_id, b_tok = _mk_user(EMAIL_B)
    return {"a": (a_id, a_tok), "b": (b_id, b_tok)}


def _create_strategy(client: TestClient, name: str = "測試策略") -> dict:
    r = client.post(_BASE + "/", json={
        "name": name,
        "conditions": [{"field": "close", "op": "gt", "value": 0}],
        "sort_field": "turnover", "sort_desc": True, "top_n": 30,
        "target_pct": 10.0, "horizon_days": 10, "stop_pct": None,
    })
    assert r.status_code == 200, r.text
    return r.json()


# ── 1) POST / 建策略 → 200，GET / 看得到 ────────────────────


def test_create_then_list(client, two_users):
    client.cookies.set(auth.SESSION_COOKIE, two_users["a"][1])
    created = _create_strategy(client, "策略一")
    assert created["name"] == "策略一"
    assert created["id"] > 0

    r = client.get(_BASE + "/")
    assert r.status_code == 200
    assert any(s["id"] == created["id"] for s in r.json())


# ── 2) 用戶 B PATCH 用戶 A 的策略 → 404（不是 403）────────────


def test_patch_other_users_strategy_is_404(client, two_users):
    client.cookies.set(auth.SESSION_COOKIE, two_users["a"][1])
    created = _create_strategy(client, "A的策略")

    client.cookies.set(auth.SESSION_COOKIE, two_users["b"][1])
    r = client.patch(f"{_BASE}/{created['id']}", json={"name": "hack"})
    assert r.status_code == 404


# ── 3) activate 後 /active/daily 的 strategy.id == sid ────────


def test_activate_then_daily_reflects_active_strategy(client, two_users):
    client.cookies.set(auth.SESSION_COOKIE, two_users["a"][1])
    created = _create_strategy(client, "啟用測試")
    sid = created["id"]

    r = client.post(f"{_BASE}/{sid}/activate")
    assert r.status_code == 200
    assert r.json()["is_active"] is True

    r = client.get(_BASE + "/active/daily")
    assert r.status_code == 200
    body = r.json()
    assert body["strategy"] is not None
    assert body["strategy"]["id"] == sid


# ── 4) 回測範圍 > 366 天 → 422 或 400 ──────────────────────────


def test_backtest_range_over_a_year_is_rejected(client, two_users):
    client.cookies.set(auth.SESSION_COOKIE, two_users["a"][1])
    created = _create_strategy(client, "回測範圍測試")
    sid = created["id"]

    r = client.post(f"{_BASE}/{sid}/backtest", json={
        "start": "2020-01-01", "end": "2022-01-01",
    })
    assert r.status_code in (400, 422)


# ── 5) GET /fields 至少含 close/turnover/rev_yoy 三鍵 ──────────


def test_fields_contains_expected_keys(client, two_users):
    client.cookies.set(auth.SESSION_COOKIE, two_users["a"][1])
    r = client.get(_BASE + "/fields")
    assert r.status_code == 200
    keys = {f["key"] for f in r.json()}
    assert {"close", "turnover", "rev_yoy"} <= keys
