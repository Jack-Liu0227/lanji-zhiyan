# 本地打包验收记录

验收日期：2026-10-24。环境为 Windows 11 x64、Python 3.13.5、PyInstaller 6.22.3、pywebview 6.2.1。

`tools\build-app.bat --no-pause` 完整执行成功，产物位于 `dist/release/`。最终安装包固定命名为 `览籍智研.exe`，免安装包为 `览籍智研-免安装版.zip`，下面保留初次验收时的文件名和校验记录：

| 文件 | 字节数 | SHA256 |
| --- | ---: | --- |
| LanjiZhiyan-1.1.2-Setup.exe | 99028589 | 1912594ffbe227a97960fab08e59550eebc986f747f431fb3bb54551bcdccf6e |
| LanjiZhiyan-1.1.2-Portable.zip | 88251591 | 7ca005a3614d9c4003993e52bf5f1e3ba8f371ba3074eee81fd46782542503ff |

自动验收通过：

- 把程序复制到源码树之外，从不含 Python 的 PATH 启动。
- 全新安装到含中文和空格的目录，逐一校验 1470 个程序文件。
- 应用全部 10 个数据库迁移，验证 assets、task_links、team_tasks 和 SQLite 完整性。
- 检查首页、JavaScript、PDF.js 和模型供应商目录的 HTTP 响应。
- 程序运行期间覆盖安装，检查原项目 ID、用户数据保留，以及过期程序文件清除。

在本机实际安装至 `%LOCALAPPDATA%\LanjiZhiyan\app`，桌面快捷方式已指向该目录。用户数据根仍是 `%LOCALAPPDATA%\览籍智研`。

安装版和 ZIP 解压后的免安装版分别以独立测试数据启动，免安装版位于中文目录。通过 CDP 连接真实 WebView2 窗口，确认原生 `pywebview.api` 存在、侧栏正常初始化、12 个页面入口均可切换，没有 JavaScript 异常或 HTTP 5xx。点击窗口关闭按钮后，窗口及其服务子进程均退出。

完整 Python 测试为 518 项，1 项按条件跳过，其余通过。前端主题与悬空 ID 检查通过。

本地详细证据位于 `build/packaging-final.log`、`build/packaging-tests-final.log`、`build/installed-gui-report.json`、`build/portable-gui-report.json`，窗口截图为 `build/installed-gui.png` 和 `build/portable-gui.png`。
