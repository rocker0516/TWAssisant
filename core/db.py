"""資料庫連線。寫入與唯讀使用不同帳號，公開網站只拿得到唯讀連線。"""

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from core.config import get_settings


class Base(DeclarativeBase):
    """所有資料表的共同父類別。"""


@lru_cache
def get_engine(readonly: bool = False) -> Engine:
    settings = get_settings()
    url = settings.readonly_database_url if readonly else settings.database_url
    return create_engine(url, pool_pre_ping=True)


def get_sessionmaker(readonly: bool = False) -> sessionmaker:
    return sessionmaker(bind=get_engine(readonly), expire_on_commit=False)
