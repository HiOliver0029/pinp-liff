# Epoch PINP 啟動腳本
$PYTHON = 'C:\Users\OliverLin\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\python.exe'

Write-Host "▶ 啟動 FastAPI 伺服器 (port 8000)..." -ForegroundColor Cyan
& $PYTHON main.py
