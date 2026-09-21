param(
    [string]$Compiler = '',
    [switch]$SkipTests,
    [switch]$ComponentTestsOnly
)
$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $workspace
$output = Join-Path $workspace 'build\native-shell'
New-Item -ItemType Directory -Path $output -Force | Out-Null

# This compiler is local to the checkout; employees never need a C++ runtime installer.
if (-not $Compiler) {
    New-Item -ItemType Directory -Path (Join-Path $workspace '.tools') -Force | Out-Null
    $toolchainName = 'llvm-mingw-20260826-ucrt-x86_64'
    $toolchain = Join-Path $workspace ".tools\$toolchainName"
    $Compiler = Join-Path $toolchain 'bin\x86_64-w64-mingw32-clang++.exe'
    if (-not (Test-Path -LiteralPath $Compiler)) {
        $archive = Join-Path $workspace ".tools\$toolchainName.zip"
        $digest = 'ae601f4e0f72bbdf441ad2df8bb16f037e2e9251559ea6b37b4057aef39c06c3'
        if (-not (Test-Path -LiteralPath $archive)) {
            Write-Host 'Downloading the pinned portable native build toolchain (191 MB)...'
            $download = "https://github.com/mstorsjo/llvm-mingw/releases/download/20260826/$toolchainName.zip"
            & curl.exe --fail --location --silent --show-error --output $archive $download
            if ($LASTEXITCODE -ne 0) { throw 'Native build toolchain download failed.' }
        }
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $digest) {
            throw "Toolchain checksum mismatch: $archive"
        }
        Write-Host 'Extracting the local native build toolchain...'
        Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $workspace '.tools') -Force
    }
}
if (-not (Test-Path -LiteralPath $Compiler)) { throw "C++ compiler not found: $Compiler" }
$source = Join-Path $workspace 'native-shell'
$windres = Join-Path (Split-Path -Parent $Compiler) 'x86_64-w64-mingw32-windres.exe'
& $windres '-i' (Join-Path $source 'adf_shell.rc') '-o' (Join-Path $output 'adf_shell.res') '-O' 'coff'
if ($LASTEXITCODE -ne 0) { throw 'Native shell version resource compilation failed.' }
# The toolchain has no Windows.Data.Pdf header; widl generates it from the IDL.
$widl = Join-Path (Split-Path -Parent $Compiler) 'x86_64-w64-mingw32-widl.exe'
& $widl '-I' (Join-Path (Split-Path -Parent (Split-Path -Parent $Compiler)) 'include') '-h' '-o' (Join-Path $output 'windows.data.pdf.h') (Join-Path $source 'windows.data.pdf.idl')
if ($LASTEXITCODE -ne 0) { throw 'Windows.Data.Pdf header generation failed.' }
$common = @('-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', '-DUNICODE', '-D_UNICODE', '-D_WIN32_WINNT=0x0A00', '-static', '-static-libgcc', '-static-libstdc++', '-I', $output)
$libraries = @('-lole32', '-lshell32', '-ladvapi32', '-luser32', '-lgdi32', '-luuid')
& $Compiler @common '-shared' (Join-Path $source 'adf_shell.cpp') (Join-Path $source 'thumbnail.cpp') (Join-Path $source 'adf_shell.def') (Join-Path $output 'adf_shell.res') '-o' (Join-Path $output 'ADFShell.dll') @libraries '-lruntimeobject' '-lshcore' '-lshlwapi' '-lwindowscodecs'
if ($LASTEXITCODE -ne 0) { throw 'Native shell DLL compilation failed.' }
# The harness also compiles thumbnail.cpp, so it links the same libraries as the DLL.
& $Compiler @common '-municode' (Join-Path $source 'shell_tests.cpp') '-o' (Join-Path $output 'shell-tests.exe') @libraries '-lruntimeobject' '-lshcore' '-lshlwapi' '-lwindowscodecs'
if ($LASTEXITCODE -ne 0) { throw 'Native shell harness compilation failed.' }
& $Compiler @common '-municode' '-mwindows' (Join-Path $source 'request_sink.cpp') '-o' (Join-Path $output 'request-sink.exe') @libraries
if ($LASTEXITCODE -ne 0) { throw 'Native request fixture compilation failed.' }
if (-not $SkipTests) {
    $harnessArgs = '"{0}" "{1}"' -f (Join-Path $output 'ADFShell.dll'), (Join-Path $output 'request-sink.exe')
    if ($ComponentTestsOnly) { $harnessArgs += ' --component-only' }
    $harnessLog = Join-Path $output 'shell-tests.log'
    $harnessError = Join-Path $output 'shell-tests-error.log'
    $harness = Start-Process -FilePath (Join-Path $output 'shell-tests.exe') -ArgumentList $harnessArgs -Wait -PassThru -WindowStyle Hidden -RedirectStandardOutput $harnessLog -RedirectStandardError $harnessError
    Get-Content -LiteralPath $harnessLog
    if ($harness.ExitCode -ne 0) {
        Get-Content -LiteralPath $harnessError
        throw 'Native shell integration tests failed.'
    }
}
Write-Host "Native Explorer extension ready: $output\ADFShell.dll"
