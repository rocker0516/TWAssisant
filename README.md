# TWAssisant

台股資訊統整、推薦與自動交易系統。完整規劃見 [docs/PLAN.md](docs/PLAN.md)。

## 環境需求

- Docker Desktop（Windows 需啟用 WSL2）
- [uv](https://docs.astral.sh/uv/)（會自動準備 Python 3.12）

## 啟動

```bash
cp .env.example .env          # 填入密碼與 FINMIND_TOKEN
docker compose up -d db       # 啟動 PostgreSQL
uv sync                       # 安裝 Python 套件
uv run pytest                 # 單元測試
uv run pytest -m integration  # 連線資料庫的測試
```
