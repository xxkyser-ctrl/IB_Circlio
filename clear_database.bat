@echo off
setlocal

set "PYTHONDONTWRITEBYTECODE=1"
set "APP_DIR=%~dp0"
set "DATA_DIR=%USERPROFILE%\Desktop\Instagram Exporter Data"

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
