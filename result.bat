@echo off
setlocal
set "PYTHONDONTWRITEBYTECODE=1"
set "APP_DIR=%~dp0"
set "DATA_DIR=%USERPROFILE%\Desktop\Instagram Exporter Data"
if "%~1"=="" (
  if exist "%APP_DIR%ib-circlio-report.exe" (
    start "" "%APP_DIR%ib-circlio-report.exe" --data-dir "%DATA_DIR%"
    exit /b 0
  )
  where py >nul 2>&1
  if errorlevel 1 (
    where pyw >nul 2>&1
    if errorlevel 1 (
      echo IB Circlio desktop report app is missing.
      pause
      exit /b 1
    )
    start "" pyw -3 "%APP_DIR%result_gui.py" --data-dir "%DATA_DIR%"
    exit /b 0
  )
  start "" pyw -3 "%APP_DIR%result_gui.py" --data-dir "%DATA_DIR%"
  exit /b 0
)
if exist "%APP_DIR%ib-circlio-result.exe" (
  "%APP_DIR%ib-circlio-result.exe" --data-dir "%DATA_DIR%" %*
) else (
  where py >nul 2>&1
  if errorlevel 1 (
    echo IB Circlio report executable is missing.
    pause
    exit /b 1
  )
  py -3 "%APP_DIR%result.py" --data-dir "%DATA_DIR%" %*
)
echo.
pause
