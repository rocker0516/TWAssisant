@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\start.ps1" %1
if errorlevel 1 pause
