@echo off
title Kamal Express - GVC Drop Booker
color 0B
echo ========================================================
echo       KAMAL EXPRESS - 1-CLICK GVC DROP BOOKER
echo ========================================================
echo.
echo Arming slot-drop booker using config.json...
echo Applicant: reading next queued client
echo.

cd /d "%~dp0"
python booker.py

echo.
pause
