@echo off
rem Build standalone PyIDM executables into dist\
rem Requires: pip install -e . pyinstaller
setlocal
set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python

"%PY%" -m PyInstaller --onefile --noconfirm --name pyidm entry_cli.py || goto :err
"%PY%" -m PyInstaller --onefile --noconfirm --name pyidm-gui --windowed entry_gui.py || goto :err

echo.
echo Done. Executables are in dist\pyidm.exe and dist\pyidm-gui.exe
exit /b 0

:err
echo Build failed.
exit /b 1
