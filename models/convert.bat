@echo off
rem ============================================================
rem   Convert a Dagor .grp pack to OBJ (LOD0, full precision)
rem   Drag a .grp file onto this .bat, or run:
rem       convert.bat path\to\file.grp
rem   Output: <project root>\output\models
rem ============================================================
if "%~1"=="" (
    echo Usage: drag a .grp file onto this convert.bat
    pause
    exit /b 1
)
python "%~dp0grp2step.py" "%~1" --out "%~dp0..\..\output\models" --obj
echo.
echo Conversion finished. Results are in output\models (next to the tools folder).
pause
