@echo off
title Kamal Express VPS Setup
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-vps.ps1"
pause
