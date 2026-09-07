@echo off
chcp 65001 >nul
title Zoho Mail
cd /d "%~dp0"
python zoho.py setup
echo.
pause
