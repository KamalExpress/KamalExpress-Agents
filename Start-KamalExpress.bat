@echo off
title Kamal Express AI Platform
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File "%~dp0start-vps.ps1"
pause
