@echo off
setlocal

set "PYTHONDONTWRITEBYTECODE=1"
set "APP_DIR=%~dp0"
set "DATA_DIR=%LOCALAPPDATA%\IB Circlio"
if not defined LOCALAPPDATA set "DATA_DIR=%USERPROFILE%\AppData\Local\IB Circlio"
set "LEGACY_DATA_DIR=%USERPROFILE%\Desktop\Instagram Exporter Data"
if exist "%LEGACY_DATA_DIR%" if not exist "%DATA_DIR%" set "DATA_DIR=%LEGACY_DATA_DIR%"

if exist "%APP_DIR%ib-circlio-clear-database.exe" (
  "%APP_DIR%ib-circlio-clear-database.exe" --data-dir "%DATA_DIR%"
) else (
  where py >nul 2>&1
  if errorlevel 1 (
    echo IB Circlio database utility is missing.
    pause
    exit /b 1
  )
  py -3 "%APP_DIR%clear_database.py" --data-dir "%DATA_DIR%"
)
echo.
pause
