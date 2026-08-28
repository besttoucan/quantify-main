@echo off
setlocal
cd /d "%~dp0"

if exist "config.bat" call "config.bat"
set "PYTHONUTF8=1"

echo.
echo Quantify will be available to devices on this Wi-Fi network at port 8787.
echo Quantify requires the owner login and two-factor authentication.
echo Use this only on a trusted private network; it is not a public internet deployment.
echo Press Ctrl+C in this window to stop the server.
echo.

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 server.py --host 0.0.0.0 --port 8787
  goto :done
)

where python >nul 2>nul
if %errorlevel%==0 (
  python server.py --host 0.0.0.0 --port 8787
  goto :done
)

echo Quantify needs Python 3.11 or newer.
echo Install Python from python.org, select "Add Python to PATH", then run this file again.
pause
exit /b 1

:done
if errorlevel 1 pause
endlocal
