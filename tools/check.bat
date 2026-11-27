@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.." || exit /b 1

set "NO_PAUSE="
if /i "%~1"=="--no-pause" set "NO_PAUSE=1"
set "PY="
if exist ".venv\Scripts\python.exe" call :select_python "%CD%\.venv\Scripts\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" call :select_python "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not defined PY (
  for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do call :select_python "%%P"
)
if not defined PY (
  for /f "delims=" %%P in ('where python 2^>nul') do if not defined PY call :select_python "%%P"
)
if not defined PY (
  echo [ERROR] Python 3.10 or newer was not found.
  goto :failed
)

echo === 1/7 Python checks ===
"%PY%" -m unittest discover -s tests -t .
if errorlevel 1 goto :failed
echo.
echo === 2/7 Frontend theme checks ===
node "tests\frontend\theme.test.mjs"
if errorlevel 1 goto :failed
echo.
echo === 3/7 DOM id checks ===
node "tests\frontend\ids.test.mjs"
if errorlevel 1 goto :failed
echo.
echo === 4/7 Workflow graph checks ===
node "tests\frontend\workflow-graph.test.mjs"
if errorlevel 1 goto :failed
echo.
echo === 5/7 Workflow file preview checks ===
node "tests\frontend\workflow-file-preview.test.mjs"
if errorlevel 1 goto :failed
echo.
echo === 6/7 Split resizer checks ===
node "tests\frontend\split-resizer.test.mjs"
if errorlevel 1 goto :failed
echo.
echo === 7/7 Markdown import checks ===
node "tests\frontend\markdown-import.test.mjs"
if errorlevel 1 goto :failed
echo.
echo [OK] All checks passed.
set "CHECK_EXIT=0"
goto :finish

:select_python
"%~1" -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 set "PY=%~1"
exit /b 0

:failed
echo [ERROR] Checks failed. Review the output above.
set "CHECK_EXIT=1"

:finish
if not defined NO_PAUSE pause
exit /b %CHECK_EXIT%
