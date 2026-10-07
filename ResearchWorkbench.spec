# -*- mode: python ; coding: utf-8 -*-
# 打包：build.bat（或 python -m PyInstaller ResearchWorkbench.spec --noconfirm --clean）
# 关键三条（少一条打出来的包就是坏的）：
#   1) datas 必须带 web/（界面）和 build/icon.ico（窗口图标），否则界面对了图标也缺
#   2) contents_directory='.' 让 web/ 与 exe 同目录 —— server.py 用 HERE/web、workflow.py 用
#      __file__ 的 parent 找 providers_catalog.json，两种写法都指向 exe 所在目录才对得上
#   3) upx=False：UPX 压缩是杀软误报的主要来源

a = Analysis(
    ['server.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('web', 'web'),
        ('build/icon.ico', 'build'),
        ('providers_catalog.json', '.'),
    ],
    hiddenimports=['pymupdf', 'webview'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ResearchWorkbench',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='build/icon.ico',
    contents_directory='.',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ResearchWorkbench',
)
