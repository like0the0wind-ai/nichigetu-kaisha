@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo シフト管理アプリを起動中...
echo http://127.0.0.1:5050 をブラウザで開いてください
start http://127.0.0.1:5050
python app.py
pause
