import pytest
from psycopg.errors import InsufficientPrivilege
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from core.config import Settings

BASE_ENV = {
    "POSTGRES_USER": "owner",
    "POSTGRES_PASSWORD": "p@ss:w/rd",
    "POSTGRES_READONLY_USER": "reader",
    "POSTGRES_READONLY_PASSWORD": "ro",
}


def make_settings(monkeypatch, **overrides) -> Settings:
    for key, value in {**BASE_ENV, **overrides}.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_readwrite_and_readonly_use_different_accounts(monkeypatch):
    s = make_settings(monkeypatch)
    assert s.database_url.username == "owner"
    assert s.readonly_database_url.username == "reader"


def test_password_with_special_chars_is_preserved(monkeypatch):
    s = make_settings(monkeypatch)
    assert s.database_url.password == "p@ss:w/rd"


def test_secrets_are_hidden_in_repr(monkeypatch):
    s = make_settings(monkeypatch, FINMIND_TOKEN="super-secret")
    assert "super-secret" not in repr(s)
    assert "p@ss:w/rd" not in repr(s)


def test_missing_password_fails_fast(monkeypatch):
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    for key in ("POSTGRES_USER", "POSTGRES_READONLY_USER", "POSTGRES_READONLY_PASSWORD"):
        monkeypatch.setenv(key, BASE_ENV[key])
    with pytest.raises(ValidationError, match="postgres_password"):
        Settings(_env_file=None)


@pytest.mark.integration
def test_readonly_account_cannot_write():
    from core.db import get_engine

    with get_engine(readonly=True).connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
        # 必須是「權限不足」，而不是連線失敗等其他錯誤
        with pytest.raises(ProgrammingError) as exc_info:
            conn.execute(text("CREATE TABLE should_fail (id int)"))
        assert isinstance(exc_info.value.orig, InsufficientPrivilege)
