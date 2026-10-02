@echo off
setlocal

set "PYTHONDONTWRITEBYTECODE=1"
set "APP_DIR=%~dp0"
set "DATA_DIR=%USERPROFILE%\Desktop\Instagram Exporter Data"
set "DB_PATH=%DATA_DIR%\instagram.db"
set "TOKEN_PATH=%DATA_DIR%\server-token.txt"
set "CONFIG_PATH=%APP_DIR%config.js"

if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"

if exist "%APP_DIR%ib-circlio-launcher.exe" (
  "%APP_DIR%ib-circlio-launcher.exe" --data-dir "%DATA_DIR%" --config "%CONFIG_PATH%"
) else (
  where py >nul 2>&1
  if errorlevel 1 (
    echo IB Circlio is missing its packaged launcher.
    echo Use a release bundle or install Python 3 for development mode.
    pause
    exit /b 1
  )
  py -3 "%APP_DIR%launcher.py" --data-dir "%DATA_DIR%" --config "%CONFIG_PATH%"
)
if errorlevel 1 (
  echo Could not prepare the local configuration.
  pause
  exit /b 1
)

echo Instagram Exporter local database
echo Data folder: "%DATA_DIR%"
echo Persistent local authentication configured.
echo No extension reload is needed when restarting this server.
echo Keep this window open while using the extension.
echo.
if exist "%APP_DIR%ib-circlio-server.exe" (
  "%APP_DIR%ib-circlio-server.exe" --db "%DB_PATH%" --token-file "%TOKEN_PATH%"
) else (
  py -3 "%APP_DIR%server.py" --db "%DB_PATH%" --token-file "%TOKEN_PATH%"
)

echo.
echo The local database server stopped.
pause
