# -*- mode: python ; coding: utf-8 -*-
# 打包：build.bat（或 python -m PyInstaller ResearchWorkbench.spec --noconfirm --clean）
# 关键三条（少一条打出来的包就是坏的）：
#   1) datas 必须带界面目录（apps/desktop/web）、Provider 目录（packages/contracts）和窗口图标，缺一个就坏
#   2) contents_directory='.'：界面目录与 exe 同目录 —— 服务与工作流都按「自己所在目录往上找」定位资源
#   3) pathex 必须带 packages/ 各包：入口在 apps/desktop/，静态分析要从那儿顺着 import 找过去
#   3) upx=False：UPX 压缩是杀软误报的主要来源

import os

_ROOT = os.path.dirname(os.path.abspath(SPECPATH))          # SPECPATH = tools/，仓库根在上一层
_PKGS = [os.path.join(_ROOT, 'packages', n) for n in
         ('database', 'workspace-core', 'workspace-service', 'connectors', 'ai-runtime')]

a = Analysis(
    [os.path.join(_ROOT, 'apps', 'desktop', 'server.py')],
    pathex=_PKGS,
    binaries=[],
    datas=[
        (os.path.join(_ROOT, 'apps', 'desktop', 'web'), 'web'),
        (os.path.join(_ROOT, 'tools', 'build', 'icon.ico'), 'build'),
        (os.path.join(_ROOT, 'packages', 'contracts', 'providers_catalog.json'), 'packages/contracts'),
    ],
    hiddenimports=['pdfplumber', 'pypdf', 'cryptography', 'PIL', 'pypdfium2', 'webview',
                   'research_core', 'workflow', 'team', 'schedule', 'papertran', 'zotero', 'vault',
                   'mail', 'rss', 'integrations', 'guards', 'pdfio'],
    # 说明：pdfminer.six / pdfplumber 里有条件导入（cryptography 处理加密 PDF、PIL/pypdfium2 渲染），
    # 静态分析看不到，必须显式列出；PyMuPDF(AGPL) 已弃用，见 pdfio.py。
   # cryptography: pdfminer.six 里是条件导入，静态分析看不到   # PyMuPDF(AGPL) 已弃用，见 pdfio.py / requirements.txt
    hookspath=[os.path.join(_ROOT, 'tools', 'hooks')],       # hooks/hook-workflow.py 用来顶掉同名 contrib 钩子（见该文件注释）
    hooksconfig={},
    runtime_hooks=[],
    # 应用一行都没用到下面这些；它们是被 3.10 里别的包连带拖进来的（torch 还是 CUDA 版）。
    # 不排除的话包 3.1GB —— 没法发给别人下载。将来真要加 OCR/机器学习功能，把对应项去掉即可。
    excludes=[
        'torch', 'torchvision', 'torchaudio',
        'onnxruntime', 'rapidocr_onnxruntime', 'cv2', 'shapely', 'pyclipper',
        'numba', 'llvmlite', 'h5py', 'pandas', 'matplotlib', 'scipy', 'sklearn',
        'google', 'pyreadline3', 'win32com',
        # 注意：cryptography（pdfminer.six 加密 PDF）和 yaml（server.py 读配置）不能排，
        # excludes 优先级高于 hiddenimports，排了就是 Startup 崩溃（已踩）。
    ],
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
    icon=os.path.join(_ROOT, 'tools', 'build', 'icon.ico'),
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
