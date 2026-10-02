@echo off
title Kamal Express - GVC Slot Scout
color 0A
echo ========================================================
echo       KAMAL EXPRESS - 1-CLICK GVC SLOT SCOUT
echo ========================================================
echo.
echo Starting slot availability checker using config.json...
echo.

cd /d "%~dp0"
python checker.py

echo.
pause
