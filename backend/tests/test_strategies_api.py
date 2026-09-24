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


# ── 審查回歸：未知欄位/排序鍵不得寫入 DB，寫入前一律 400 ────────


def test_create_with_unknown_field_is_400(client, two_users):
    client.cookies.set(auth.SESSION_COOKIE, two_users["a"][1])
    r = client.post(_BASE + "/", json={
        "name": "壞條件",
        "conditions": [{"field": "nope", "op": "gt", "value": 0}],
        "sort_field": "turnover", "sort_desc": True, "top_n": 30,
        "target_pct": 10.0, "horizon_days": 10, "stop_pct": None,
    })
    assert r.status_code == 400
    # 沒有殘留：GET / 看不到任何名叫「壞條件」的策略
    listed = client.get(_BASE + "/").json()
    assert not any(s["name"] == "壞條件" for s in listed)


def test_create_with_invalid_sort_field_is_400(client, two_users):
    client.cookies.set(auth.SESSION_COOKIE, two_users["a"][1])
    r = client.post(_BASE + "/", json={
        "name": "壞排序",
        "conditions": [{"field": "close", "op": "gt", "value": 0}],
        "sort_field": "does_not_exist", "sort_desc": True, "top_n": 30,
        "target_pct": 10.0, "horizon_days": 10, "stop_pct": None,
    })
    assert r.status_code == 400


def test_patch_with_unknown_field_is_400(client, two_users):
    client.cookies.set(auth.SESSION_COOKIE, two_users["a"][1])
    created = _create_strategy(client, "待改策略")
    r = client.patch(f"{_BASE}/{created['id']}", json={
        "conditions": [{"field": "nope", "op": "gt", "value": 0}],
    })
    assert r.status_code == 400
    # 沒有被壞資料污染：原條件維持不變
    r2 = client.get(_BASE + "/")
    st = next(s for s in r2.json() if s["id"] == created["id"])
    assert st["conditions"] == created["conditions"]


def test_patch_with_invalid_sort_field_is_400(client, two_users):
    client.cookies.set(auth.SESSION_COOKIE, two_users["a"][1])
    created = _create_strategy(client, "待改排序")
    r = client.patch(f"{_BASE}/{created['id']}", json={
        "sort_field": "does_not_exist",
    })
    assert r.status_code == 400


# ── F2 審查回歸：條件 value 型別不合法（dict 塞進 gt、壞 streak）→ 400 ──


def test_create_with_dict_value_on_comparison_op_is_400(client, two_users):
    client.cookies.set(auth.SESSION_COOKIE, two_users["a"][1])
    r = client.post(_BASE + "/", json={
        "name": "壞型別條件",
        "conditions": [{"field": "close", "op": "gt", "value": {}}],
        "sort_field": "turnover", "sort_desc": True, "top_n": 30,
        "target_pct": 10.0, "horizon_days": 10, "stop_pct": None,
    })
    assert r.status_code == 400
    listed = client.get(_BASE + "/").json()
    assert not any(s["name"] == "壞型別條件" for s in listed)


def test_create_with_streak_n_zero_is_400(client, two_users):
    client.cookies.set(auth.SESSION_COOKIE, two_users["a"][1])
    r = client.post(_BASE + "/", json={
        "name": "壞streak",
        "conditions": [{"field": "close", "op": "streak_gt",
                        "value": {"n": 0, "threshold": 1}}],
        "sort_field": "turnover", "sort_desc": True, "top_n": 30,
        "target_pct": 10.0, "horizon_days": 10, "stop_pct": None,
    })
    assert r.status_code == 400


def test_create_with_streak_n_non_int_is_400(client, two_users):
    client.cookies.set(auth.SESSION_COOKIE, two_users["a"][1])
    r = client.post(_BASE + "/", json={
        "name": "壞streak2",
        "conditions": [{"field": "close", "op": "streak_gt",
                        "value": {"n": "abc", "threshold": 1}}],
        "sort_field": "turnover", "sort_desc": True, "top_n": 30,
        "target_pct": 10.0, "horizon_days": 10, "stop_pct": None,
    })
    assert r.status_code == 400


def test_patch_with_dict_value_on_comparison_op_is_400(client, two_users):
    client.cookies.set(auth.SESSION_COOKIE, two_users["a"][1])
    created = _create_strategy(client, "待改壞型別")
    r = client.patch(f"{_BASE}/{created['id']}", json={
        "conditions": [{"field": "close", "op": "gt", "value": {"n": 1}}],
    })
    assert r.status_code == 400
    r2 = client.get(_BASE + "/")
    st = next(s for s in r2.json() if s["id"] == created["id"])
    assert st["conditions"] == created["conditions"]


# ── F3 審查回歸：數值欄位無邊界 → 打爆後端；改為 422 ──────────


def test_create_with_huge_horizon_days_is_422(client, two_users):
    client.cookies.set(auth.SESSION_COOKIE, two_users["a"][1])
    r = client.post(_BASE + "/", json={
        "name": "巨大horizon",
        "conditions": [{"field": "close", "op": "gt", "value": 0}],
        "sort_field": "turnover", "sort_desc": True, "top_n": 30,
        "target_pct": 10.0, "horizon_days": 1000000, "stop_pct": None,
    })
    assert r.status_code == 422
    listed = client.get(_BASE + "/").json()
    assert not any(s["name"] == "巨大horizon" for s in listed)


def test_create_with_huge_top_n_is_422(client, two_users):
    client.cookies.set(auth.SESSION_COOKIE, two_users["a"][1])
    r = client.post(_BASE + "/", json={
        "name": "巨大topn",
        "conditions": [{"field": "close", "op": "gt", "value": 0}],
        "sort_field": "turnover", "sort_desc": True, "top_n": 999999,
        "target_pct": 10.0, "horizon_days": 10, "stop_pct": None,
    })
    assert r.status_code == 422


def test_patch_with_huge_horizon_days_is_422(client, two_users):
    client.cookies.set(auth.SESSION_COOKIE, two_users["a"][1])
    created = _create_strategy(client, "待改horizon")
    r = client.patch(f"{_BASE}/{created['id']}", json={
        "horizon_days": 1000000,
    })
    assert r.status_code == 422
