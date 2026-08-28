"""Level 1 端點的版本隔離：v1/v2 ledger 並存時，board 不得混回兩版。

沿用 test_ctx_api.py 的 TestClient＋登入慣例：monkeypatch auth_enabled 為 True，
用 session_scope 建帳號、issue_token 換 session cookie。
"""

from __future__ import annotations

import json
import secrets
from datetime import date, datetime
from pathlib import Path

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
        s.query(models.DailyPrice).filter(
            models.DailyPrice.stock_id == "9001").delete()
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


def test_board_returns_only_current_model_version(client, monkeypatch):
    monkeypatch.setattr(routes_level1, "CURRENT_MODEL_VERSION", "l1_test_iso")
    _seed_two_versions()
    _, tok = _mk_user(EMAIL)
    client.cookies.set(auth.SESSION_COOKIE, tok)

    r = client.get("/api/level1/board?horizon=5&k=20")
    assert r.status_code == 200
    ids = [it["stock_id"] for it in r.json()["items"]]
    assert ids == ["9001"], f"版本隔離失敗（應只回現行版該列）：{ids}"


def test_current_model_version_matches_predict_script():
    """端點的版本常數必須與生產 pipeline 一致，否則畫面會永遠是空的。"""
    from scripts import level1_predict
    assert routes_level1.CURRENT_MODEL_VERSION == level1_predict.MODEL_VERSION


# ── validation 端點（畫面設計 §5.1：凍結驗證的投影） ──

_RESULTS = Path(__file__).resolve().parents[1] / "data" / "level1_results.json"


@pytest.mark.skipif(not _RESULTS.exists(), reason="level1_results.json artifact 不存在")
def test_validation_endpoint_shape(client):
    _, tok = _mk_user(EMAIL)
    client.cookies.set(auth.SESSION_COOKIE, tok)

    r = client.get("/api/level1/validation")
    assert r.status_code == 200
    body = r.json()
    assert body["model_version"] == routes_level1.CURRENT_MODEL_VERSION
    assert set(body["horizons"].keys()) == {"1", "5", "10"}
    h5 = body["horizons"]["5"]
    assert set(h5["ladder"].keys()) == {"random", "mom_ret20", "ridge_v2", "lgbm"}
    cell = h5["ladder"]["lgbm"]["holdout"]
    assert set(cell.keys()) == {"mean_ic", "icir", "monotonicity", "n_days",
                                "evaluation_n_mean",
                                "top20_excess_pct", "top20_day_win_rate"}
    for seg in ("dev_oos", "holdout"):
        q = h5["quantiles"][seg]
        assert len(q["values"]) == 10
        assert q["interpretation"]["shape"] in {"monotonic", "weak_top_end"}
        assert q["interpretation"]["text"]
    assert body["generated_at"]  # provenance——畫面數字可回答「我來自哪個 artifact」


def test_validation_endpoint_404_when_artifact_missing(client, monkeypatch):
    """load_validation() 回 None（檔缺）時須明確 404——不能被恆存在的本機 artifact
    蓋過去而永遠沒被驗證到（同 test_ctx_api 慣例）。"""
    monkeypatch.setattr(routes_level1, "load_validation", lambda: None)
    _, tok = _mk_user(EMAIL)
    client.cookies.set(auth.SESSION_COOKIE, tok)
    r = client.get("/api/level1/validation")
    assert r.status_code == 404


# ── board adv20（畫面設計 §5.2：倉位規模脈絡，非過濾） ──

def test_board_includes_adv20(client, monkeypatch):
    """monkeypatch 版本常數把查詢隔離到測試列——否則 max(prediction_date) 會選到
    真實 ledger 的最新日，2019 年的測試列進不了榜單（虛測）。"""
    monkeypatch.setattr(routes_level1, "CURRENT_MODEL_VERSION", "l1_test")
    with session_scope() as s:
        if not s.query(models.Stock).filter(models.Stock.id == "9001").first():
            s.add(models.Stock(id="9001", name="測試股", is_etf=False, market="上市"))
        s.merge(models.Level1Prediction(
            prediction_date=PRED_DATE, stock_id="9001", horizon=5,
            model_version="l1_test", score=0.9, rank=1, pct_rank=1.0,
            universe_size=1, feature_version="test"))
        for day in (date(2019, 1, 1), date(2019, 1, 2)):
            s.merge(models.DailyPrice(
                stock_id="9001", date=day, open=10.0, high=10.0, low=10.0,
                close=10.0, volume=1000, turnover=2e8))
    _, tok = _mk_user(EMAIL)
    client.cookies.set(auth.SESSION_COOKIE, tok)

    r = client.get("/api/level1/board?horizon=5&k=20")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [it["stock_id"] for it in items] == ["9001"]   # 版本隔離下只有測試列
    assert items[0]["adv20"] == pytest.approx(2e8)


def test_validation_endpoint_503_on_schema_drift(client, monkeypatch, tmp_path):
    """舊版 artifact 缺新欄位時要明確 503，不是裸 KeyError 500。"""
    stale = tmp_path / "level1_results.json"
    stale.write_text(json.dumps({
        "first_test": "2022-01-01", "periods": {},
        "horizons": {"1": {}, "5": {}, "10": {}},   # 缺 ladder 模型與欄位
    }), encoding="utf-8")
    monkeypatch.setattr(routes_level1, "_RESULTS_PATH", stale)
    monkeypatch.setattr(routes_level1, "_validation_cache", None)
    _, tok = _mk_user(EMAIL)
    client.cookies.set(auth.SESSION_COOKIE, tok)
    r = client.get("/api/level1/validation")
    assert r.status_code == 503
