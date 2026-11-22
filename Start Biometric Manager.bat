@echo off
rem Monitoring: "Start Biometric Manager.bat" --monitor-only --profile "C:\Private\monitor.json"
rem Synthetic monitoring preview: "Start Biometric Manager.bat" --demo --monitor-only
rem Read-only investigation: "Start Biometric Manager.bat" --investigation-only --profile "C:\Private\investigation.json"
setlocal
cd /d "%~dp0"
set "MANAGER_PY=%~dp0.venv\Scripts\python.exe"
if not exist "%MANAGER_PY%" (
  echo Manager environment missing. Create it using Python 3.11:
  echo py -3.11 -m venv .venv
  echo .venv\Scripts\python.exe -m pip install -r requirements-manager.txt
  pause
  exit /b 1
)
"%MANAGER_PY%" -B -c "import sys,flask,waitress,requests; assert sys.version_info[:2] == (3,11)" >nul 2>&1
if errorlevel 1 (
  echo Python 3.11 or manager dependencies are missing. See docs\phase2c-manager.md.
  pause
  exit /b 1
)
"%MANAGER_PY%" -B "%~dp0scripts\biometric_manager.py" %*
if errorlevel 1 (
  echo Startup failed. Review the message above; the synchronization service was not changed.
  pause
  exit /b 1
)
endlocal
