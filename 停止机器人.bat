@echo off
for /f "tokens=5" %p in ('netstat -ano ^| findstr :8790 ^| findstr LISTENING') do taskkill /PID %p /F
echo 已停止
pause
