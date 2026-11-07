# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import copy_metadata

datas = [('C:/Tools/VYRXONE-Sentinel-V5/config', 'config'), ('C:/Tools/VYRXONE-Sentinel-V5/assets', 'assets')]
hiddenimports = ['keyring.backends.Windows', 'win32ctypes.pywin32.win32cred', 'win32ctypes.pywin32.pywintypes']
datas += copy_metadata('keyring')
datas += copy_metadata('pywin32-ctypes')
hiddenimports += collect_submodules('keyring.backends')


a = Analysis(
    ['C:/Tools/VYRXONE-Sentinel-V5/launch_sentinel.pyw'],
    pathex=['C:/Tools/VYRXONE-Sentinel-V5/src'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'pytest'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VYRXONE-Sentinel',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['C:/Tools/VYRXONE-Sentinel-V5/assets/sentinel.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='VYRXONE-Sentinel',
)
