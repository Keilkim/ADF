[CmdletBinding()]
param([string]$BaseSetup, [string]$Patch, [string]$Manifest, [string]$ReportPath)
# Install the earlier release in an isolated test namespace, update it with the
# patch the way the app does, and require exactly the new build's files, a
# restart with the open document, and a clean uninstall. Without arguments,
# every patch in release/ is tested against its published base installer.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$repoRoot = Split-Path -Parent $PSScriptRoot
. "$PSScriptRoot\installer-test-support.ps1"

if (-not $Patch) {
    $version = [regex]::Match((Get-Content -LiteralPath (Join-Path $repoRoot 'adf\__init__.py') -Raw), "__version__ = '([^']+)'").Groups[1].Value
    $releaseRoot = Join-Path $repoRoot 'release'
    $bases = Join-Path $repoRoot '.tools\update-bases'
    New-Item -ItemType Directory -Path $bases -Force | Out-Null
    $patches = @(Get-ChildItem -LiteralPath $releaseRoot -Filter "ADF-Update-*-to-$version.exe")
    if (-not $patches.Count) { 'No update patches were built; the full installer is the only update.'; exit 0 }
    foreach ($file in $patches) {
        $base = [regex]::Match($file.Name, '^ADF-Update-(\d+\.\d+\.\d+)-to-').Groups[1].Value
        $download = "https://github.com/Keilkim/ADF/releases/download/v$base"
        $setup = Join-Path $bases "ADF-Setup-$base.exe"
        $sums = Invoke-WebRequest -UseBasicParsing -Uri "$download/SHA256SUMS-$base.txt"
        $expected = ([Text.Encoding]::UTF8.GetString($sums.Content) -split "`r?`n" | Where-Object { $_ -match "^([a-f0-9]{64})  ADF-Setup-$([regex]::Escape($base))\.exe$" } | ForEach-Object { $Matches[1] })
        if (-not $expected) { throw "No published checksum for ADF-Setup-$base.exe" }
        if (-not (Test-Path -LiteralPath $setup) -or (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
            Invoke-WebRequest -UseBasicParsing -Uri "$download/ADF-Setup-$base.exe" -OutFile $setup
            if ((Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw "Downloaded ADF-Setup-$base.exe differs from its release checksum." }
        }
        & $PSCommandPath -BaseSetup $setup -Patch $file.FullName -Manifest (Join-Path $releaseRoot "ADF-Files-$version-Windows.json") `
            -ReportPath (Join-Path $releaseRoot "update-smoke-$base-to-$version.json")
        if (-not $?) { throw "Update patch test failed: $($file.Name)" }
    }
    exit 0
}

$expected = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
$version = $expected.version
$patchName = Split-Path -Leaf $Patch
if ($patchName -notmatch '^ADF-Update-(\d+\.\d+\.\d+)-to-(\d+\.\d+\.\d+)\.exe$' -or $Matches[2] -ne $version) { throw 'The patch and the file manifest belong to different versions.' }
$base = $Matches[1]
$token = [guid]::NewGuid().ToString('N')
$testTempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$testRoot = [IO.Path]::GetFullPath((Join-Path $testTempBase "adf-installer-smoke-$token"))
$installRoot = Join-Path $testRoot 'installed'
$shortcutDirectory = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\ADF installer test $token"
$privateRoot = "HKCU:\Software\ADFInstallerSmoke\$token"
$logRoot = Join-Path $repoRoot '.tools\verification'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
if ((Test-Path -LiteralPath $testRoot) -or (Test-Path -LiteralPath $privateRoot)) { throw 'Unexpected isolated test collision.' }
$before = Get-ExistingStateSnapshot
$report = [ordered]@{ patch = $patchName; base = $base; version = $version; test_token = $token; isolated_registry = $true }

function Invoke-Installer([string]$Executable, [string[]]$Arguments) {
    $process = Start-Process -FilePath $Executable -ArgumentList $Arguments -PassThru -WindowStyle Hidden
    if (-not $process.WaitForExit(600000)) { $process.Kill(); throw "Installer timed out: $Executable" }
    $process.Refresh()
    return $process.ExitCode
}

function Assert-InstalledFiles {
    $actual = @{}
    foreach ($file in Get-ChildItem -LiteralPath $installRoot -File -Recurse -Force) {
        $actual[$file.FullName.Substring($installRoot.Length + 1).Replace('\', '/')] = $file.FullName
    }
    foreach ($entry in $expected.files.PSObject.Properties) {
        if (-not $actual.ContainsKey($entry.Name)) { throw "Missing after the update: $($entry.Name)" }
        if ((Get-FileHash -LiteralPath $actual[$entry.Name] -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.Value.sha256) { throw "Differs after the update: $($entry.Name)" }
        $actual.Remove($entry.Name)
    }
    # Old versions' sources and Explorer DLL are removed; only the uninstaller is extra.
    $extra = @($actual.Keys | Where-Object { $_ -notmatch '^unins000\.(exe|dat)$' })
    if ($extra.Count) { throw "Left over after the update: $($extra -join ', ')" }
}

$common = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', "/ADFISOLATEDTEST=$token", ('/DIR="{0}"' -f $installRoot))
try {
    New-Item -ItemType Directory -Path $testRoot | Out-Null
    $document = Join-Path $testRoot '업데이트 뒤 다시 열 문서.pdf'
    [IO.File]::WriteAllText($document, "%PDF-1.4`n%%EOF`n")
    if ((Invoke-Installer $BaseSetup (@("/ADFACKNOTICE=$base") + $common + ('/LOG="{0}"' -f (Join-Path $logRoot "update-$base-base.log")))) -ne 0) { throw "ADF $base did not install." }
    $report.base_installed = $true
    # The arguments of adf/updates.py installer_arguments, quoted the way Qt quotes them.
    $executable = Join-Path $installRoot 'ADF.exe'
    $arguments = @("/ADFACKNOTICE=$version") + $common + @(('"/ADFRELAUNCH={0}"' -f $executable), ('"/ADFOPEN={0}"' -f $document),
        ('/LOG="{0}"' -f (Join-Path $logRoot "update-$base-to-$version.log")))
    if ((Invoke-Installer $Patch $arguments) -ne 0) { throw 'The update patch failed.' }
    Assert-InstalledFiles
    $report.files_match = $true
    $deadline = (Get-Date).AddSeconds(30)
    do {
        $restarted = @(Get-CimInstance Win32_Process -Filter "Name = 'ADF.exe'" | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.Contains($token) })
        if ($restarted.Count) { break }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    $report.restarted_with_document = [bool]@($restarted | Where-Object { $_.CommandLine.Contains('"' + $document + '"') }).Count
    $restarted | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    if (-not $report.restarted_with_document) { throw 'ADF did not restart with the open document after the update.' }
    Start-Sleep -Seconds 1
    # Built for exactly one version, the patch must refuse the version it produced.
    $again = Invoke-Installer $Patch (@("/ADFACKNOTICE=$version") + $common + ('/LOG="{0}"' -f (Join-Path $logRoot "update-$base-to-$version-again.log")))
    if ($again -eq 0) { throw 'The patch installed over a version it was not built for.' }
    Assert-InstalledFiles
    $report.other_version_rejected = $true
} catch {
    $report.failure = $_.Exception.Message
    throw
} finally {
    $uninstaller = Join-Path $installRoot 'unins000.exe'
    $report.uninstall = $false
    if (Test-Path -LiteralPath $uninstaller) {
        $report.uninstall = (Invoke-Installer $uninstaller @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')) -eq 0
    }
    $report.cleanup_complete = (-not (Test-Path -LiteralPath $privateRoot) -and @(Get-IsolatedUninstallKeys $installRoot).Count -eq 0 -and
        -not (Test-Path -LiteralPath (Join-Path $installRoot 'ADF.exe')) -and -not (Test-Path -LiteralPath $shortcutDirectory))
    if (-not $report.cleanup_complete) {
        Remove-ADFIsolatedTest -Token $token -TestRoot $testRoot -ShortcutDirectory $shortcutDirectory
    } elseif (Test-Path -LiteralPath $testRoot) {
        Remove-Item -LiteralPath $testRoot -Recurse -Force
    }
    $report.existing_installation_preserved = ($before -eq (Get-ExistingStateSnapshot))
    if ($ReportPath) { $report | ConvertTo-Json | Set-Content -LiteralPath $ReportPath -Encoding UTF8 }
}
if (-not $report.uninstall -or -not $report.cleanup_complete -or -not $report.existing_installation_preserved) { throw 'Update test cleanup failed or the existing installation changed.' }
$report | ConvertTo-Json
