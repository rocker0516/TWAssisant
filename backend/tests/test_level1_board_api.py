"""Level 1 端點的版本隔離：v1/v2 ledger 並存時，board 不得混回兩版。

沿用 test_ctx_api.py 的 TestClient＋登入慣例：monkeypatch auth_enabled 為 True，
用 session_scope 建帳號、issue_token 換 session cookie。
"""

from __future__ import annotations

import secrets
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app import auth, main
from app.api import routes_level1
from app.storage import models
from app.storage.database import init_db, session_scope

_SUFFIX = secrets.token_hex(4)
EMAIL = f"l1-api-{_SUFFIX}@test.local"
PRED_DATE = date(2019, 1, 2)          # 遠早於真實資料，不干擾既有 ledger


@pytest.fixture(scope="module", autouse=True)
def _db():
    init_db()
    yield
    with session_scope() as s:
        s.query(models.Level1Prediction).filter(
            models.Level1Prediction.prediction_date == PRED_DATE).delete()
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


def _seed_two_versions():
    """同日同股寫入 v1 與現行版兩列 ledger（PK 含 model_version，可並存）。"""
    with session_scope() as s:
        if not s.query(models.Stock).filter(models.Stock.id == "9001").first():
            s.add(models.Stock(id="9001", name="測試股", is_etf=False, market="上市"))
            s.flush()  # autoflush=False；Level1Prediction 無 relationship() 連 Stock，
            # 同一 flush 內 UOW 不保證先插入 Stock，需顯式 flush（同 test_multiuser.py _any_stock）
        for ver, score in (("l1_lgbm_v1", 0.9),
                           (routes_level1.CURRENT_MODEL_VERSION, 0.8)):
            s.merge(models.Level1Prediction(
                prediction_date=PRED_DATE, stock_id="9001", horizon=5,
                model_version=ver, score=score, rank=1, pct_rank=1.0,
                universe_size=564, feature_version="test"))


def test_board_returns_only_current_model_version(client):
    _seed_two_versions()
    _, tok = _mk_user(EMAIL)
    client.cookies.set(auth.SESSION_COOKIE, tok)

    r = client.get("/api/level1/board?horizon=5&k=20")
    assert r.status_code == 200
    ids = [it["stock_id"] for it in r.json()["items"]]
    assert len(ids) == len(set(ids)), f"同一股票回傳多列（版本未隔離）：{ids}"


def test_current_model_version_matches_predict_script():
    """端點的版本常數必須與生產 pipeline 一致，否則畫面會永遠是空的。"""
    from scripts import level1_predict
    assert routes_level1.CURRENT_MODEL_VERSION == level1_predict.MODEL_VERSION
