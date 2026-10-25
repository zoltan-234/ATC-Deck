# ATC Deck eltavolito - parancsikonok, beallitasok torlese, futo peldany leallitasa
$ErrorActionPreference = 'SilentlyContinue'

# 1. futo ATC Deck leallitasa
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like '*atc_deck.py*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Output 'Futo ATC Deck leallitva' }

# 2. parancsikonok (asztal + Start menu)
foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
  if (-not $dir) { continue }
  $f = Join-Path $dir 'ATC Deck.lnk'
  if (Test-Path $f) { Remove-Item $f -Force; Write-Output ('Parancsikon torolve: ' + $f) }
}

# 3. beallitasok
$ini = Join-Path (Split-Path $PSScriptRoot -Parent) 'beallitasok.ini'
if (Test-Path $ini) { Remove-Item $ini -Force; Write-Output 'Beallitasok torolve (beallitasok.ini)' }

# 4. korabbi tesztverziok registry-beallitasai, ha maradtak
foreach ($k in @('HKCU:\Software\ATCDeck', 'HKCU:\Software\ROBI\RadarDesktop')) {
  if (Test-Path $k) { Remove-Item $k -Recurse -Force; Write-Output ('Regi beallitas torolve: ' + $k) }
}
$robi = 'HKCU:\Software\ROBI'
if ((Test-Path $robi) -and -not (Get-ChildItem $robi) -and -not (Get-ItemProperty $robi).PSObject.Properties.Name.Count) {
  Remove-Item $robi -Force
}

# 5. Python gyorsitotar a mappaban
$cache = Join-Path (Split-Path $PSScriptRoot -Parent) '__pycache__'
if (Test-Path $cache) { Remove-Item $cache -Recurse -Force }
