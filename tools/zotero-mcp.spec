# -*- mode: python ; coding: utf-8 -*-
import os

_ROOT = os.path.dirname(os.path.abspath(SPECPATH))
_PKGS = [os.path.join(_ROOT, 'packages', name) for name in
         ('database', 'workspace-core', 'connectors')]

a = Analysis(
    [os.path.join(_ROOT, 'packages', 'connectors', 'zotero_mcp.py')],
    pathex=_PKGS,
    binaries=[],
    datas=[],
    hiddenimports=['paths', 'securestore', 'service_settings', 'zotero', 'zotero_sync'],
    hookspath=[os.path.join(_ROOT, 'tools', 'hooks')],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['workflow', 'project_library', 'project_sources', 'zotero_sources', 'zotero_links',
              'zotero_staged', 'transformers', 'tensorflow', 'datasets', 'google', 'torch',
              'torchvision', 'torchaudio', 'onnxruntime', 'rapidocr_onnxruntime', 'cv2',
              'h5py', 'pandas', 'matplotlib', 'scipy', 'sklearn'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='zotero-mcp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory='.',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='zotero-mcp',
)
