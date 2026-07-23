# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_dynamic_libs

datas = [('airscan/ui.html', '.'), ('airscan/ui.css', '.'), ('airscan/ui.js', '.'), ('airscan/icon.ico', '.')]
binaries = []
datas += collect_data_files('webview')
binaries += collect_dynamic_libs('pyzbar')


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=['webview.platforms.edgechromium', 'win32gui', 'win32ui', 'win32con', 'pystray._win32'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'tkinter', 'PySide6', 'PyQt5', 'PyQt6', 'matplotlib', 'scipy', 'pandas'],
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
    name='AirScan-QR',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['airscan\\icon.ico'],
)
