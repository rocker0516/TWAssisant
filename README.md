# TWAssistant — 台股操作助手

進場推薦 + 出場提醒，**只本機單人跑**。資料源全免費（Fugle 行情 + FinMind 籌碼/基本面）。

> 目前進度：**P0 地基**（骨架 + SQLite schema + 來源 adapter + 抓資料落庫 + launchd 排程）。
> 後續：P1 進場核心 → P2 出場 → P3 類股 → P4 消息面 → P5 LLM → P6 整合打磨。

## 架構（七層 + 兩橫切）

```
來源層(可抽換) → 資料層(SQLite) → 運算引擎(可插拔) → 服務API → 前端
              ↘ LLM層(翻譯員)   ↘ 排程(每日盤後 pipeline)
```

核心流：每日盤後排程抓資料存 SQLite → 引擎全算好 → LLM 翻白話存快取 → Discord 通知；
白天前端只讀算好的結果，不臨場運算。依賴單向往下，換來源/加指標/換模型只動一層。

## 後端目錄

```
backend/app/
  config.py            # 全域設定 + 能力→來源綁定（DI）
  credentials.py       # token 讀取（Keychain 優先，fallback credentials.toml）
  sources/             # 來源 adapter 層
    base.py            #   BaseSource：HTTP/重試/限流/health/test
    interfaces.py      #   能力介面 Universe/Price/Chip/Fundamental/News
    schemas.py         #   統一輸出欄位
    finmind.py         #   FinMind（主檔+籌碼+基本面+全市場日K）
    fugle.py           #   Fugle（逐檔細K，候選池用）
    twse_news.py       #   重訊（P4 填）
    registry.py        #   config 驅動 DI
  storage/             # 資料層
    models.py          #   六群表 schema
    repositories.py    #   BaseRepository（冪等 upsert）
    database.py        #   引擎 / session / init_db
  scheduler/           # 排程
    pipeline.py        #   PipelineStep / DailyPipeline
    steps.py           #   FetchStep
    run.py             #   進入點：python -m app.scheduler.run
    trading_calendar.py
  main.py              # FastAPI（P0 最小版：狀態 / 來源測試 / 觸發）
```

## 啟動

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 設定 token（擇一）：
#   1) 複製 credentials.example.toml → credentials.toml 填入
#   2) 或寫 Keychain：security add-generic-password -a twassistant -s finmind_token -w "TOKEN"

# 手動跑一次盤後 pipeline（無 token 也會建表，僅各來源標 error）
python -m app.scheduler.run            # 跑當日
python -m app.scheduler.run --date 2026-06-05   # 回補指定日

# 起 API（驗證 / 設定頁測試連線用）
uvicorn app.main:app --reload
#   GET  /system/status        資料筆數 + 最近一次 pipeline
#   GET  /sources              來源健康狀態
#   POST /sources/finmind/test {"token":"...","save":true}
#   POST /pipeline/run         背景重跑
```

## 排程（每日 21:30）

```bash
deploy/launchd/install.sh        # 載入 launchd
```

## 設計要點

- **OOP**：同概念邏輯抽抽象基底（BaseSource / BaseRepository / PipelineStep），子類只實作差異。
- **可抽換來源**：下游依賴能力介面，換來源只改 `config.source_bindings`。
- **冪等**：增量補缺 + upsert 覆寫 → pipeline 可重跑、關機後補跑安全。
- **token 不入庫**：存 Keychain / gitignore 的 credentials.toml，備份 DB 不洩漏。
