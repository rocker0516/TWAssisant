"""集中管理設定。所有值都從環境變數 / .env 讀取，程式碼內不寫死任何密碼或路徑。"""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "production"] = "development"

    # 用 127.0.0.1 而非 localhost：Windows 會先試 IPv6 (::1)，
    # 但資料庫只綁 IPv4，會卡約 2 分鐘才退回
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_db: str = "twassistant"
    postgres_user: str
    postgres_password: SecretStr
    postgres_readonly_user: str
    postgres_readonly_password: SecretStr

    finmind_token: SecretStr = SecretStr("")

    def _url(self, user: str, password: SecretStr) -> URL:
        # URL.create 會正確跳脫密碼中的特殊字元
        return URL.create(
            "postgresql+psycopg",
            username=user,
            password=password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    @property
    def database_url(self) -> URL:
        """可寫入：排程、抓取程式使用。"""
        return self._url(self.postgres_user, self.postgres_password)

    @property
    def readonly_database_url(self) -> URL:
        """唯讀：公開網站使用。"""
        return self._url(self.postgres_readonly_user, self.postgres_readonly_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()
