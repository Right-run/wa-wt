@echo off
rem ============================================================
rem   Build a standalone grp2step.exe (result in "exe_build" folder)
rem   Copy the whole "exe_build" folder to any PC - no Python needed.
rem ============================================================
echo Installing packager ...
python -m pip install pyinstaller >nul
echo Packaging (takes 2-8 minutes) ...
pyinstaller --noconfirm --clean --onedir --name grp2step ^
  --add-data "daelib;daelib" ^
  --collect-all zstandard ^
  grp2step.py
if errorlevel 1 ( echo Packaging failed & pause & exit /b 1 )
if exist exe_build rmdir /s /q exe_build
mkdir exe_build\lib
copy dist\grp2step\grp2step.exe exe_build\ >nul
copy daelib\lib\daKernel-dev.dll exe_build\lib\ >nul
xcopy /e /i /q dist\grp2step\_internal exe_build\_internal >nul
echo.
echo Done! Copy the whole "exe_build" folder to any PC and run grp2step.exe directly.
pause
