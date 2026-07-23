# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_dynamic_libs
from PyInstaller.utils.hooks import collect_all

datas = [('airscan/ui.html', '.'), ('airscan/ui.css', '.'), ('airscan/ui.js', '.'), ('airscan/icon.ico', '.')]
vcr120 = os.environ.get(
    'VCR120_DLL',
    os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'msvcr120.dll'),
)
if not os.path.isfile(vcr120):
    raise FileNotFoundError(
        'MSVCR120.dll is required by pyzbar. Set VCR120_DLL to its x64 path.'
    )

binaries = [(vcr120, '.')]
hiddenimports = ['win32gui', 'win32ui', 'win32con']
binaries += collect_dynamic_libs('pyzbar')
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
