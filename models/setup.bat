@echo off
rem ============================================================
rem   grp2step dependency setup (one-time, needs internet)
rem   Run this before using convert.bat on a fresh machine.
rem ============================================================
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo         Install Python 3.10+ from https://www.python.org/downloads/
    echo         and tick "Add Python to PATH" during install.
    pause
    exit /b 1
)
python --version
echo Installing dependencies (zstandard / pylzma / PyQt5 / cadquery-ocp) ...
python -m pip install --upgrade pip
python -m pip install zstandard pylzma PyQt5 cadquery-ocp
if errorlevel 1 (
    echo [ERROR] Installing dependencies failed. Check your network and retry.
    pause
    exit /b 1
)
echo.
echo [DONE] Dependencies ready. You can now drag a .grp file onto convert.bat.
pause
