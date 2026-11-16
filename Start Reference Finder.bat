@echo off
setlocal
title Atomized Reference Finder
pushd "%~dp0"
if errorlevel 1 (
    echo Could not open the project folder.
    pause
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    echo The project Python environment was not found.
    echo Follow the Setup instructions in README.md, then try again.
    popd
    pause
    exit /b 1
)
".venv\Scripts\python.exe" "run.py" --open-browser %*
set "launcher_exit=%errorlevel%"
if not "%launcher_exit%"=="0" (
    echo.
    echo The workspace could not start. Review the message above.
    pause
)
popd
exit /b %launcher_exit%
