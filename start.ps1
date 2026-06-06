# CHWL Agent - one-click launcher
$ROOT = Split-Path $MyInvocation.MyCommand.Path

Write-Host "=== CHWL Agent - starting ===" -ForegroundColor Cyan

Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ROOT'; .\venv\Scripts\Activate.ps1; python -X utf-8 api\app.py"
Start-Sleep -Seconds 2

Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ROOT\frontend'; npm run dev"

Write-Host "Backend: http://localhost:8000" -ForegroundColor Green
Write-Host "Frontend: http://localhost:5173" -ForegroundColor Green
Write-Host "Waiting for startup... then open browser." -ForegroundColor Yellow
