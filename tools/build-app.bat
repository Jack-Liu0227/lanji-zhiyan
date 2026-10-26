@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.." || exit /b 1
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=%CD%\.venv\Scripts\python.exe"
if not defined PY (
  for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%P"
)
if not defined PY (
  for /f "delims=" %%P in ('where python 2^>nul') do if not defined PY set "PY=%%P"
)
if not defined PY (
  echo [ERROR] Python 3.10 or newer was not found.
  if /i not "%~1"=="--no-pause" pause
  exit /b 1
)
"%PY%" tools\build.py
set "BUILD_RESULT=%ERRORLEVEL%"
if /i not "%~1"=="--no-pause" pause
exit /b %BUILD_RESULT%
