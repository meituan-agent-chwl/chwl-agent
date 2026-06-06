# CHWL Agent - 一键启动
# 用法: powershell -ExecutionPolicy Bypass -File start.ps1

$ROOT = Split-Path $MyInvocation.MyCommand.Path

Write-Host "=== CHWL Agent - 一键启动 ===" -ForegroundColor Cyan

# 启动后端（新窗口）
Start-Process powershell -ArgumentList "-NoExit", "-Command", "
  Set-Location '$ROOT'
  .\venv\Scripts\Activate.ps1
  python -X utf-8 api\app.py
"

Start-Sleep -Seconds 3

# 启动前端（新窗口）
Start-Process powershell -ArgumentList "-NoExit", "-Command", "
  Set-Location '$ROOT\frontend'
  npm run dev
"

Write-Host "`n后端: http://localhost:8000" -ForegroundColor Green
Write-Host "前端: http://localhost:5173" -ForegroundColor Green
Write-Host "`n两个新窗口已打开，等待启动后刷新浏览器。" -ForegroundColor Yellow
