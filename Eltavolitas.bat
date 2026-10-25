@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ATC Deck - eltavolitas
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\eltavolitas.ps1"
echo.
echo A Python csomagokat (PySide6, miniaudio) mas program is hasznalhatja.
choice /C IN /M "Eltavolitsam oket is? I = igen, N = nem"
if errorlevel 2 goto vege
python -m pip uninstall -y miniaudio PySide6 PySide6_Essentials PySide6_Addons shiboken6
:vege
echo.
echo Kesz. Az ATC-Deck mappat most mar kezzel torolheted.
pause
