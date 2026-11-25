# recap @VERSION@: offline installer for Windows. Started by "Install recap.bat".
#   install.ps1 [folder]     default: %LOCALAPPDATA%\recap\@VERSION@
# Set RECAP_NO_ICON=1 to skip the desktop icon.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$target = if ($args.Count -gt 0) { $args[0] } else { Join-Path $env:LOCALAPPDATA 'recap\@VERSION@' }
Write-Host "Installing recap @VERSION@ into $target (a few minutes)..."
New-Item -ItemType Directory -Force -Path $target | Out-Null
& (Join-Path $here 'recap-env.ps1') -o $target -s cmd -q
if ($LASTEXITCODE) { throw "unpacking failed ($LASTEXITCODE)" }

# the pretrained StarDist model, so the first run needs no internet
$keras = Join-Path $env:USERPROFILE '.keras\models'
New-Item -ItemType Directory -Force -Path $keras | Out-Null
Copy-Item -Recurse -Force (Join-Path $here 'stardist-models\StarDist2D') $keras

$launcher = Join-Path $target 'recap.bat'
@'
@echo off
rem recap launcher: activates the installed environment and runs recap.
setlocal
if exist "%~dp0activate.bat" (
    call "%~dp0activate.bat"
) else (
    set "PATH=%~dp0env;%~dp0env\Library\mingw-w64\bin;%~dp0env\Library\usr\bin;%~dp0env\Library\bin;%~dp0env\Scripts;%PATH%"
)
set "RECAP_LAUNCHER=%~f0"
"%~dp0env\python.exe" -m recap %*
'@ | Set-Content -Encoding ASCII $launcher
& $launcher --version
if ($env:RECAP_NO_ICON -ne '1') { & $launcher desktop-icon }
Write-Host ""
Write-Host "Done. Open recap from its desktop icon, or run:  `"$launcher`" --help"
Write-Host "Fiji is not included: install it from https://fiji.sc, then run:  `"$launcher`" fiji set <Fiji folder>"
