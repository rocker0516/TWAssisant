"""多租戶隔離＋身分系統測試（分層設計第 8 節的第一波斷言）。

涵蓋：
  - 水平越權（IDOR）：A 存取 B 的資源 ID → 404（且不是 403——403 洩漏存在性）
  - 註冊 → Email 驗證 → 登入 全流程（含未驗證擋登入）
  - Session 撤銷：session_version +1 後舊 token 立即失效
  - UserData 無 user_id 不可建構

測試跑在開發 DB 上（與既有測試同慣例），所有建立的列在 teardown 清掉；
email 帶隨機字尾避免與真帳號相撞。
"""

from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient

from app import auth, main
from app.storage import models
from app.storage.database import init_db, session_scope
from app.storage.user_data import UserData

_SUFFIX = secrets.token_hex(4)
EMAIL_A = f"idor-a-{_SUFFIX}@test.local"
EMAIL_B = f"idor-b-{_SUFFIX}@test.local"
EMAIL_SIGNUP = f"signup-{_SUFFIX}@test.local"


@pytest.fixture(scope="module", autouse=True)
def _db():
    init_db()
    yield
    with session_scope() as s:
        users = s.query(models.User).filter(
            models.User.email.in_([EMAIL_A, EMAIL_B, EMAIL_SIGNUP])).all()
        ids = [u.id for u in users]
        if ids:
            for model in (models.Transaction, models.Holding,
                          models.WatchlistItem, models.Watchlist):
                s.query(model).filter(model.user_id.in_(ids)).delete(
                    synchronize_session=False)
            for model in (models.EmailVerification, models.PasswordReset):
                s.query(model).filter(model.user_id.in_(ids)).delete(
                    synchronize_session=False)
            for u in users:
                s.delete(u)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "auth_enabled", lambda: True)
    return TestClient(main.app)


def _mk_user(email: str) -> tuple[int, str]:
    """建已驗證帳號，回 (id, session_cookie)。"""
    from datetime import datetime

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
    with session_scope() as s:
        h = models.Holding(user_id=a_id, stock_id=_any_stock(s), track="wave",
                           status="open")
        wl = models.Watchlist(user_id=a_id, name="idor-test")
        s.add_all([h, wl])
        s.flush()
        holding_id, wl_id = h.id, wl.id
    return {"a": (a_id, a_tok), "b": (b_id, b_tok),
            "holding_id": holding_id, "wl_id": wl_id}


def _any_stock(s) -> str:
    sid = s.query(models.Stock.id).first()
    if sid:
        return sid[0]
    s.add(models.Stock(id="TEST9", name="測試股"))
    s.flush()
    return "TEST9"


# ── 水平越權（IDOR）────────────────────────────────────────


def test_other_users_holding_is_404(client, two_users):
    client.cookies.set(auth.SESSION_COOKIE, two_users["b"][1])
    hid = two_users["holding_id"]
    assert client.patch(f"/api/holdings/{hid}", json={"note": "hack"}).status_code == 404
    assert client.delete(f"/api/holdings/{hid}").status_code == 404
    assert client.post(f"/api/holdings/{hid}/transactions", json={
        "type": "sell", "date": "2026-01-05", "price": 100, "shares": 1,
    }).status_code == 404


def test_other_users_watchlist_is_404(client, two_users):
    client.cookies.set(auth.SESSION_COOKIE, two_users["b"][1])
    assert client.delete(f"/api/watchlists/{two_users['wl_id']}").status_code == 404


def test_owner_still_sees_own_holding(client, two_users):
    client.cookies.set(auth.SESSION_COOKIE, two_users["a"][1])
    r = client.get("/api/holdings")
    assert r.status_code == 200
    assert any(i["id"] == two_users["holding_id"] for i in r.json()["items"])


def test_list_is_scoped(client, two_users):
    client.cookies.set(auth.SESSION_COOKIE, two_users["b"][1])
    r = client.get("/api/holdings")
    assert r.status_code == 200
    assert all(i["id"] != two_users["holding_id"] for i in r.json()["items"])


# ── 身分流程 ──────────────────────────────────────────────


def test_signup_verify_login_flow(client):
    r = client.post("/api/auth/signup",
                    json={"email": EMAIL_SIGNUP, "password": "longenough1"})
    assert r.status_code == 200

    # 未驗證前登入要被擋
    r = client.post("/api/auth/login",
                    json={"username": EMAIL_SIGNUP, "password": "longenough1"})
    assert r.status_code == 403

    # 從 DB 撿驗證 token（console 模式下信在 log 裡）走公開驗證頁
    with session_scope() as s:
        uid = s.query(models.User.id).filter(
            models.User.email == EMAIL_SIGNUP).scalar()
        token = s.query(models.EmailVerification.token).filter(
            models.EmailVerification.user_id == uid).scalar()
    assert token
    assert client.get(f"/verify/{token}").status_code == 200

    r = client.post("/api/auth/login",
                    json={"username": EMAIL_SIGNUP, "password": "longenough1"})
    assert r.status_code == 200 and r.json()["ok"]
    assert auth.SESSION_COOKIE in r.cookies


def test_signup_duplicate_email_409(client):
    r = client.post("/api/auth/signup",
                    json={"email": EMAIL_A, "password": "longenough1"})
    assert r.status_code == 409


def test_wrong_password_401_generic(client):
    r = client.post("/api/auth/login",
                    json={"username": EMAIL_A, "password": "wrong-pass"})
    assert r.status_code == 401
    # 與「查無此帳號」同一句——不洩漏帳號是否存在
    r2 = client.post("/api/auth/login",
                     json={"username": f"nobody-{_SUFFIX}@test.local", "password": "x"})
    assert r2.status_code == 401
    assert r.json()["reason"] == r2.json()["reason"]


# ── Session 撤銷 ─────────────────────────────────────────


def test_session_version_bump_revokes_token(client, two_users):
    a_id, a_tok = two_users["a"]
    client.cookies.set(auth.SESSION_COOKIE, a_tok)
    assert client.get("/api/holdings").status_code == 200

    with session_scope() as s:
        s.get(models.User, a_id).session_version += 1
    assert client.get("/api/holdings").status_code == 401


# ── 隔離層的型別保證 ─────────────────────────────────────


def test_userdata_requires_user_id():
    with pytest.raises(ValueError):
        UserData(object(), 0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        UserData(object(), None)  # type: ignore[arg-type]
