@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "IMAGE=netslug-old-devkitpro:r27-libogc-1.8.12"
if not "%NETSLUG_OLD_DEVKITPRO_IMAGE%"=="" set "IMAGE=%NETSLUG_OLD_DEVKITPRO_IMAGE%"
set "TARGET=%~1"
set "MAKE_TARGET=%~2"
set "LOG_DIR=%SCRIPT_DIR%logs"
set "LOG_FILE=%LOG_DIR%\compile-source.log"

if "%TARGET%"=="" set "TARGET=%SCRIPT_DIR%.."
if /I "%TARGET%"=="original" set "TARGET=%SCRIPT_DIR%netslug-wii-original"
if "%MAKE_TARGET%"=="" set "MAKE_TARGET=release"

for %%I in ("%TARGET%") do set "TARGET=%%~fI"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

docker run --rm -v "%TARGET%:/work" -w /work "%IMAGE%" make clean "%MAKE_TARGET%" > "%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"
type "%LOG_FILE%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo Compile failed. Full log: "%LOG_FILE%"
  pause
)

exit /b %EXIT_CODE%
