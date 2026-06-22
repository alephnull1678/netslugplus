@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "IMAGE=netslug-old-devkitpro:r27-libogc-1.8.12"
if not "%NETSLUG_OLD_DEVKITPRO_IMAGE%"=="" set "IMAGE=%NETSLUG_OLD_DEVKITPRO_IMAGE%"
if not "%~1"=="" set "IMAGE=%~1"
set "LOG_DIR=%SCRIPT_DIR%logs"
set "LOG_FILE=%LOG_DIR%\build-container.log"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

docker build -f "%SCRIPT_DIR%..\Dockerfile" -t "%IMAGE%" "%SCRIPT_DIR%.." > "%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"
type "%LOG_FILE%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo Build failed. Full log: "%LOG_FILE%"
  pause
)

exit /b %EXIT_CODE%
