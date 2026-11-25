@echo off
chcp 65001 >nul
cd /d "%~dp0.."
rem 览籍智研 一条命令自检：Python 自检（含「主脚本必须过 node --check」）+ 前端主题配色 + 悬空 id + 工作流图 / 文件预览 / 分栏
set PY=%LOCALAPPDATA%\Programs\Python\Python310\python.exe
if not exist "%PY%" set PY=python
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
echo === 1/6 Python 自检（tests/）===
"%PY%" -m unittest discover -s tests -t .
if errorlevel 1 (echo. & echo [X] Python 自检没过 & pause & exit /b 1)
echo.
echo === 2/6 前端主题配色自检 ===
node "apps\desktop\web\theme.test.mjs"
if errorlevel 1 (echo. & echo [X] 主题自检没过 & pause & exit /b 1)
echo.
echo === 3/6 悬空 id 体检（顶层 $("x") 撞上不存在的 id 会让整页空壳）===
node "apps\desktop\web\ids.test.mjs"
if errorlevel 1 (echo. & echo [X] 有悬空 id 引用 & pause & exit /b 1)
echo.
echo === 4/6 工作流图自检 ===
node "apps\desktop\web\workflow-graph.test.mjs"
if errorlevel 1 (echo. & echo [X] 工作流图自检没过 & pause & exit /b 1)
echo.
echo === 5/6 工作流文件预览自检（只读、越界路径与大小上限）===
node "apps\desktop\web\workflow-file-preview.test.mjs"
if errorlevel 1 (echo. & echo [X] 文件预览自检没过 & pause & exit /b 1)
echo.
echo === 6/6 分栏拖拽与宽度记忆自检 ===
node "apps\desktop\web\split-resizer.test.mjs"
if errorlevel 1 (echo. & echo [X] 分栏自检没过 & pause & exit /b 1)
echo.
echo [OK] 全绿。
pause
