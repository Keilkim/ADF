[CmdletBinding()]
param([switch]$SkipNativeHarness, [switch]$SkipBlockedUninstaller, [switch]$NativeComponentTestsOnly)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
$releaseRoot = Join-Path $repoRoot 'release'
$verificationRoot = Join-Path $repoRoot '.tools\verification'
$version = '0.3.27'
New-Item -ItemType Directory -Path $verificationRoot -Force | Out-Null
$setupPath = Join-Path $releaseRoot "ADF-Setup-$version.exe"
if (-not (Test-Path -LiteralPath $setupPath)) { throw 'Build the installer first.' }

. "$PSScriptRoot\installer-test-support.ps1"

function Run-CheckedProcess([string]$Executable, [string[]]$Arguments, [int]$TimeoutSeconds = 60) {
    $process = Start-Process -FilePath $Executable -ArgumentList $Arguments -PassThru -WindowStyle Hidden
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        $process.Kill()
        throw "Process timed out: $Executable"
    }
    $process.Refresh()
    if ($process.ExitCode -ne 0) { throw "Process failed: $Executable (exit $($process.ExitCode))" }
}

$token = [guid]::NewGuid().ToString('N')
$privateRoot = "HKCU:\Software\ADFInstallerSmoke\$token"
$registryPrefix = "$privateRoot\Software"
$shortcutDirectory = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\ADF installer test $token"
$testTempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$testRoot = [IO.Path]::GetFullPath((Join-Path $testTempBase "adf-installer-smoke-$token"))
if (-not $testRoot.StartsWith($testTempBase, [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid test path.' }
if ((Test-Path -LiteralPath $privateRoot) -or (Test-Path -LiteralPath $shortcutDirectory)) { throw 'Unexpected isolated test collision.' }
New-Item -ItemType Directory -Path $testRoot | Out-Null
$installRoot = Join-Path $testRoot 'installed'
$smokeJson = Join-Path $testRoot 'smoke.json'
$before = Get-ExistingStateSnapshot
$report = [ordered]@{
    installer = (Split-Path -Leaf $setupPath); isolated_registry = $true;
    test_token = $token;
    install = $false; launch = $false; existing_installation_preserved = $false;
    association_preserved = $false; native_registration = $false; legacy_split_removed = $false;
    uninstall = $false
}
try {
    # The same installer must reject an unattended install without acknowledgement.
    $deniedArgs = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', "/ADFISOLATEDTEST=$token", ('/DIR="{0}"' -f $installRoot))
    $denied = Start-Process -FilePath $setupPath -ArgumentList $deniedArgs -WindowStyle Hidden -PassThru
    if (-not $denied.WaitForExit(60000)) { $denied.Kill(); throw 'Unacknowledged install did not stop.' }
    $denied.Refresh()
    if ($denied.ExitCode -eq 0 -or (Test-Path -LiteralPath $installRoot) -or (Test-Path -LiteralPath $privateRoot)) { throw 'Installation proceeded without required acknowledgement.' }
    $report.unacknowledged_install_rejected = $true
    # Seed 0.1's static verb only inside our new private registry subtree.
    New-Item -Path "$registryPrefix\Classes\SystemFileAssociations\.pdf\shell\ADF.Split" -Force | Out-Null
    Run-CheckedProcess $setupPath @("/ADFACKNOTICE=$version", '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', "/ADFISOLATEDTEST=$token", ('/DIR="{0}"' -f $installRoot), ('/LOG="{0}"' -f (Join-Path $verificationRoot "installer-$version-install.log"))) -TimeoutSeconds 240
    $installedExe = Join-Path $installRoot 'ADF.exe'
    $installedDll = Join-Path $installRoot "ADFShell-$version.dll"
    if (-not (Test-Path -LiteralPath $installedExe) -or -not (Test-Path -LiteralPath $installedDll)) { throw 'Installed executable or native shell DLL missing.' }
    $builtRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot 'dist\ADF'))
    $payloadCount = 0
    foreach ($builtFile in (Get-ChildItem -LiteralPath $builtRoot -File -Recurse)) {
        $relative = $builtFile.FullName.Substring($builtRoot.Length + 1)
        $installedFile = Join-Path $installRoot $relative
        if (-not (Test-Path -LiteralPath $installedFile) -or (Get-FileHash -LiteralPath $builtFile.FullName -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $installedFile -Algorithm SHA256).Hash) {
            throw "Installed payload differs from the verified build: $relative"
        }
        $payloadCount++
    }
    $report.installed_payload_matches_build = $true
    $report.installed_payload_files = $payloadCount
    if (-not (Test-Path -LiteralPath (Join-Path $shortcutDirectory 'ADF.lnk'))) { throw 'Start Menu shortcut missing.' }
    if (-not (Test-Path -LiteralPath (Join-Path $installRoot '_internal\LICENSES\AGPL-3.0.txt'))) { throw 'Installed license missing.' }
    $helpTargets = [ordered]@{
        '사용 안내' = '_internal\docs\사용안내.html'
        '오픈소스 라이선스' = '_internal\LICENSES\index.html'
        '소스코드' = '_internal\SOURCES'
    }
    # WScript.Shell returns empty targets for Korean shortcut names on CI.
    # Read the installed links through the same Unicode shell API as Explorer.
    $shortcutShell = New-Object -ComObject Shell.Application
    $shortcutFolder = $shortcutShell.NameSpace($shortcutDirectory)
    if ($null -eq $shortcutFolder) { throw 'Cannot read installed shortcut folder.' }
    $helpSections = @{ '사용 안내' = 'guide'; '오픈소스 라이선스' = 'licenses'; '소스코드' = 'sources' }
    foreach ($label in $helpTargets.Keys) {
        $target = Join-Path $installRoot $helpTargets[$label]
        $shortcut = Join-Path $shortcutDirectory "$label.lnk"
        if (-not (Test-Path -LiteralPath $target) -or -not (Test-Path -LiteralPath $shortcut)) { throw "Installed help or shortcut missing: $label" }
        $item = $shortcutFolder.ParseName((Split-Path -Leaf $shortcut))
        if ($null -eq $item) { throw "Cannot read installed shortcut: $label" }
        $link = $item.GetLink
        # Windows shortcuts can return an 8.3 path even when Inno was given a long path.
        # Compare the file identity so an alias is accepted, but another EXE is not.
        $actualTarget = $link.Path
        $actualArguments = $link.Arguments
        $expectedArguments = '--help-section ' + $helpSections[$label]
        if (-not $report.Contains('help_shortcut_targets')) { $report.help_shortcut_targets = @() }
        $report.help_shortcut_targets += [ordered]@{ label = $label; target = $actualTarget; arguments = $actualArguments; expected_target = $installedExe; expected_arguments = $expectedArguments }
        & $pythonPath -c 'import os, sys; sys.exit(0 if os.path.samefile(sys.argv[1], sys.argv[2]) else 1)' $actualTarget $installedExe
        if ($LASTEXITCODE -ne 0 -or $actualArguments -ne $expectedArguments) {
            throw "Incorrect in-app help shortcut: $label; target=$actualTarget; arguments=$actualArguments"
        }
    }
    foreach ($name in @("ADF-Source-$version.zip", "ADF-ThirdParty-Sources-$version.zip", 'SHA256SUMS.txt')) {
        if (-not (Test-Path -LiteralPath (Join-Path $installRoot "_internal\SOURCES\$name"))) { throw "Installed corresponding source missing: $name" }
    }
    $report.installed_guide = $true
    $report.installed_license_reader = $true
    $report.installed_sources = $true
    $appResources = Join-Path $installRoot '_internal'
    $modelManifest = Get-Content -LiteralPath (Join-Path $appResources 'LICENSES\build-manifest.json') -Encoding UTF8 -Raw | ConvertFrom-Json
    foreach ($model in $modelManifest.ocr_models) {
        $modelFile = Join-Path $appResources ('OCR_MODELS\' + $model.name)
        if (-not (Test-Path -LiteralPath $modelFile)) { throw "Installed OCR model missing: $($model.name)" }
        if ((Get-FileHash -LiteralPath $modelFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $model.sha256) { throw "Installed OCR model checksum differs: $($model.name)" }
    }
    if ($modelManifest.ocr_models.Count -ne 5) { throw 'Expected five offline OCR models.' }
    $report.installed_ocr_models = $modelManifest.ocr_models.Count
    $report.help_shortcuts = $true
    $clsid = '{8093F936-820B-4CDB-A64B-7A39EC807A11}'
    $inproc = Get-Item -LiteralPath "$registryPrefix\Classes\CLSID\$clsid\InprocServer32"
    if ($inproc.GetValue('') -ne $installedDll -or $inproc.GetValue('ThreadingModel') -ne 'Apartment') { throw 'Native COM server registration is incorrect.' }
    $handler = Get-Item -LiteralPath "$registryPrefix\Classes\SystemFileAssociations\.pdf\shellex\ContextMenuHandlers\ADF"
    if ($handler.GetValue('') -ne $clsid) { throw 'Native PDF handler registration is incorrect.' }
    if (Test-Path -LiteralPath "$registryPrefix\Classes\SystemFileAssociations\.pdf\shell\ADF.Split") { throw 'Legacy static split verb remains.' }
    $report.native_registration = $true
    $report.legacy_split_removed = $true
    if ($before -ne (Get-ExistingStateSnapshot)) { throw 'Isolated installation changed the existing ADF installation or PDF association.' }
    $report.install = $true
    Run-CheckedProcess $installedExe @('--smoke-test', ('"{0}"' -f $smokeJson)) -TimeoutSeconds 180
    if (-not (Test-Path -LiteralPath $smokeJson)) { throw 'Installed app smoke result missing.' }
    $smoke = Get-Content -LiteralPath $smokeJson -Encoding UTF8 -Raw | ConvertFrom-Json
    if (-not $smoke.ok) { throw "Installed app smoke test failed: $($smoke | ConvertTo-Json -Compress)" }
    $report.launch = $true
    $report.app_smoke = $smoke
    Copy-Item -LiteralPath $smokeJson -Destination (Join-Path $releaseRoot "installed-app-smoke-$version.json")
    $smokeImage = [IO.Path]::ChangeExtension($smokeJson, '.png')
    if (Test-Path -LiteralPath $smokeImage) { Copy-Item -LiteralPath $smokeImage -Destination (Join-Path $releaseRoot "installed-app-smoke-$version.png") }
    & $pythonPath (Join-Path $PSScriptRoot 'test-frozen-worker.py') $installedExe --report (Join-Path $releaseRoot "installed-worker-smoke-$version.json")
    if ($LASTEXITCODE -ne 0) { throw 'Installed app worker export test failed.' }
    $report.worker_exports = $true
    & $pythonPath (Join-Path $PSScriptRoot 'test-frozen-tools.py') $installedExe --report (Join-Path $releaseRoot "installed-tools-smoke-$version.json")
    if ($LASTEXITCODE -ne 0) { throw 'Installed independent tool window test failed.' }
    $report.independent_tools = $true
    $harness = Join-Path $repoRoot 'build\native-shell\shell-tests.exe'
    $sink = Join-Path $repoRoot 'build\native-shell\request-sink.exe'
    if ($SkipNativeHarness) {
        $report.native_selection_harness = $false
        $report.native_selection_harness_skipped = 'Explicitly skipped: native test executable is blocked by Windows Application Control.'
    } else {
        $harnessArguments = @(('"{0}"' -f $installedDll), ('"{0}"' -f $sink))
        if ($NativeComponentTestsOnly) { $harnessArguments += '--component-only' }
        Run-CheckedProcess $harness $harnessArguments
        $report.native_component_harness = $true
        $report.native_selection_harness = -not $NativeComponentTestsOnly
        if ($NativeComponentTestsOnly) {
            $report.native_selection_harness_skipped = 'Component tests only; Windows-assembled Explorer menus are checked separately.'
        }
    }
} catch {
    $report.failure = $_.Exception.Message
    throw
} finally {
    $uninstaller = Join-Path $installRoot 'unins000.exe'
    $uninstallerRan = $false
    if ($SkipBlockedUninstaller) {
        $report.uninstall_skipped = 'Explicitly skipped after an observed Windows Application Control block; the blocked uninstaller was not executed.'
        Remove-ADFIsolatedTest -Token $token -TestRoot $testRoot -ShortcutDirectory $shortcutDirectory
        $report.test_files_removed_manually = $true
    } elseif (Test-Path -LiteralPath $uninstaller) {
        try {
            Run-CheckedProcess $uninstaller @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', ('/LOG="{0}"' -f (Join-Path $verificationRoot "installer-$version-uninstall.log")))
            $uninstallerRan = $true
        } catch {
            # Keep installation and app results even if policy blocks the uninstaller.
            $report.uninstall_failure = $_.Exception.Message
        }
    } elseif (-not $report.install -and -not (Test-Path -LiteralPath (Join-Path $installRoot 'ADF.exe'))) {
        # The setup loader can be blocked before it creates an uninstaller.
        # Remove only the legacy-menu fixture seeded under this run's GUID.
        if ($token -match '^[a-f0-9]{32}$' -and $privateRoot -eq "HKCU:\Software\ADFInstallerSmoke\$token" -and (Test-Path -LiteralPath $privateRoot)) {
            Remove-Item -LiteralPath $privateRoot -Recurse -Force
        }
    }
    $report.existing_installation_preserved = ($before -eq (Get-ExistingStateSnapshot))
    $report.association_preserved = $report.existing_installation_preserved
    $report.cleanup_complete = (-not (Test-Path -LiteralPath $privateRoot) -and @(Get-IsolatedUninstallKeys $installRoot).Count -eq 0 -and -not (Test-Path -LiteralPath (Join-Path $installRoot 'ADF.exe')) -and -not (Test-Path -LiteralPath $shortcutDirectory))
    $report.uninstall = ($uninstallerRan -and $report.cleanup_complete)
    $report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $releaseRoot "installer-smoke-$version.json") -Encoding UTF8
    $resolvedTestRoot = [IO.Path]::GetFullPath($testRoot)
    if ($report.cleanup_complete -and (Test-Path -LiteralPath $resolvedTestRoot) -and $resolvedTestRoot.StartsWith($testTempBase, [StringComparison]::OrdinalIgnoreCase) -and (Split-Path -Leaf $resolvedTestRoot) -match '^adf-installer-smoke-[a-f0-9]{32}$') {
        Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
    }
}
if (-not $report.existing_installation_preserved -or -not $report.cleanup_complete -or (-not $report.uninstall -and -not $SkipBlockedUninstaller)) { throw "Installer cleanup or preservation failed. See release/installer-smoke-$version.json." }
$report | ConvertTo-Json -Depth 10
