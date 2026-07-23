@echo off
setlocal

REM AirScan-QR one-file Windows build.
REM VCR120_DLL is required by pyzbar's bundled libzbar-64.dll.
if not defined VCR120_DLL set "VCR120_DLL=%SystemRoot%\System32\msvcr120.dll"
if not exist "%VCR120_DLL%" goto :vcrerr

echo [1/2] Installing build dependencies...
python -m pip install segno pyzbar pillow numpy pywebview pywin32 pyinstaller
if errorlevel 1 goto :err

echo [2/2] Building dist\AirScan-QR.exe...
python -m PyInstaller --noconfirm AirScan-QR.spec
if errorlevel 1 goto :err

echo.
echo Build complete: dist\AirScan-QR.exe
goto :eof

:vcrerr
echo.
echo Build failed: MSVCR120.dll was not found.
echo Set VCR120_DLL to the x64 DLL path and run build.bat again.
exit /b 1

:err
echo.
echo Build failed. Review the output above.
exit /b 1
