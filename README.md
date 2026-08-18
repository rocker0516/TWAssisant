# TWAssistant — 台股操作助手

協助挑選台股標的的助手：**進場推薦 + 出場提醒 + 類股方向 + 消息面 + AI 解讀**。
**資料源全免費**（證交所 / 櫃買中心官方開放資料）。

## 啟動方式

| 平台 | 方式 |
|---|---|
| **Windows（網站模式）** | 雙擊 `start.bat` — 自動建環境/打包前端/產生登入密碼，綁 `0.0.0.0:8000`，區網裝置可用 `http://<主機IP>:8000` 連入，**需登入**（帳密在 `backend/.env`，首次啟動視窗會顯示）。改過前端後用 `start.bat build` 強制重打包。 |
| macOS（本機模式） | 雙擊 `start.command`（維持原本機單人、無登入模式）。 |

> 登入開關：`backend/.env` 的 `TWA_AUTH_PASSWORD` 有值才會要求登入；刪掉即回到無登入的本機開發模式。

> 狀態：P0~P6 全數完成，完整可用。涵蓋上市 + 上櫃。

---

## 它能做什麼（九頁，側欄順序）

| 頁面 | 功能 |
|---|---|
| 🏠 **今日總覽** | 大盤狀態列（成交額/漲跌家數/三大法人）+ 可拖拉自訂 widget（持股提醒/推薦/類股/消息）+ AI 盤勢總結 |
| 📰 **情報** | AI 全市場消息重點 + 我的關注焦點（持股/觀察清單相關）+ 依題材分群 + 近期事件（利空/展望/題材/內部人轉讓/中性 可篩） |
| 🎯 **進場推薦** | 波段軌 + 長線軌雙軌選股（硬篩→評分→門檻），卡片含買進區間/參考停損/理由；接近門檻折疊區 |
| 📊 **類股行情** | ECharts 熱力圖（顏色=方向、面積=成交佔比）+ 強弱排行；點入類股專屬頁（方向判讀 + AI 解讀 + 成分股）|
| 💰 **籌碼動向** | 類股資金輪動（可切法人別）、資金流入/流出榜、今日籌碼異動、個股籌碼榜 |
| 💼 **我的持股** | 加碼/分批賣（均價重算）、出場狀態燈 🔴🟠🟡🟢、可展開看停損/移動停利/交易明細 |
| ⭐ **觀察清單** | 多組命名清單、進場狀態燈、到價/達門檻/消息提醒、一鍵轉持股 |
| 🧪 **策略室** | 研究用：訊號時變效力、標籤共存機率、模擬倉（每天照單操作）、勝率分析、參數敏感度 |
| ⚙️ **設定** | 評分配分（即時生效重算）、出場參數、資料來源 token 測試、深/淺主題 |

另有 **個股詳情**（K 線 + 技術/基本面/籌碼/支撐壓力/本益比河流/目標價/產業鏈）與 **類股專屬頁**，由上列頁面點入。

**公開頁（免登入、伺服器端渲染）**：`/rankings`（漲跌幅/成交值/法人買超排行）、`/stocks`（全市場個股索引）、`/stock/{id}`（個股公開頁：行情/估值/月營收/季財報/股利，不含買賣參數）。命名空間約定：`/api/*` = JSON API（需登入）、`/app/*` = 上表的 SPA、其餘 = 公開頁（`backend/app/web/`）。

右下角常駐 **🤖 浮動 AI 助手**：情境感知（知道你在看哪頁/哪檔）、跨頁不消失、逐字串流、只根據 App 內部已算好的結論回答。

---

## 架構（七層 + 兩橫切）

```
來源層(可抽換) → 資料層(SQLite) → 運算引擎(可插拔) → 服務 API → 前端
              ↘ LLM 層(翻譯員)    ↘ 排程(每日盤後 pipeline)
```

每日盤後排程抓資料 → 引擎全算好 → LLM 翻白話存快取 → Discord 通知；白天前端只讀算好的結果。
依賴單向往下：**換來源 / 加指標 / 換模型只動一層**。

**每日 pipeline（以 `scheduler/run.py` 的清單為準）**：Fetch → Indicator → Sector → News →
TargetPrice → Attention → Scoring → MLConsensus → Corners → **SignalLog** → Exit → Notify →
PoppableEfficacy。LLM 翻白話已移出 pipeline，改端點首讀懶生成（`llm/lazy.py`）。
整條冪等（增量補缺 + upsert 覆寫）→ 可重跑、關機後補跑安全。

---

## 資料來源（全免費）

| 用途 | 來源 |
|---|---|
| 主檔/產業別 | FinMind `TaiwanStockInfo`（免 token） |
| 行情/法人/融資券 | **TWSE（上市）+ TPEX（上櫃）官方開放資料**（免 token、全市場 by-date） |
| 估值/月營收/季財報 | TWSE BWIBBU + MOPS（上市）、TPEX openapi（上櫃） |
| 重訊/處置股 | TWSE OpenAPI（每日重大訊息 + 處置股） |
| AI 解讀 | Claude API（Haiku 例行 / Sonnet 助手） |

> 註：FinMind 免費層擋全市場 by-date，故行情/籌碼全面改用證交所/櫃買官方開放資料。

---

## 安裝與啟動

### 🚀 一鍵啟動（macOS，最簡）
在 Finder 直接**雙擊專案根目錄的 `start.command`**即可：

- 首次會自動建 venv、裝後端依賴、打包前端（約 1~2 分鐘，之後秒開）。
- 啟動單一伺服器（`:8000`）並自動開瀏覽器 → 後端同時服務打包好的前端，不用另外開前端。
- 關掉那個終端機視窗（或按 `Ctrl-C`）即停止。
- 前端有改動想重打包：刪掉 `frontend/dist` 後再雙擊，或手動 `npm --prefix frontend run build`。

> 首次雙擊若被 Gatekeeper 擋，於 `start.command` 按右鍵 →「打開」放行一次即可。
> 下方是手動 / 開發模式（前後端分離、Vite 熱更新）。

### 後端
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install "anthropic>=0.40"   # 需要 AI 功能才裝

# 跑一次盤後 pipeline（行情/籌碼免 token；AI/Discord 未設則自動略過）
python -m app.scheduler.run                    # 當日
python -m app.scheduler.run --date 2026-06-05  # 回補指定日

# 起 API
uvicorn app.main:app --port 8000
```

### 前端
```bash
cd frontend
npm install
npm run dev          # → http://localhost:5173/app（App 掛在 /app 下；請用 localhost，非 127.0.0.1）
npm run gen:api      # 後端改欄位後重生 TS 型別
```
dev 模式 Vite 會把 `/api` 代理到後端 `:8000`。

### 每日排程（macOS）
```bash
deploy/launchd/install.sh    # 每日 21:30 自動跑（融資券約 21:00 才齊）
```

---

## 憑證設定（皆選用，不設則該功能略過）

token 不入庫，存 **macOS Keychain** 或 `backend/credentials.toml`（已 gitignore）。

```bash
# Claude API（AI 解讀/助手）
security add-generic-password -a twassistant -s anthropic_token -w "sk-ant-..."
# Discord 通知 webhook
security add-generic-password -a twassistant -s discord_webhook -w "https://discord.com/api/webhooks/..."
```
或複製 `backend/credentials.example.toml` → `credentials.toml` 填入。
也可在 **設定 → 資料來源** 頁測試連線後存入。

---

## 操作邏輯重點

- **雙軌分開**：波段軌（技術+籌碼）、長線軌（基本面）各自硬篩→評分，只推 ≥ 門檻（預設 70，可調）。
- **配分自由給分**：5 大類配分可在設定頁調整，系統自動換算比例、即時重算生效。
- **類股修正**：強勢類股加分、弱勢扣分（不排除）。
- **出場狀態燈**：停損 / 移動停利 / 技術轉弱 / 基本面轉弱 / 消息利空多訊號整合成一個結論。
- **LLM 只當翻譯員**：把已算好的結論翻白話，不餵原始數字、不喊買賣、附免責；日批次存快取省 token。
- **誠實定位**：方向與輪動為趨勢判讀、非預測；所有 AI 內容非投資建議。

---

## 專案結構

```
backend/app/
  config.py            設定 + 能力→來源綁定（DI）
  credentials.py       token 讀取（Keychain / toml）
  sources/             來源 adapter（base/interfaces/twse/tpex/combined/finmind/fugle/registry）
  storage/             models（六群表）/ repositories（冪等 upsert）/ database
  engines/             indicators / sector / scoring / exit / news / signal_log + rules(可插拔規則)
  llm/                 client / translators / batch / assistant / store
  services/            holding_service / settings_service
  scheduler/           pipeline / steps / run / trading_calendar
  api/                 routes*（推薦/詳情/持股/類股/觀察/設定/總覽/助手）+ schemas
frontend/src/
  pages/               9 頁 + 個股詳情 + 類股專屬 + 登入
  components/          Layout / FloatingAssistant / KLineChart / SectorHeatmap / …
  api/                 client（React Query hooks）+ types（openapi 生成）
deploy/launchd/        每日排程
```

---

## 疑難排解

- **`/recommendations` 空 / 數字怪**：先確認當日 pipeline 有跑成功（設定無誤、`/system/status` 看資料筆數）。
- **前端連不到後端**：用 `http://localhost:5173/app`（App 掛在 /app 下；Vite 綁 IPv6），並確認後端在 `:8000`。
- **AI 卡片/助手沒反應**：未設 Claude 金鑰時會略過或提示；設定後重啟後端。
- **改 `tailwind.config.js` 沒生效**：重啟 `npm run dev`（Tailwind config 有快取）。
- **新增市場/來源後沒歷史資料**：FetchStep 用全域 max_date 增量，需清行情表重抓一次。

---

## 已知限制 / 未做

- TPEX 估值為當日快照（無逐日歷史；估值僅取最新，影響不大）。
- 重大訊息端點為當日快照（無歷史，往後每日累積）。
- 全額交割 / 連續漲跌停硬篩：尚無免費資料源（已用暴量/處置過濾擋住極端）。
- 首頁 widget 為顯示/隱藏 + 拖拉排序；更細的網格自訂未做。

*本工具為投資輔助，所有輸出非投資建議，決策與風險請自行承擔。*
