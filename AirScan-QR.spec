# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path
from build_metadata import executable_name
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_dynamic_libs
from PyInstaller.utils.hooks import get_module_file_attribute

datas = [('airscan/ui.html', '.'), ('airscan/ui.css', '.'), ('airscan/ui.js', '.'), ('airscan/icon.ico', '.')]
vcr120 = os.environ.get('VCR120_DLL')
binaries = [(vcr120, '.')] if vcr120 and os.path.isfile(vcr120) else []
datas += collect_data_files('webview')
binaries += collect_dynamic_libs('pyzbar')

# win32ui.pyd must find these beside itself after one-file extraction. PyInstaller
# otherwise places pywintypes313.dll under pywin32_system32, which fails on clean PCs.
win32ui_path = Path(get_module_file_attribute('win32ui'))
site_packages = win32ui_path.parents[1]
pywin32_system32 = site_packages / 'pywin32_system32'
mfc140u_path = Path(os.environ.get(
    'MFC140U_DLL', Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'mfc140u.dll'))
native_root_files = (
    win32ui_path,
    pywin32_system32 / 'pywintypes313.dll',
    mfc140u_path,
    Path(sys.base_prefix) / 'VCRUNTIME140.dll',
    Path(sys.base_prefix) / 'VCRUNTIME140_1.dll',
)
binaries += [(path, '.') for path in native_root_files if path.is_file()]


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

# PyInstaller's pywin32 hook assigns subdirectories after user binaries are
# merged. Relocate the two linked modules so the Windows loader can resolve
# them without a machine-level Python/pywin32 installation.
root_native_names = {'win32ui.pyd', 'pywintypes313.dll'}
for index, (dest, source, kind) in enumerate(a.binaries):
    filename = Path(dest).name
    if filename.lower() in root_native_names:
        a.binaries[index] = (filename, source, kind)

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
