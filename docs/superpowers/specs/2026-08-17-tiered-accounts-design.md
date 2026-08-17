# 分層帳號（Free / Pro / Admin）設計

日期：2026-08-17 ｜ 分支：`feature/FunctionUpdate`

---

## 1. 背景

TWAssistant 目前是**單租戶單人工具**：

- `auth.py` 只有一組 `.env` 的帳密，session token 為 `user:expiry:sig`，只證明「有人通過了那唯一一道密碼」，不表示「你是誰」。
- 35 張表中，`holdings` / `transactions` / `watchlists` / `watchlist_items` / `settings` **沒有 `user_id`**。多人登入即共用同一份資料。

要改為公開註冊 + 線上付費的網站，真正的前置工程是**使用者身分與資料隔離**；付費牆是隔離完成後加的一層判斷。

**有利條件**：行情 / 指標 / 評分 / 類股 / 籌碼等 30 張表天生全站共用（每日 pipeline 算一次給所有人讀），不需改動。每日批次 AI 解讀已存 `llm_cache` 共用。**唯一隨用戶數線性成長的成本是即時 AI 助手對話**。

---

## 2. 目標與非目標

### 目標
1. 三層存取模型：Free / Pro / Admin，後端為唯一權威。
2. 使用者資料完全隔離，杜絕越權存取（IDOR）。
3. 機密的策略研究工具（訊號衰減、共現、敏感度）僅 Admin 可用。
4. 分兩波上線，第一波不含金流。

### 非目標（本次不做）
- 用量計量／配額系統 —— 免費層不給即時 AI，採純布林鎖，故不需要。
- 每用戶功能旗標覆寫表 —— 促銷、灰度、老用戶保留權益等需求出現時再加。
- 金流串接 —— 屬第二波。

---

## 3. 收費哲學

**付費賣的是「工具與深度」，不是「買什麼」。** 進場推薦、類股、大盤一律免費。

此選擇同時降低法規暴露：在台灣，向不特定人收費提供個股分析或選股建議，可能落入《證券投資顧問事業管理規則》需具投顧執照的範圍。把付費牆放在「記錄、驗證、效率、通知」而非「推薦訊號」，賣的是軟體工具。

> ⚠️ 此為工程觀察，非法律意見。上線前應取得專業法律意見。

---

## 4. 分層定義

### 4.1 Free

| 區域 | 內容 |
|---|---|
| 今日總覽 | 全開，含每日批次 AI 盤勢總結（已快取共用，邊際成本為零） |
| 進場推薦 | 雙軌完整名單、分數、買進區間、參考停損、理由、走勢、標籤徽章、質化風險等級（欄位級分層見 4.4） |
| 類股行情 | 熱力圖、強弱排行、類股詳情、每日批次 AI 解讀 |
| 消息面 | `/intel` 全開 |
| 籌碼流向 | 基礎：大盤資金流、類股資金流 |
| 個股詳情 | 基礎：K 線／OHLCV、技術摘要、基本面歷史、股利、財報、注意處置 |
| 我的持股 | **上限 100 檔** ＋ 出場狀態燈 |
| 觀察清單 | 1 組、上限 20 檔 ＋ 到價提醒 |
| 角落訊號 | `/corners`（推薦頁 `CornerSignalsStrip`） |
| 設定 | 個人偏好：主題、首頁、widget 排版 |

### 4.2 Pro

| 區域 | 端點 |
|---|---|
| AI 助手 ＋ 個股健檢 | `/assistant/chat`、`/assistant/brief`、`/stocks/{id}/health` |
| 回看驗證 | `/recommendations/lookback`、`/recommendations/lookback/calendar` |
| 籌碼進階 | `/flow/alerts`、`/flow/relation`、`/flow/rotation` |
| 個股進階 | `pe-river`、`pb-river`、`target-price`、`industry-chain`、`chip-history`、`levels`、`recommendation-marks` |
| 配分自訂 | `scoring.*` / `sector.*` 個人權重（請求時套用，不寫共享表） |
| 紙上模擬（限定參數） | `/lab/paper/simulate` —— 註冊表等級為 `PRO`；參數限伺服器端白名單（見 5.5） |
| 容量 | 無限持股／無限觀察清單 |
| 主動通知 🆕 | Email／LINE |
| 績效報表 🆕 | 已實現損益、勝率、月報、CSV 匯出 |
| 自訂選股器 🆕 | 條件組合 ＋ 存模板 |
| 組合警示 🆕 | 多條件觸發規則 |

### 4.3 Admin only（機密）

| 端點 | 說明 |
|---|---|
| `/lab/recommendations/signal-decay` | 訊號衰減 |
| `/lab/recommendations/lookback/cooccurrence`（含 `/samples`） | 共現分析 |
| `/lab/recommendations/lookback/sensitivity` | 參數敏感度 |
| `/lab/recommendations/lookback/stats` | 勝率分組統計 |
| `/lab/paper/simulate`（**任意參數**） | 可調參研究模式。非獨立註冊項，見 5.5 |
| `/corners/review` | 角落訊號覆盤 |
| `/settings/system/*` | 排程時間與開關 |

---

### 4.4 進場推薦的欄位級分層

推薦頁是本設計中唯一需要**同端點、不同深度**的區域（`/lab/paper/simulate` 之外）。它並非單一功能，而是四層疊加：

| 層 | 內容 | 回答 | 歸屬 |
|---|---|---|---|
| ① 名單 | 誰上榜、分數、走勢、標籤徽章 | 買什麼 | Free |
| ② 操作參數 | 買進區間、參考停損、理由 | 怎麼買、哪裡跑 | Free |
| ③ 信心與統計 | 命中率統計、可信度、逐面向證據、ML 共識、長線目標區間 | 憑什麼相信 | **Pro** |
| ④ 篩選與排序工具 | 機率門檻、標籤開關、位階、價格、走勢視窗、排序、回看月曆 | 怎麼快速找到 | **Pro** |

①② 屬「買什麼」→ 免費，符合第 3 節的收費哲學與法規考量。③④ 屬「深度」與「工具」→ 付費。

#### `RecommendationItem` 欄位歸屬

| Free | Pro |
|---|---|
| `stock_id`、`name`、`sector_name`、`track` | `coverage`、`confidence`、`stability` |
| `total_score`、`sub_scores` | `details`（逐面向分數 ＋ 帶數字 evidence） |
| `close`、`change_pct`、`spark` | `prob_hit`、`prob_n`、`prob_cond`、`prob_mae`（精確值） |
| `buy_low`、`buy_high`、`stop_loss`、`loss_pct` | `ml_consensus` |
| `reasons` | `target_zone`、`graduation`（長線軌） |
| `passed_styles`、`passed_filter`、`vol_ratio` | `review`（回看模式） |
| `attention`、`attention_tags` | |
| 🆕 `prob_band`、`risk_band`（質化三級） | |

#### 質化降級：`prob_band` / `risk_band`

免費層不得完全看不到風險資訊——那會讓免費層顯得比實際更有把握，與 README「誠實定位」牴觸，等於**好消息免費送、壞消息收費**。

| | Free | Pro |
|---|---|---|
| 命中率 | `prob_band`：偏低／中等／偏高 | `prob_hit` 精確 % ＋ `prob_n` 樣本數 ＋ `prob_cond` 條件描述 |
| 回撤風險 | `risk_band`：低／中／高 | `prob_mae` 精確 % |

升級動機從「有沒有」變為「模糊 vs 精確」，轉換力更強且不犧牲誠實。

**實作約束（兩者皆為安全要求，非美化）：**

1. **降級必須在後端完成。** 依 tier 決定 response 中放 `prob_hit` 或 `prob_band`，兩者**互斥**。若後端照回精確值、由前端換算顯示，開 DevTools 即可看到原值——付費牆架在渲染層等同不存在。

2. **分級門檻必須用相對口徑，不可用絕對值。** 絕對命中率會隨大盤行情整體漂移（見既有結論：10 日 70% 為行情產物）。固定門檻會使多頭時全清單「偏高」、空頭時全部「偏低」，徽章退化為大盤指標而失去個股區辨力。採**當日清單內相對三分位**（與角落挖掘「門檻用基率倍數搬移」同一原理）。門檻常數為伺服器端常數，不進前端 bundle。

#### 前端連帶調整

- **預設排序**：頁面現為 `sort = "prob"`，但 `prob_hit` 屬 Pro。免費層預設須改為 `score`。
- **排序選項須同步移除**，不可僅視覺灰掉——`compareBy("prob")` 對全 `null` 會產生不確定順序，看似亂排。**資料鎖了，依賴該資料的 UI 邏輯必須跟著調整**，此為功能分層常見破口。
- **`comboFilters`（精確組合篩選）**：組合鍵由策略室寫入 localStorage、推薦頁純前端過濾，後端不參與。策略室轉 Admin 後，非 Admin 的 localStorage 恆為空，篩選自然失效。該區塊 UI 須條件式渲染，否則留下永遠無作用的入口。

---

## 5. 架構決策

### 5.1 Entitlement 註冊表（default-deny）

單一檔案 `app/entitlements.py` 為所有存取等級的唯一真相來源：

```python
class Tier(StrEnum):
    FREE = "free"
    PRO = "pro"
    ADMIN = "admin"

ROUTE_TIERS: dict[str, Tier] = {
    "GET /recommendations": Tier.FREE,
    "GET /recommendations/lookback": Tier.PRO,
    "GET /lab/recommendations/signal-decay": Tier.ADMIN,
    ...
}

LIMITS = {
    "holdings":   {Tier.FREE: 100, Tier.PRO: None},
    "watchlists": {Tier.FREE: 1,   Tier.PRO: None},
    "watchlist_items_per_list": {Tier.FREE: 20, Tier.PRO: None},
}
```

**關鍵性質：未列出 = 拒絕。** 新增端點忘了登記，會直接失敗而非默默變免費。

**啟動時自我檢查**：走訪 `app.routes`，任何未登記的路由 → **啟動失敗**（不是警告，警告會被忽略）。

**為何不用路徑前綴 middleware**：`routes_lab.py` 原本沒有 prefix，其端點與公開的 `/recommendations/*` 混在同一命名空間。`/recommendations/lookback/calendar`（公開月曆）與 `/recommendations/lookback/stats`（機密統計）僅一字之差，前綴規則必然誤傷或漏放。

**配套**：`routes_lab.py` 補上 `prefix="/lab"`。此舉不是安全措施（安全靠註冊表），而是讓「哪些是機密」在 URL 上一眼可辨。

### 5.2 資料隔離在 Repository 層

現況 [`routes_holdings.py:209`](../../../backend/app/api/routes_holdings.py) 為 `session.get(models.Holding, holding_id)`，純 ID 查詢、零 ownership 檢查。加上 `user_id` 後若僅靠「記得在每支 route 加 filter」，`PATCH /holdings/3` 仍能改到他人資料。

**設計原則：讓「不帶 user_id 的查詢」在型別上不可能寫出來。**

- Repository 建構子強制吃 `user_id`，所有 query 由其組裝。
- Route 層透過 DI 取得 scoped repository，**拿不到裸 session**。
- 查無資料一律回 **404 而非 403**（403 會洩漏「該 ID 存在」）。

### 5.3 `settings` 三分

現況 `settings` 為全站單一 key-value 表，且 [`settings_service.recompute()`](../../../backend/app/services/settings_service.py) 會覆寫全市場的 `sector_daily` 與 `scores`；`general.schedule` 更會直接 `get_scheduler().reschedule()`。多租戶下這代表任一用戶改設定即污染全站資料、或關掉全站排程。

| 類別 | 內容 | 處理 |
|---|---|---|
| 系統級 | `general.schedule` | 移出 user settings，Admin only |
| 個人偏好（不影響計算） | `general.theme`、`general.home`、`layout.widgets` | 每人一份 |
| 個人演算法參數 | `exit.*` | 每人一份。README 已載明「讀取時計算」，不寫共享表，天生安全 |
| 個人演算法參數 | `scoring.*`、`sector.*` | 見 5.4 |

### 5.4 個人評分權重改為「請求時套用」

評分本質是**純函數**：輸入為 pipeline 已算好的 `indicators` / `sector_daily`，輸出為分數。`recompute()` 自身註解載明「不重抓資料、秒級」，證明貴的是抓資料而非計算。

- 每日 pipeline 照常以**官方預設權重**算一份寫入 `scores` → Free 用戶直接讀，零成本。
- Pro 用戶若自訂權重，於 `/recommendations` 請求時對既有 indicators 重跑評分，**不落地**。
- 移除面向一般用戶的 `POST /settings/recompute`（改 Admin only）。

成本從 O(用戶數 × 每日) 降為 O(請求數)，且僅實際改過權重的 Pro 用戶負擔。

### 5.5 紙上模擬的參數白名單

Pro 可調參數會形成洩漏路徑：掃 `probMin` × `holdDays` 等同手工重建 Admin 專屬的敏感度分析。**鎖住查詢卻未鎖住能推導出同樣答案的另一個查詢。**

採**離散化**（方案 a）：

```python
PAPER_SIM_ALLOWED = {          # 僅適用非 Admin
    "prob_min":  {50, 60, 70},
    "top_n":     {3, 5},
    "hold_days": {10, 20},
}
```

- **必須在伺服器端驗證**，超出白名單回 422。前端下拉選單只是 UI，Pro 用戶可直接 curl 送任意值。
- Admin 不受限制。
- **與註冊表的關係**：`/lab/paper/simulate` 在 `ROUTE_TIERS` 中登記為**單一等級 `PRO`**，維持「一路由一等級」的不變式；Admin 的可調參特權由 handler 內依 role 略過白名單檢查達成，**不是**第二筆註冊表項目。

**「同端點不同深度」共兩處**，皆以「註冊表登記最低可存取等級 ＋ handler 內依 tier 調整回應」實作，不破壞一路由一等級：

| 端點 | 註冊等級 | 更高等級額外獲得 |
|---|---|---|
| `/lab/paper/simulate` | `PRO` | Admin：參數不受白名單限制 |
| `/recommendations` | `FREE` | Pro：統計與信心欄位（見 4.4） |

兩者皆須在 handler 明確註記，並各自有測試斷言。
- 白名單為伺服器端常數，日後可隨時收緊。
- 已知取捨：12 種組合仍會揭露一張粗略的參數表現圖。若判定過寬，改用方案 b（固定官方參數）。

### 5.6 機密性保護：第 2 級

| 層 | 措施 |
|---|---|
| 後端 | Admin 以外一律 403 —— **這是真正的保護**，數值永不離開伺服器 |
| 導覽 | 側邊欄對非 Admin 隱藏策略室入口 |
| 前端 | `LabPage` 切為獨立 lazy chunk，非 Admin 不預載 |

**說明**：前端 bundle 永遠是公開的。但機密的**結論數值**只存在於 API 回應，機密的**方法論**（`corner_defs.py`、`data/corners.json`）本來就不送前端。bundle 最多洩漏「存在一個策略室」，不洩漏內容。若日後認定連存在性都不可洩漏，再升級為 build 時排除（第 3 級，需雙 build）。

---

## 6. 資料模型變更

### 新增

```
users
  id, email(unique), password_hash, tier, role,
  email_verified_at, created_at, session_version

email_verifications   token, user_id, expires_at
password_resets       token, user_id, expires_at, used_at
```

### 既有表加 `user_id`（外鍵 + 索引）

`holdings`、`transactions`、`watchlists`、`watchlist_items`、`settings`

### 遷移

現有單人資料歸屬給第一個 Admin 帳號。遷移須冪等、可重跑（與 pipeline 一致的專案慣例）。

---

## 7. 資安要求

### 7.1 已修（本分支 `3999954`）

`_require_login` 以 `"text/html" in Accept` 放行所有 GET，且該判斷早於 token 驗證。因 API 端點同為 GET，`curl -H "Accept: text/html" /api/recommendations` 可無 session 取得完整資料；`start.bat` 綁 `0.0.0.0:8000` 使同網段任何裝置皆可讀取。已改為以路徑判斷 SPA 殼，並加上回歸測試。

**通用教訓：授權判斷不得依賴客戶端可控的輸入**（header、query、body 中的 `plan` / `user_id`）。授權只能依據伺服器端自已驗簽 session 解出的身分。

### 7.2 `auth.py` 尚待補強

| # | 現況 | 風險 | 改為 |
|---|---|---|---|
| 1 | 密碼明文存 `.env` | 檔案外洩即全毀 | bcrypt / argon2 雜湊存 DB |
| 2 | Token 無法撤銷 | 登出僅刪 cookie；token 到期前永遠有效 | 加 `session_version`，改密碼／登出即失效 |
| 3 | 鎖定為 in-memory、per-IP | 重啟即清空、多 worker 不共享；擋不住分散 IP，又誤傷 NAT | 落 DB，per-account 也鎖 |
| 4 | `samesite="lax"` | 已擋多數跨站 POST，但擋不了同站不同子網域 | `Strict` ＋ 狀態變更端點加 CSRF token |
| 5 | 無 Email 驗證／密碼重設 | 註冊灌水、無自助救援 | 驗證信 ＋ 有時效的一次性 token |

### 7.3 金流紅線（第二波）

- **升級只能由已驗簽的 webhook 觸發**。絕不信任前端回報付款成功。
- **webhook 必須冪等**，以金流商交易序號為唯一鍵。

### 7.4 濫用防護

AI 助手鎖 Pro 後，漏財點是帳號共享（被當免費 Claude proxy）。即使 Pro 為「無限」，仍需 per-account 速率上限。布林鎖決定**能不能用**，速率閘決定**能用多快**。

---

## 8. 測試策略

| 類別 | 斷言 |
|---|---|
| 註冊表完整性 | 啟動時走訪 `app.routes`，未登記者 → 啟動失敗 |
| 越權（垂直） | Free 帳號打每一支 Pro 端點 → 403；Pro 打每一支 Admin 端點 → 403 |
| 越權（水平／IDOR） | A 帳號存取 B 的資源 ID → **404**（非 403） |
| 未驗證 | 未登入打任何 `/api/*`（含各種 Accept 變體）→ 401 ✅ 已完成 |
| 容量上限 | Free 第 101 檔持股 → 422；第 2 組清單 → 422 |
| 參數白名單 | 非 Admin 送白名單外的 `paper/simulate` 參數 → 422 |
| 欄位級分層 | Free 打 `/recommendations` 的回應**不得含** `prob_hit`／`prob_n`／`prob_cond`／`prob_mae`／`confidence`／`stability`／`coverage`／`details`／`ml_consensus`／`target_zone`／`graduation`；且**必須含** `prob_band`／`risk_band`。Pro 反之。以欄位名黑白名單斷言，避免日後新增欄位默默外洩 |
| 質化分級穩健性 | 以多頭／空頭兩組模擬清單餵入，斷言三級各自都有成員（驗證相對分位未退化為大盤指標） |
| 資料隔離 | Repository 在無 `user_id` 下無法建構 |

---

## 9. 執行順序

八個子系統，各自一輪 spec → plan → 實作：

| # | 子系統 | 波次 |
|---|---|---|
| 1 | 資料多租戶隔離（加 `user_id` ＋ repository 層 ＋ 遷移） | 一 |
| 2 | 身分系統（註冊／登入／Email 驗證／密碼重設／可撤銷 session） | 一 |
| 3 | Entitlement 註冊表 ＋ 權限閘 ＋ 前端鎖定 UI | 一 |
| 4 | `settings` 三分 ＋ 評分請求時套用 | 一 |
| 5 | 主動通知（Email／LINE） | 二 |
| 6 | 績效報表 ＋ CSV 匯出 | 二 |
| 7 | 自訂選股器 ＋ 組合警示 | 二 |
| 8 | 金流與訂閱 ＋ 管理後台 | 二 |

**先隔離再身分**——隔離做錯了後面全部要重做。

**兩波上線**：
- **第一波**：多租戶 ＋ 身分 ＋ 免費層 ＋ Admin 策略室。無金流。累積用戶、驗證需求。
- **第二波**：四個新功能 ＋ Pro ＋ 金流。

Pro 的既有功能（AI 助手、籌碼進階、個股進階、配分自訂、限定參數紙上模擬）不足以單獨支撐訂閱定價，故 Pro 必須等第二波的新功能完成。

---

## 10. 未決事項

1. **法律意見** —— 收費提供個股分析在台灣的執照要求，須專業意見確認。
2. **Pro 定價** —— 待第二波功能範圍確定後決定。
3. **SQLite 併發** —— 公開多用戶下的寫入併發尚未評估；`_raise_if_db_locked` 顯示已有 lock 問題。可能需遷移 Postgres，屬第 7 項基礎建設。
4. **紙上模擬白名單寬度** —— 12 種組合是否過寬，待實際使用觀察。
