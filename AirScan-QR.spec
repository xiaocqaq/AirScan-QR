# -*- mode: python ; coding: utf-8 -*-
import os
from build_metadata import executable_name
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_dynamic_libs

datas = [('airscan/ui.html', '.'), ('airscan/ui.css', '.'), ('airscan/ui.js', '.'), ('airscan/icon.ico', '.')]
vcr120 = os.environ.get('VCR120_DLL')
binaries = [(vcr120, '.')] if vcr120 and os.path.isfile(vcr120) else []
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
    excludes=[
        'numpy', 'tkinter', 'PySide6', 'PyQt5', 'PyQt6', 'matplotlib', 'scipy', 'pandas',
        'PIL.AvifImagePlugin', 'PIL.WebPImagePlugin', 'PIL.ImageTk',
        'cryptography', 'bcrypt',
    ],
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
    name=executable_name('AirScan-QR', 'version_info.txt'),
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
    version='version_info.txt',
)
