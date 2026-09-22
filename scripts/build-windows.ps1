[CmdletBinding()]
param([switch]$SkipDependencies, [switch]$SkipInstaller, [switch]$NativeComponentTestsOnly)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 (64-bit) is required on the build computer.' }
}
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not $SkipDependencies) {
    & $pythonPath -m pip install -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
& $pythonPath scripts/make-icon.py
if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed.' }
& "$PSScriptRoot\build-shell.ps1" -ComponentTestsOnly:$NativeComponentTestsOnly
if ($LASTEXITCODE -ne 0) { throw 'Native Explorer extension build failed.' }
& $pythonPath scripts/prepare-ocr.py
if ($LASTEXITCODE -ne 0) { throw 'OCR model preparation failed.' }
& $pythonPath scripts/collect-licenses.py
if ($LASTEXITCODE -ne 0) { throw 'License collection failed.' }
& $pythonPath scripts/package-sources.py
if ($LASTEXITCODE -ne 0) { throw 'Corresponding source packaging failed.' }
& $pythonPath -m PyInstaller --noconfirm --clean ADF.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
# The in-process COM DLL resolves its own sibling ADF.exe. Do not put it in _internal.
Copy-Item -LiteralPath 'build\native-shell\ADFShell.dll' -Destination 'dist\ADF\ADFShell-0.3.30.dll' -Force
if (-not $SkipInstaller) {
    & "$PSScriptRoot\bootstrap-inno.ps1"
    $compilerPath = Join-Path $repoRoot '.tools\innosetup-6.7.3\ISCC.exe'
    & $compilerPath '/Qp' 'installer\adf.iss'
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }
    # Small installers from recent releases. GH_TOKEN avoids the API rate limit.
    & $pythonPath scripts/make-update-patches.py --iscc $compilerPath
    if ($LASTEXITCODE -ne 0) { throw 'Update patch build failed.' }
}
& $pythonPath scripts/package-release.py
if ($LASTEXITCODE -ne 0) { throw 'Release packaging failed.' }
Write-Host 'Build ready in release/ (installer) and dist/ADF/ (portable directory).'
