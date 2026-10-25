@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ATC Deck - telepites
echo.
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Hiba a csomagok telepitesekor. Telepitve van a Python?
  pause
  exit /b 1
)
echo.
echo Parancsikon letrehozasa...
python atc_deck.py --parancsikon
echo.
echo Kesz! Az asztalon es a Start menuben is ott van az "ATC Deck" ikon.
pause
