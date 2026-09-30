@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Edit Studio: build EXE (PyInstaller) ===
python -m pip install --upgrade pyinstaller >nul
if errorlevel 1 ( echo Khong cai duoc pyinstaller & exit /b 1 )
pyinstaller --noconfirm EditStudio.spec
if errorlevel 1 ( echo Build that bai & exit /b 1 )
if not exist "dist\EditStudio\bin" mkdir "dist\EditStudio\bin"
copy /y "bin\ffmpeg.exe" "dist\EditStudio\bin\" >nul
copy /y "bin\ffprobe.exe" "dist\EditStudio\bin\" >nul
if not exist "dist\EditStudio\presets" mkdir "dist\EditStudio\presets"
xcopy /e /i /y "presets" "dist\EditStudio\presets" >nul
xcopy /e /i /y "app\assets" "dist\EditStudio\app\assets" >nul
python tools\bundle_cuda.py "dist\EditStudio"
echo.
echo Xong: dist\EditStudio\EditStudio.exe
