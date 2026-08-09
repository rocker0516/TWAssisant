#!/usr/bin/env bash
# TWAssistant 一鍵啟動（macOS 雙擊即可）
# 單一伺服器：後端(:8000) 直接吃打包好的前端 dist，開一個瀏覽器就是整個 App。
# 首次執行會自動建 venv / 裝依賴 / 打包前端；之後秒開。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PORT=8000
URL="http://127.0.0.1:${PORT}"   # 與 uvicorn --host 127.0.0.1 對齊（避開 localhost 走 IPv6 ::1）
VENV="$ROOT/backend/.venv"
PY="$VENV/bin/python"

echo "───────────────────────────────────────────"
echo " TWAssistant 台股操作助手 — 啟動中"
echo "───────────────────────────────────────────"

# 1) 後端虛擬環境 + 依賴（缺才裝）
if [ ! -x "$PY" ]; then
  echo "▶ 首次啟動：建立 Python 虛擬環境…"
  python3 -m venv "$VENV"
  "$PY" -m pip install --quiet --upgrade pip
  echo "▶ 安裝後端依賴（約 1~2 分鐘）…"
  "$PY" -m pip install --quiet -r "$ROOT/backend/requirements.txt"
  "$PY" -m pip install --quiet "anthropic>=0.40"   # AI 功能，未設金鑰則自動略過
fi

# 2) 前端打包產物（缺、或原始碼比 dist 新 → 重打包；需要 npm）
DIST_INDEX="$ROOT/frontend/dist/index.html"
NEED_BUILD=0
if [ ! -f "$DIST_INDEX" ]; then
  NEED_BUILD=1                                   # 還沒打包過
elif [ -n "$(find "$ROOT/frontend/src" "$ROOT/frontend/index.html" -newer "$DIST_INDEX" -print -quit 2>/dev/null)" ]; then
  NEED_BUILD=1                                   # 前端有改動 → dist 過期，重打包（避免看到舊版）
fi
if [ "$NEED_BUILD" = "1" ]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "✗ 找不到 npm，無法打包前端。請先安裝 Node.js（https://nodejs.org）後重試。"
    read -r -p "按 Enter 關閉…" _
    exit 1
  fi
  echo "▶ 打包前端（偵測到首次或原始碼有更新）…"
  [ -d "$ROOT/frontend/node_modules" ] || npm --prefix "$ROOT/frontend" install
  npm --prefix "$ROOT/frontend" run build
fi

# 3) 若 8000 已被占用，視為已在跑，直接開瀏覽器
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "▶ 偵測到伺服器已在執行，直接開啟瀏覽器。"
  open "$URL"
  exit 0
fi

# 4) 啟動後端（單一行程；含內建每日排程）
echo "▶ 啟動伺服器 :$PORT …"
"$VENV/bin/uvicorn" app.main:app --app-dir backend --host 127.0.0.1 --port "$PORT" &
SERVER_PID=$!

# 關閉視窗 / Ctrl-C 時一併收掉伺服器
cleanup() { echo; echo "▶ 關閉伺服器…"; kill "$SERVER_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

# 5) 等健康檢查通過再開瀏覽器
echo -n "▶ 等待伺服器就緒"
for _ in $(seq 1 40); do
  if curl -fs "$URL/health" >/dev/null 2>&1; then
    echo " ✓"
    open "$URL"
    break
  fi
  echo -n "."
  sleep 0.5
done

echo "───────────────────────────────────────────"
echo " 已開啟 $URL"
echo " 關掉這個視窗（或按 Ctrl-C）即可停止伺服器。"
echo "───────────────────────────────────────────"

# 保持前景，讓伺服器持續運作
wait "$SERVER_PID"
