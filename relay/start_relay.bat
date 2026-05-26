@echo off
setlocal

cd /d "%~dp0"
python netslug_relay.py

echo.
echo Relay server stopped.
pause
