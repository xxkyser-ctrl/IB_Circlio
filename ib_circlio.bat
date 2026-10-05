@echo off
setlocal
set "PYTHONDONTWRITEBYTECODE=1"
set "APP_DIR=%~dp0"
set "DATA_DIR=%LOCALAPPDATA%\IB Circlio"
if not defined LOCALAPPDATA set "DATA_DIR=%USERPROFILE%\AppData\Local\IB Circlio"
set "LEGACY_DATA_DIR=%USERPROFILE%\Desktop\Instagram Exporter Data"
if exist "%LEGACY_DATA_DIR%" if not exist "%DATA_DIR%" set "DATA_DIR=%LEGACY_DATA_DIR%"

if exist "%APP_DIR%ib_circlio.exe" (
  start "" "%APP_DIR%ib_circlio.exe" --data-dir "%DATA_DIR%"
  exit /b 0
)

where pyw >nul 2>&1
if errorlevel 1 (
  echo IB_Circlio desktop app is missing.
  echo Use a portable release or install Python 3 for source mode.
  pause
  exit /b 1
)

start "" pyw -3 "%APP_DIR%result_gui.py" --data-dir "%DATA_DIR%"
exit /b 0
