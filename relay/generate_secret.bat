@echo off
setlocal

cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; $bytes = New-Object byte[] 32; $rng = [Security.Cryptography.RandomNumberGenerator]::Create(); try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }; [Convert]::ToBase64String($bytes) | Set-Content -NoNewline -Encoding ascii 'relay_secret.txt'"

if errorlevel 1 (
    echo Failed to generate relay_secret.txt.
) else (
    echo Generated relay_secret.txt.
)

pause
