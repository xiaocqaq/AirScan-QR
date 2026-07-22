@echo off
chcp 65001 >nul
REM AirScan-QR 打包脚本: 生成免环境单文件 exe (dist\AirScan-QR.exe)
setlocal

echo [1/2] 安装依赖...
python -m pip install segno pyzbar pillow numpy pywebview pywin32 pyinstaller
if errorlevel 1 goto :err

echo [2/2] 打包 exe...
REM 用 python -m PyInstaller 调用, 避免 pyinstaller.exe 不在 PATH 时找不到命令。
REM 关键选项:
REM   --add-data "airscan/ui.html;."  把界面 HTML 打进 exe 根 (与 app._resource_path 一致),
REM                                    否则运行时找不到 ui.html 会崩溃。
REM   --collect-binaries pyzbar        打包 ZBar 原生库 (libzbar-64.dll/libiconv.dll)。
REM   --collect-all webview            打包 pywebview 的 JS bridge 等数据文件。
REM   --hidden-import win32gui/win32ui  窗口捕获用到的 pywin32 子模块。
python -m PyInstaller --onefile --windowed --noconfirm --name AirScan-QR ^
  --icon airscan/icon.ico ^
  --add-data "airscan/ui.html;." ^
  --add-data "airscan/icon.ico;." ^
  --collect-binaries pyzbar ^
  --collect-all webview ^
  --hidden-import win32gui --hidden-import win32ui --hidden-import win32con ^
  main.py
if errorlevel 1 goto :err

echo.
echo ✅ 完成: dist\AirScan-QR.exe (双击即用, 无需 Python 环境)
goto :eof

:err
echo.
echo ❌ 构建失败, 请查看上方错误输出。
exit /b 1
