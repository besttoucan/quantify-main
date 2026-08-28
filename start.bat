@echo off
setlocal
cd /d "%~dp0"

if exist "config.bat" call "config.bat"
set "PYTHONUTF8=1"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 server.py --open
  goto :done
)

where python >nul 2>nul
if %errorlevel%==0 (
  python server.py --open
  goto :done
)

echo.
echo Quantify needs Python 3.11 or newer.
echo Install Python from python.org, select "Add Python to PATH", then run start.bat again.
echo.
pause
exit /b 1

:done
if errorlevel 1 (
  echo.
  echo Quantify stopped because of an error. Review the message above.
  pause
)
endlocal
