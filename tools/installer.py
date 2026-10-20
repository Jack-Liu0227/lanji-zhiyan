# -*- coding: utf-8 -*-
"""览籍智研安装程序（单文件 exe）

用户双击即装，装完是这一套布局（程序、数据、用户、执行器分开）：
    %LOCALAPPDATA%\\览籍智研\\
        app\\          程序（览籍智研.exe 与只读资源）
        data\\         运行数据（库 / 文献 / 产物 / 备份），重装不覆盖
        user\\         用户资料与加密凭据
        executors\\    agents / skills / tools（模型和用户都能写）
        layout.json    布局版本

重新打包（改完程序代码后，在仓库根目录跑）：
    "%LOCALAPPDATA%\\Programs\\Python\\Python310\\python.exe" -m PyInstaller tools\\installer.spec --noconfirm --clean
生成 dist\\览籍智研-安装程序.exe，改名成「览籍智研-<版本>-安装程序.exe」发人即可。
"""
import os
import shutil
import subprocess
import sys
import time

APP = "览籍智研"
TITLE = APP
LEGACY_APPS = ("ResearchWorkbench",)


def say(msg=""):
    print(msg, flush=True)


def _copy_missing(src, dst):
    """复制缺失项；目标已有文件不覆盖，保证升级不碰用户数据。"""
    if os.path.isdir(src):
        os.makedirs(dst, exist_ok=True)
        for name in os.listdir(src):
            _copy_missing(os.path.join(src, name), os.path.join(dst, name))
    elif os.path.isfile(src) and not os.path.exists(dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)


def _migrate_legacy_data(root):
    local = os.environ["LOCALAPPDATA"]
    old_roots = [os.path.join(local, name) for name in LEGACY_APPS]
    old_roots += [os.path.join(local, "Programs", name) for name in LEGACY_APPS]
    copied = []
    for old_root in old_roots:
        for sub in ("data", "user", "executors"):
            src = os.path.join(old_root, sub)
            if not os.path.isdir(src):
                continue
            try:
                _copy_missing(src, os.path.join(root, sub))
                copied.append(sub)
            except OSError:
                pass
    return sorted(set(copied))


def main():
    # 用户机器的控制台默认 GBK，不设一下中文会花
    if os.name == "nt":
        os.system("chcp 65001 >nul")
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    here = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    src = os.path.join(here, APP)
    if not os.path.isfile(os.path.join(src, APP + ".exe")):
        say("包里没有 %s.exe，安装包损坏。" % APP)
        return 1

    root = os.path.join(os.environ["LOCALAPPDATA"], APP)          # 数据根
    app_dir = os.path.join(root, "app")                          # 程序根
    exe = os.path.join(app_dir, APP + ".exe")

    say("=" * 46)
    say("  %s 安装程序" % TITLE)
    say("=" * 46)
    say("程序装到：%s" % app_dir)
    say()

    # 关掉正在运行的新旧版本，否则文件被占用
    for image in (APP, *LEGACY_APPS):
        subprocess.run(["taskkill", "/F", "/IM", image + ".exe"], capture_output=True)
    time.sleep(0.5)

    # 建树：data / user / executors 与程序分开，升级只换 app
    for sub in ("data", "user", os.path.join("executors", "agents"),
                os.path.join("executors", "skills"), os.path.join("executors", "tools")):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    layout = os.path.join(root, "layout.json")
    if not os.path.isfile(layout):
        with open(layout, "w", encoding="utf-8") as fh:
            fh.write('{\n  "layout": 2\n}\n')

    migrated = _migrate_legacy_data(root)
    if migrated:
        say("  已补齐旧版本数据：%s" % "、".join(migrated))

    # 复制：跳过 data/（用户资料）与被占用的失败项
    os.makedirs(app_dir, exist_ok=True)
    n = skipped = 0
    for dirpath, dirnames, filenames in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        if rel != "." and rel.split(os.sep)[0] == "data":
            dirnames[:] = []
            continue
        target_dir = app_dir if rel == "." else os.path.join(app_dir, rel)
        os.makedirs(target_dir, exist_ok=True)
        for name in filenames:
            try:
                shutil.copy2(os.path.join(dirpath, name), os.path.join(target_dir, name))
                n += 1
            except Exception:
                skipped += 1
            if n % 25 == 0:
                print("\r  正在复制 ... %d 个文件" % n, end="", flush=True)
    say("\r  已复制 %d 个文件%s" % (n, "，%d 个跳过" % skipped if skipped else "  "))

    # 桌面快捷方式：优先 pywin32；没有就退回 PowerShell（两者任一都能成）
    lnk = os.path.join(os.path.expanduser("~"), "Desktop", TITLE + ".lnk")
    icon = os.path.join(app_dir, "build", "icon.ico")
    try:
        import win32com.client  # noqa: F401  pywin32
        sh = win32com.client.Dispatch("WScript.Shell")
        sc = sh.CreateShortcut(lnk)
        sc.TargetPath = exe
        sc.WorkingDirectory = app_dir
        if os.path.isfile(icon):
            sc.IconLocation = icon
        sc.Description = TITLE
        sc.Save()
    except Exception:
        ps = (
            "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
            "$s.TargetPath='%s';$s.WorkingDirectory='%s';$s.Description='%s';$s.Save()"
            % (lnk, exe, app_dir, TITLE)
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    say("  桌面快捷方式：%s" % ("已创建" if os.path.isfile(lnk) else "创建失败（可手动从安装目录启动）"))
    say()

    # 启动
    try:
        subprocess.Popen([exe], cwd=app_dir)
        say("装好了！桌面上的「%s」双击就能用。" % TITLE)
    except Exception as e:
        say("启动失败：%s（可从 %s 手动双击）" % (e, app_dir))
    say()
    say("提醒：第一次打开要去「模型」页填自己的 API key。")
    say()
    input("按回车关闭本窗口 ...")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # 双击出错时别闪退，让人看见原因
        print("\n安装出错：%r" % exc)
        input("按回车关闭 ...")
        sys.exit(1)
