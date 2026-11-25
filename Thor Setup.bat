@echo off
rem Double-click to open the AYN Thor Setup Helper menu.
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (python thor_setup.py %*) else (py -3 thor_setup.py %*)
echo.
pause
