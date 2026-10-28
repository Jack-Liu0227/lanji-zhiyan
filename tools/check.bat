@echo off
chcp 65001 >nul
cd /d "%~dp0.."
rem 览籍智研 一条命令自检：Python 自检（含「主脚本必须过 node --check」）+ 前端主题配色 + 悬空 id 体检
set PY=%LOCALAPPDATA%\Programs\Python\Python310\python.exe
if not exist "%PY%" set PY=python
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
echo === 1/3 Python 自检（tests/）===
"%PY%" -m unittest discover -s tests -t .
if errorlevel 1 (echo. & echo [X] Python 自检没过 & pause & exit /b 1)
echo.
echo === 2/3 前端主题配色自检 ===
node "apps\desktop\web\theme.test.mjs"
if errorlevel 1 (echo. & echo [X] 主题自检没过 & pause & exit /b 1)
echo.
echo === 3/3 悬空 id 体检（顶层 $("x") 撞上不存在的 id 会让整页空壳）===
node "apps\desktop\web\ids.test.mjs"
if errorlevel 1 (echo. & echo [X] 有悬空 id 引用 & pause & exit /b 1)
echo.
echo [OK] 全绿。
pause
