@echo off
setlocal
set "APP_DIR=%~dp0"
set "DATA_DIR=%USERPROFILE%\Desktop\Instagram Exporter Data"
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
