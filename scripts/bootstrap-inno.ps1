[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$toolRoot = Join-Path $repoRoot '.tools'
$innoRoot = Join-Path $toolRoot 'innosetup-6.7.3'
$compilerPath = Join-Path $innoRoot 'ISCC.exe'
if (Test-Path -LiteralPath $compilerPath) {
    Write-Output $compilerPath
    exit 0
}
New-Item -ItemType Directory -Path $toolRoot -Force | Out-Null
$downloadPath = Join-Path $toolRoot 'innosetup-6.7.3.exe'
$downloadUri = 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe'
$expectedHash = '9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732'
if (-not (Test-Path -LiteralPath $downloadPath)) {
    Invoke-WebRequest -Uri $downloadUri -OutFile $downloadPath -UseBasicParsing
}
if ((Get-FileHash -LiteralPath $downloadPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
    throw 'The Inno Setup download does not match the SHA-256 published by its official GitHub release.'
}
$signature = Get-AuthenticodeSignature -LiteralPath $downloadPath
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Pyrsys B.V.') {
    throw 'The official Inno Setup installer signature could not be verified.'
}
# Inno Setup's documented portable mode writes no app association or uninstall entry.
$arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', '/PORTABLE=1', ('/DIR="{0}"' -f $innoRoot))
$process = Start-Process -FilePath $downloadPath -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $compilerPath)) {
    throw "Portable Inno Setup preparation failed: exit $($process.ExitCode)."
}
Write-Output $compilerPath
