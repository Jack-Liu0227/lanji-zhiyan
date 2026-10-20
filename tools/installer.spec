# -*- mode: python ; coding: utf-8 -*-


import os
_ROOT = os.path.dirname(os.path.abspath(SPECPATH))     # SPECPATH = tools/，仓库根在上一层

a = Analysis(
    [os.path.join(_ROOT, 'tools', 'installer.py')],
    pathex=[],
    binaries=[],
    datas=[(os.path.join(_ROOT, 'dist', '览籍智研'), '览籍智研')],
    hiddenimports=['win32com.client'],
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
    a.binaries,
    a.datas,
    [],
    name='览籍智研-安装程序',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[os.path.join(_ROOT, 'tools', 'build', 'icon.ico')],
)
