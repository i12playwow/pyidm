@echo off
rem Build a SAC-proof portable PyIDM: python.org embeddable Python + site dir.
rem The frozen PyInstaller exes can be blocked by Smart App Control (no
rem sign code -- plain script files everywhere.
rem Tk source install (DLLs, tcl, tkinter) defaults to the machine's Python312
rem under LOCALAPPDATA; override with PY312=<dir> when it lives elsewhere (CI).
setlocal
set "ROOT=%~dp0"
if not defined PY312 set "PY312=%LOCALAPPDATA%\Programs\Python\Python312"
set "PY=%PY312%\python.exe"
set "VER=3.12.10"
set "OUT=%ROOT%portable\PyIDM"

if not exist "%ROOT%portable\python-embed.zip" (
  echo Downloading python-%VER%-embed-amd64.zip ...
  powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/%VER%/python-%VER%-embed-amd64.zip' -OutFile '%ROOT%portable\python-embed.zip' } catch { exit 1 }"
  if errorlevel 1 (
    echo Download failed. Get it manually from python.org/downloads/windows ^(embeddable package^).
    exit /b 1
  )
)

if exist "%OUT%" rmdir /s /q "%OUT%"
mkdir "%OUT%" || exit /b 1
mkdir "%OUT%\site"

echo Extracting embeddable Python ...
powershell -NoProfile -Command "Expand-Archive -Force '%ROOT%portable\python-embed.zip' '%OUT%\python'"

echo Copying Tk support from the installed Python ...
mkdir "%OUT%\python\DLLs" 2>nul
copy /y "%PY312%\DLLs\_tkinter.pyd" "%OUT%\python\DLLs\" || exit /b 1
copy /y "%PY312%\DLLs\tcl86t.dll" "%OUT%\python\DLLs\" || exit /b 1
copy /y "%PY312%\DLLs\tk86t.dll" "%OUT%\python\DLLs\" || exit /b 1
copy /y "%PY312%\DLLs\zlib1.dll" "%OUT%\python\DLLs\" || exit /b 1
xcopy /e /i /q /y "%PY312%\tcl" "%OUT%\python\tcl" >nul
xcopy /e /i /q /y "%PY312%\Lib\tkinter" "%OUT%\python\Lib\tkinter" >nul
> "%OUT%\python\python312._pth" echo python312.zip
>>"%OUT%\python\python312._pth" echo .
>>"%OUT%\python\python312._pth" echo Lib
>>"%OUT%\python\python312._pth" echo DLLs
>>"%OUT%\python\python312._pth" echo ..\site

echo Installing dependencies into site\ ...
"%PY%" -m pip install -q --target "%OUT%\site" requests rich || exit /b 1

echo Copying the idm package ...
xcopy /e /i /q /y "%ROOT%idm" "%OUT%\site\idm" >nul

rem Static launcher files: pyidm.bat execs cli_main.py (no -c quoting sandwich,
rem so shell-significant characters in --query survive); pyidm.sh gives POSIX
rem shells a native launcher that never goes through cmd.exe at all.
copy /y "%ROOT%portable\cli_main.py" "%OUT%\cli_main.py" || exit /b 1
copy /y "%ROOT%portable\pyidm.sh" "%OUT%\pyidm.sh" >nul || exit /b 1
copy /y "%ROOT%portable\smoke_test.sh" "%OUT%\smoke_test.sh" >nul || exit /b 1
powershell -NoProfile -Command "foreach ($f in 'pyidm.sh','smoke_test.sh') { $p=\"%OUT%\\$f\"; $t=[IO.File]::ReadAllText($p); $t=$t.Replace([string][char]13+[string][char]10,[string][char]10); [IO.File]::WriteAllText($p,$t) }"

(
  echo @echo off
  echo set "HERE=%%~dp0"
  echo "%%HERE%%python\python.exe" "%%HERE%%cli_main.py" %%*
) > "%OUT%\pyidm.bat"

(
  echo @echo off
  echo set "HERE=%%~dp0"
  echo start "" "%%HERE%%python\pythonw.exe" -c "import sys; sys.path.insert(0, r'%%HERE%%site'); from idm.gui import main; main()"
) > "%OUT%\pyidm-gui.bat"

echo Done: %OUT%  ^(pyidm.bat, pyidm.sh, smoke_test.sh, pyidm-gui.bat^)

echo Packing PyIDM-portable.zip ...
"%PY%" "%ROOT%portable_zip.py" "%OUT%" "%ROOT%portable\PyIDM-portable.zip" || exit /b 1
echo Done: %ROOT%portable\PyIDM-portable.zip
endlocal
