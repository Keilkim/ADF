[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$setup = @(Get-ChildItem (Join-Path $repoRoot '.tools/artifact') -Recurse -Filter 'ADF-Setup-*.exe')
if ($setup.Count -ne 1) { throw 'Expected one verified installer.' }
$version = [regex]::Match($setup[0].Name, '^ADF-Setup-(\d+\.\d+\.\d+)\.exe$').Groups[1].Value
if (-not $version) { throw 'Invalid installer version.' }
$token = [guid]::NewGuid().ToString('N')
$folder = Join-Path ([IO.Path]::GetTempPath()) "adf-installer-inspect-$token"
$installRoot = Join-Path $folder 'installed'
$shortcuts = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\ADF installer test $token"
$reportRoot = Join-Path $repoRoot '.tools/ci'
$report = [ordered]@{ version = $version; shortcuts = @(); smoke = $false; workers = $false; tools = $false; uninstall = $false }
function Run-InspectionProcess([string]$Executable, [string[]]$Arguments) {
    $process = Start-Process -FilePath $Executable -ArgumentList $Arguments -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(240000)) { $process.Kill(); throw "Timed out: $Executable" }
    $process.Refresh()
    if ($process.ExitCode -ne 0) { throw "Process failed: $Executable ($($process.ExitCode))" }
}
New-Item -ItemType Directory $folder | Out-Null
try {
    Run-InspectionProcess $setup[0].FullName @("/ADFACKNOTICE=$version", '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', "/ADFISOLATEDTEST=$token", ('/DIR="{0}"' -f $installRoot))
    $wsh = New-Object -ComObject WScript.Shell
    $shell = New-Object -ComObject Shell.Application
    $namespace = $shell.NameSpace($shortcuts)
    New-Item -ItemType Directory (Join-Path $reportRoot 'shortcuts') -Force | Out-Null
    foreach ($file in Get-ChildItem -LiteralPath $shortcuts -Filter '*.lnk') {
        Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $reportRoot 'shortcuts')
        $link = $wsh.CreateShortcut($file.FullName)
        $item = $namespace.ParseName($file.Name)
        $native = $item.GetLink
        $report.shortcuts += [ordered]@{ name = $file.Name; bytes = $file.Length; wsh_target = $link.TargetPath; wsh_arguments = $link.Arguments; shell_target = $native.Path; shell_arguments = $native.Arguments }
    }
    $exe = Join-Path $installRoot 'ADF.exe'
    Run-InspectionProcess $exe @('--smoke-test', ('"{0}"' -f (Join-Path $reportRoot 'app-smoke.json')))
    $smoke = Get-Content (Join-Path $reportRoot 'app-smoke.json') -Raw | ConvertFrom-Json
    if (-not $smoke.ok) { throw 'Packaged app smoke failed.' }
    $report.smoke = $true
    python (Join-Path $PSScriptRoot 'test-frozen-worker.py') $exe --report (Join-Path $reportRoot 'worker-smoke.json')
    if ($LASTEXITCODE -ne 0) { throw 'Packaged workers failed.' }
    $report.workers = $true
    python (Join-Path $PSScriptRoot 'test-frozen-tools.py') $exe --report (Join-Path $reportRoot 'tools-smoke.json')
    if ($LASTEXITCODE -ne 0) { throw 'Packaged tools failed.' }
    $report.tools = $true
} catch {
    $report.failure = $_.Exception.Message
    throw
} finally {
    $uninstaller = Join-Path $installRoot 'unins000.exe'
    if (Test-Path -LiteralPath $uninstaller) {
        Run-InspectionProcess $uninstaller @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
        $report.uninstall = $true
    }
    $report | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $reportRoot 'inspection.json') -Encoding utf8
}
