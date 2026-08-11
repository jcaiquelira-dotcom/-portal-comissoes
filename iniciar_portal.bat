@echo off
cd /d "%~dp0"
start "Portal de Comissoes - servidor" "C:\Users\José Caique\AppData\Local\Programs\Python\Python312\python.exe" app\server.py
timeout /t 2 /nobreak >nul
start "" http://localhost:8010
