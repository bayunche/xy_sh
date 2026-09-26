@echo off
chcp 65001 >nul
title xy-dsh-robot
powershell -ExecutionPolicy Bypass -File "%~dp0runun-app.ps1"
pause
