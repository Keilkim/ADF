[CmdletBinding()]
param([Parameter(Mandatory)][string]$RunId, [Parameter(Mandatory)][string]$Tag)
$ErrorActionPreference = 'Stop'
if ($RunId -notmatch '^\d+$' -or $Tag -notmatch '^v\d+\.\d+\.\d+$') { throw 'Invalid build run or release tag.' }
$version = $Tag.Substring(1)
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot
$run = gh api "repos/$env:GITHUB_REPOSITORY/actions/runs/$RunId" | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $run.conclusion -ne 'success' -or $run.status -ne 'completed') { throw 'A successful completed build is required.' }
$release = gh release view $Tag --json isDraft,targetCommitish | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $release.isDraft -or $release.targetCommitish -ne $run.head_sha) { throw 'Draft release must target the original build commit.' }
$artifactName = "ADF-Windows-installer-unsigned-$($run.head_sha)-$RunId"
$download = Join-Path $repoRoot '.tools/release-input'
gh run download $RunId --name $artifactName --dir $download
if ($LASTEXITCODE -ne 0) { throw 'Build artifact download failed.' }
$inputRoot = Join-Path $download 'release'
$origin = Get-Content (Join-Path $download '.tools/ci/build-origin.json') -Raw | ConvertFrom-Json
if ($origin.commit -ne $run.head_sha -or $origin.repository -ne $env:GITHUB_REPOSITORY) { throw 'Build origin mismatch.' }
$checksums = @{}
foreach ($line in Get-Content (Join-Path $inputRoot "SHA256SUMS-$version.txt")) {
    $parts = $line -split '  ', 2
    if ($parts.Count -ne 2 -or $parts[0] -notmatch '^[a-f0-9]{64}$') { throw 'Malformed build checksums.' }
    $checksums[$parts[1]] = $parts[0]
}
function Check-File([string]$File, [string]$Name) {
    if (-not $checksums.ContainsKey($Name) -or (Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash.ToLowerInvariant() -ne $checksums[$Name]) { throw "Release file differs from the verified build: $Name" }
}
$setup = Join-Path $inputRoot "ADF-Setup-$version.exe"
Check-File $setup "ADF-Setup-$version.exe"
$output = Join-Path $repoRoot '.tools/release-output'
New-Item -ItemType Directory $output -Force | Out-Null
$token = [guid]::NewGuid().ToString('N')
$installRoot = Join-Path ([IO.Path]::GetTempPath()) "adf-release-extract-$token"
if (Test-Path -LiteralPath $installRoot) { throw 'Unexpected installation collision.' }
function Run-Installer([string]$Executable, [string[]]$Arguments) {
    $process = Start-Process -FilePath $Executable -ArgumentList $Arguments -PassThru -WindowStyle Hidden
    if (-not $process.WaitForExit(240000)) { $process.Kill(); throw 'Installer timed out.' }
    $process.Refresh()
    if ($process.ExitCode -ne 0) { throw "Installer failed: $($process.ExitCode)" }
}
try {
    Run-Installer $setup @("/ADFACKNOTICE=$version", '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', "/ADFISOLATEDTEST=$token", ('/DIR="{0}"' -f $installRoot))
    foreach ($prefix in @('ADF-Source', 'ADF-ThirdParty-Sources')) {
        $name = "$prefix-$version.zip"
        $file = Join-Path $installRoot "_internal/SOURCES/$name"
        Check-File $file $name
        Copy-Item -LiteralPath $file -Destination (Join-Path $output $name)
    }
    $guide = Join-Path $installRoot '_internal/docs/사용안내.html'
    Check-File $guide "사용안내-$version.html"
    Copy-Item -LiteralPath $guide -Destination (Join-Path $output "ADF-Guide-$version-Windows.html")
} finally {
    $uninstaller = Join-Path $installRoot 'unins000.exe'
    if (Test-Path -LiteralPath $uninstaller) { Run-Installer $uninstaller @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') }
}
Copy-Item -LiteralPath $setup -Destination $output
Copy-Item -LiteralPath (Join-Path $download '.tools/ci/build-origin.json') -Destination (Join-Path $output "ADF-Build-$version-Windows.json")
$sums = foreach ($name in @("ADF-Setup-$version.exe", "ADF-Source-$version.zip", "ADF-ThirdParty-Sources-$version.zip", "ADF-Guide-$version-Windows.html")) {
    $file = Join-Path $output $name
    (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() + '  ' + $name
}
$sums | Set-Content -LiteralPath (Join-Path $output "SHA256SUMS-$version.txt") -Encoding utf8
foreach ($file in Get-ChildItem -LiteralPath $output -File) {
    $current = gh release view $Tag --json isDraft,assets | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $current.isDraft) { throw 'Release must remain a draft during upload.' }
    $existing = @($current.assets | Where-Object name -eq $file.Name)
    if ($existing.Count -gt 0) {
        $digest = 'sha256:' + (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($existing.Count -ne 1 -or $existing[0].digest -ne $digest) { throw "Conflicting release asset: $($file.Name)" }
    } else {
        gh release upload $Tag $file.FullName
        if ($LASTEXITCODE -ne 0) { throw "Asset upload failed: $($file.Name)" }
    }
}
'Verified Windows installer and corresponding bundled sources uploaded to draft release. No release was published.' | Add-Content $env:GITHUB_STEP_SUMMARY
