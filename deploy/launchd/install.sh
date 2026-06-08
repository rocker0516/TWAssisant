#!/usr/bin/env bash
# 安裝 / 重載 每日盤後排程（macOS launchd）
set -euo pipefail

PLIST_SRC="$(cd "$(dirname "$0")" && pwd)/com.twassistant.daily.plist"
LABEL="com.twassistant.daily"
DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"

cp "$PLIST_SRC" "$DEST"

# 已載入則先卸載再載入（冪等）
launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"

echo "已載入排程 ${LABEL}（每日 21:30）。"
echo "查看狀態： launchctl print gui/$(id -u)/${LABEL}"
echo "立即測跑： launchctl kickstart -k gui/$(id -u)/${LABEL}"
echo "卸載：     launchctl bootout gui/$(id -u)/${LABEL}"
