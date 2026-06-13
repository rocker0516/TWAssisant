"""全域設定。

只本機單人跑 → 不需登入/CORS 全開/雲端設定，重點在：
  - 路徑與 DB 位置
  - 「能力 → 來源實作」的綁定（DI 用，換來源只改這裡）
  - 各來源限流參數

設定值優先序：環境變數 / .env > 此處預設。
憑證（token）不在這裡，走 credentials.py（Keychain / credentials.toml）。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 目錄（本檔在 backend/app/config.py）
BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TWA_",
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- 路徑 / DB ---
    data_dir: Path = DATA_DIR
    db_filename: str = "twa.db"

    # --- 服務（本機）---
    host: str = "127.0.0.1"
    port: int = 8000

    # --- 能力 → 來源實作 綁定（DI）。換來源只動這裡 ---
    # key = 能力介面，value = registry 中註冊的來源名稱
    #
    # 全市場日K/法人/融資券 綁 twse：證交所官方開放資料，全免費、免 token、
    # 全市場 by-date（FinMind 免費層擋全市場，故不用其做全市場日資料）。
    # universe 仍用 finmind（TaiwanStockInfo 免費可全抓，含產業別）。
    # fundamental（營收/財報/估值）暫綁 finmind，P0 不抓、P1 再定免費策略。
    # Fugle 保留給 P1+ 候選池/詳情頁逐檔細 K。換來源只動這裡，下游不動。
    source_bindings: dict[str, str] = Field(
        default_factory=lambda: {
            "universe": "finmind",
            "price": "twmarket",  # 上市(TWSE)+上櫃(TPEX) 合併
            "chip": "twmarket",
            "fundamental": "twmarket",  # 估值/營收/財報 上市+上櫃
            "news": "twnews",  # TWSE 重訊/處置 + FinMind 個股新聞（合併）
        }
    )

    # --- 來源限流（token bucket：每秒補充 rate 顆、桶容量 capacity）---
    # FinMind 免費版有流量限制，保守一點；Fugle 行情較寬鬆。
    rate_limits: dict[str, dict[str, float]] = Field(
        default_factory=lambda: {
            "finmind": {"rate": 0.8, "capacity": 5, "timeout": 20.0},
            "fugle": {"rate": 3.0, "capacity": 10, "timeout": 15.0},
            "twse": {"rate": 1.0, "capacity": 3, "timeout": 20.0},
            "tpex": {"rate": 1.0, "capacity": 3, "timeout": 25.0},
        }
    )

    # --- 外部研報來源（公開研報無穩定免費 API、爬蟲易壞）：預設關閉，留接口供後續補強 ---
    research_enabled: bool = False

    # --- 重試 ---
    max_retries: int = 3
    backoff_base: float = 1.5  # 退避秒數 = backoff_base ** attempt

    # --- 排程（融資券約 21:00 才齊，21:30 跑確保籌碼齊）---
    daily_run_time: str = "21:30"

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.db_filename

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s


settings = get_settings()
