@echo off
rem Double-click to open Deal Sniper. Works from any location: it moves to this
rem folder first, which is what "python -m app.web" needs.
cd /d "%~dp0"
title Deal Sniper
echo Starting Deal Sniper... (close this window to stop it)
python -m app.web
if errorlevel 1 (
  echo.
  echo Something went wrong. Is Python installed and on your PATH?
  echo Try running:  python --version
)
pause
