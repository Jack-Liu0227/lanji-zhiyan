@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.." || exit /b 1

rem Resolve a console interpreter first, rather than trusting pyw to exist.
set "PY="
if exist ".venv\Scripts\python.exe" call :select_python "%CD%\.venv\Scripts\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" call :select_python "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not defined PY (
  for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do call :select_python "%%P"
)
if not defined PY (
  for /f "delims=" %%P in ('where python 2^>nul') do if not defined PY call :select_python "%%P"
)
if not defined PY goto :no_python

rem Import failures occur before server.py's own error handler is installed.
if not exist "data" mkdir "data"
set "LOG=%CD%\data\startup.log"
"%PY%" -c "import runpy, sys; print('Python:', sys.executable, flush=True); runpy.run_path('apps/desktop/server.py', run_name='startup_check')" > "%LOG%" 2>&1
if errorlevel 1 goto :failed

rem Use pythonw from the exact environment that passed the startup check.
for %%P in ("%PY%") do set "PYW=%%~dpPpythonw.exe"
if not exist "%PYW%" set "PYW=%PY%"
start "" "%PYW%" "%CD%\apps\desktop\server.py" %*
if errorlevel 1 goto :failed
exit /b 0

:select_python
"%~1" -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 set "PY=%~1"
exit /b 0

:no_python
echo [ERROR] Python 3.10 or newer was not found.
echo Install Python, then run: python -m pip install -r requirements.txt
pause
exit /b 1

:failed
echo [ERROR] Application startup check or process launch failed.
if exist "%LOG%" type "%LOG%"
echo.
echo Check dependencies with:
echo "%PY%" -m pip install -r "%CD%\requirements.txt"
echo Log: "%LOG%"
pause
exit /b 1
