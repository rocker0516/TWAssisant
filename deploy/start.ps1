# TWAssistant 一鍵啟動（由 start.bat 呼叫；Windows 網站模式）
# 單一伺服器：後端(:8000) 直接吃打包好的前端 dist。
# 首次執行：自動建 venv / 裝依賴 / 打包前端 / 產生登入密碼；之後秒開。
param([string]$Mode = "")

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot   # deploy/ 的上一層 = 專案根目錄
Set-Location $Root

$Port = 8000
$Venv = Join-Path $Root "backend\.venv"
$Py = Join-Path $Venv "Scripts\python.exe"
$Uvicorn = Join-Path $Venv "Scripts\uvicorn.exe"
$Url = "http://127.0.0.1:$Port"

function Fail($msg) {
    Write-Host ""
    Write-Host "[X] $msg" -ForegroundColor Red
    Read-Host "按 Enter 關閉"
    exit 1
}

Write-Host "============================================"
Write-Host " TWAssistant 台股操作助手 - 啟動中"
Write-Host "============================================"

# 1) Python 虛擬環境 + 依賴（缺才裝）
if (-not (Test-Path $Py)) {
    Write-Host "[1/4] 首次啟動：建立 Python 虛擬環境..."
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) { & py -3 -m venv $Venv } else { & python -m venv $Venv }
    if (-not (Test-Path $Py)) {
        Fail "找不到 Python。請先安裝 Python 3.11+ (https://www.python.org) 後重試。"
    }
}
if (-not (Test-Path $Uvicorn)) {
    Write-Host "[1/4] 安裝後端依賴（約 1~2 分鐘）..."
    & $Py -m pip install --quiet --upgrade pip
    & $Py -m pip install --quiet -r (Join-Path $Root "backend\requirements.txt")
    & $Py -m pip install --quiet "anthropic>=0.40"
    if (-not (Test-Path $Uvicorn)) { Fail "後端依賴安裝失敗，請檢查上方錯誤訊息。" }
}

# 2) 首次啟動：產生登入帳密寫入 backend\.env
$EnvFile = Join-Path $Root "backend\.env"
if (-not (Test-Path $EnvFile)) {
    Write-Host "[2/4] 首次啟動：產生登入密碼..."
    $chars = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    $pw = -join (1..16 | ForEach-Object { $chars[(Get-Random -Maximum $chars.Length)] })
    [System.IO.File]::WriteAllText($EnvFile, "TWA_AUTH_USERNAME=admin`nTWA_AUTH_PASSWORD=$pw`n", (New-Object System.Text.UTF8Encoding $false))
    Write-Host ""
    Write-Host " ==========================================" -ForegroundColor Yellow
    Write-Host "  登入帳號：admin" -ForegroundColor Yellow
    Write-Host "  登入密碼：$pw" -ForegroundColor Yellow
    Write-Host "  （已存於 backend\.env，可自行修改）" -ForegroundColor Yellow
    Write-Host " ==========================================" -ForegroundColor Yellow
    Write-Host ""
}

# 3) 前端打包產物（缺才打包；改過前端請執行「start.bat build」強制重打包）
$DistIndex = Join-Path $Root "frontend\dist\index.html"
$needBuild = (-not (Test-Path $DistIndex)) -or ($Mode -eq "build")
if ($needBuild) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Fail "找不到 npm，無法打包前端。請先安裝 Node.js (https://nodejs.org) 後重試。"
    }
    Write-Host "[3/4] 打包前端..."
    if (-not (Test-Path (Join-Path $Root "frontend\node_modules"))) {
        & npm --prefix frontend install
    }
    & npm --prefix frontend run build
    if (-not (Test-Path $DistIndex)) { Fail "前端打包失敗，請檢查上方錯誤訊息。" }
}

# 4) 若 8000 已被占用，視為已在跑，直接開瀏覽器
$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Write-Host "[4/4] 偵測到伺服器已在執行，直接開啟瀏覽器。"
    Start-Process $Url
    exit 0
}

# 5) 啟動後端（綁 0.0.0.0：區網其他裝置也能連；防火牆詢問請按「允許」）
Write-Host "[4/4] 啟動伺服器 :$Port ...（關掉這個視窗即停止）"
# 背景等健康檢查通過再開瀏覽器
Start-Job -ScriptBlock {
    param($u)
    foreach ($i in 1..40) {
        try { Invoke-RestMethod "$u/health" -TimeoutSec 1 | Out-Null; Start-Process $u; break }
        catch { Start-Sleep -Milliseconds 500 }
    }
} -ArgumentList $Url | Out-Null

& $Uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port $Port
Read-Host "伺服器已停止，按 Enter 關閉"
