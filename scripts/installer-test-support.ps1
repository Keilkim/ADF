# Helpers for isolated installer verification.
function Get-RegistryTree([string]$RelativePath) {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($RelativePath)
    if ($null -eq $key) { return $null }
    try {
        $result = [ordered]@{ values = [ordered]@{}; children = [ordered]@{} }
        foreach ($name in ($key.GetValueNames() | Sort-Object)) {
            $result.values[$name] = @($key.GetValueKind($name).ToString(), $key.GetValue($name))
        }
        foreach ($name in ($key.GetSubKeyNames() | Sort-Object)) {
            $result.children[$name] = Get-RegistryTree ($RelativePath + '\' + $name)
        }
        return $result
    } finally { $key.Dispose() }
}

function Get-ExistingStateSnapshot {
    $state = [ordered]@{ registry = [ordered]@{}; files = [ordered]@{} }
    foreach ($relative in @(
        'Software\Classes\.pdf',
        'Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\UserChoice',
        'Software\Classes\ADF.Document', 'Software\Classes\Applications\ADF.exe',
        'Software\ADF\Capabilities', 'Software\Classes\SystemFileAssociations\.pdf',
        'Software\Classes\CLSID\{8093F936-820B-4CDB-A64B-7A39EC807A11}',
        'Software\Classes\CLSID\{A96AE73F-5DB5-4CF1-80EF-9A44D2B3D84D}',
        'Software\Microsoft\Windows\CurrentVersion\Uninstall\{941DF95F-945A-4A82-BB29-ED83E65BC1B1}_is1'
    )) { $state.registry[$relative] = Get-RegistryTree $relative }
    $registered = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\RegisteredApplications')
    if ($registered) {
        $state.registry['RegisteredApplications.ADF'] = $registered.GetValue('ADF')
        $registered.Dispose()
    }
    # Preserve the user's installed app and its shortcuts byte for byte.
    $registration = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{941DF95F-945A-4A82-BB29-ED83E65BC1B1}_is1' -ErrorAction SilentlyContinue
    $existingRoot = if ($registration -and $registration.InstallLocation) { $registration.InstallLocation } else { Join-Path $env:LOCALAPPDATA 'Programs\ADF' }
    $roots = @($existingRoot, (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\ADF'))
    foreach ($root in $roots) {
        if (Test-Path -LiteralPath $root) {
            foreach ($file in (Get-ChildItem -LiteralPath $root -File -Recurse | Sort-Object FullName)) {
                $state.files[$file.FullName] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
            }
        }
    }
    $desktop = Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'ADF.lnk'
    if (Test-Path -LiteralPath $desktop) { $state.files[$desktop] = (Get-FileHash -LiteralPath $desktop -Algorithm SHA256).Hash }
    return ($state | ConvertTo-Json -Depth 30 -Compress)
}

function Test-UninstallLogMentions([string]$Path, [string]$Text) {
    # unins000.dat holds each recorded uninstall action's expanded paths as
    # UTF-16 text, at even or odd byte offsets.
    $bytes = [IO.File]::ReadAllBytes($Path)
    foreach ($offset in 0, 1) {
        $decoded = [Text.Encoding]::Unicode.GetString($bytes, $offset, $bytes.Length - $offset)
        if ($decoded.IndexOf($Text, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
    }
    return $false
}

function Get-IsolatedUninstallKeys([string]$InstallDirectory) {
    # Inno Setup may shorten a long AppId with a hash in the uninstall key.
    # Identify this test by its full install directory, never by the prefix alone.
    $expectedDirectory = [IO.Path]::GetFullPath($InstallDirectory).TrimEnd('\')
    Get-ChildItem -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall' |
        Where-Object { $_.PSChildName -like 'ADF.IsolatedInstallerTest.*_is1' } |
        Where-Object {
            $location = $_.GetValue('InstallLocation')
            $location -and [IO.Path]::GetFullPath($location).TrimEnd('\') -eq $expectedDirectory
        }
}

function Remove-ADFIsolatedTest([string]$Token, [string]$TestRoot, [string]$ShortcutDirectory) {
    if ($Token -notmatch '^[a-f0-9]{32}$') { throw 'Invalid isolated test token.' }
    $expectedRoot = [IO.Path]::GetFullPath((Join-Path ([IO.Path]::GetTempPath()) "adf-installer-smoke-$Token"))
    $expectedShortcut = [IO.Path]::GetFullPath((Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\ADF installer test $Token"))
    if ([IO.Path]::GetFullPath($TestRoot) -ne $expectedRoot -or [IO.Path]::GetFullPath($ShortcutDirectory) -ne $expectedShortcut) { throw 'Cleanup target is not this isolated test.' }
    # Validate all paths before deleting anything. Never follow a junction.
    foreach ($candidate in @($expectedRoot, $expectedShortcut)) {
        if (-not (Test-Path -LiteralPath $candidate)) { continue }
        if ((Resolve-Path -LiteralPath $candidate).Path -ne $candidate) { throw "Unexpected resolved target: $candidate" }
        $pending = [Collections.Generic.Stack[string]]::new()
        $pending.Push($candidate)
        while ($pending.Count) {
            $entry = Get-Item -LiteralPath $pending.Pop() -Force
            if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Unexpected link: $($entry.FullName)" }
            if ($entry.PSIsContainer) {
                Get-ChildItem -LiteralPath $entry.FullName -Force | ForEach-Object { $pending.Push($_.FullName) }
            }
        }
    }
    $installDirectory = Join-Path $expectedRoot 'installed'
    $keys = @(Get-IsolatedUninstallKeys $installDirectory)
    foreach ($key in $keys) {
        if (-not $key.Name.StartsWith('HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Uninstall\ADF.IsolatedInstallerTest.', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected uninstall key.' }
    }
    foreach ($key in $keys) { Remove-Item -LiteralPath $key.PSPath -Recurse -Force }
    $privateKey = "HKCU:\Software\ADFInstallerSmoke\$Token"
    if (Test-Path -LiteralPath $privateKey) { Remove-Item -LiteralPath $privateKey -Recurse -Force }
    foreach ($candidate in @($expectedShortcut, $expectedRoot)) {
        if (Test-Path -LiteralPath $candidate) { Remove-Item -LiteralPath $candidate -Recurse -Force }
    }
}
