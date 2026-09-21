"""The installer leaves other programs and user data alone; releases list every file they upload."""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
ISS = (ROOT/'installer/adf.iss').read_text(encoding='utf-8-sig')


def section(name):
    """The entries of one installer section, without comments."""
    body = re.search(rf'^\[{name}\]\n(.*?)(?=^\[|\Z)', ISS, re.M | re.S).group(1)
    return [line for line in body.splitlines() if line.strip() and not line.startswith(';')]


def routine(name):
    return re.search(rf'^(?:function|procedure) {name}\b.*?^end;', ISS, re.M | re.S).group(0)


def test_uninstall_deletes_only_the_downloaded_updates():
    # Setup records the entry when it installs, so an isolated test never records the real folder.
    assert section('UninstallDelete') == [
        r'Type: filesandordirs; Name: "{localappdata}\ADF\ADF\updates"; Check: not IsIsolatedTest']


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows installer')
def test_uninstall_deletes_the_folder_the_app_downloads_updates_to():
    app = (ROOT/'adf/app.py').read_text(encoding='utf-8')
    assert "writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))/'updates'" in app
    names = [re.search(rf"app\.set{kind}Name\('([^']+)'\)", app).group(1) for kind in ('Organization', 'Application')]
    script = ('import sys; from PySide6.QtCore import QCoreApplication as A, QStandardPaths as P; '
              'A.setOrganizationName(sys.argv[1]); A.setApplicationName(sys.argv[2]); '
              'print(P.writableLocation(P.StandardLocation.AppLocalDataLocation))')
    folder = subprocess.run([sys.executable, '-c', script, *names], capture_output=True, check=True, encoding='utf-8',
                            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}).stdout.strip()
    installer = re.search(r'Name: "\{localappdata\}\\([^"]+)"', ISS).group(1)
    assert Path(folder)/'updates' == Path(os.environ['LOCALAPPDATA'])/installer


def test_thumbnail_slot_is_left_to_other_programs():
    slot = next(line for line in section('Registry') if r'.pdf\shellex\{{E357FCCD' in line)
    # The uninstaller removes the slot in code, and only while it still names ADF.
    assert slot.endswith('Check: ThumbnailSlotAvailable') and 'uninsdelete' not in slot
    subkey = re.search(r'Subkey: "\{code:GetRegistryPrefix\}([^"]+)"', slot).group(1).replace('{{', '{')
    handler = re.search(r'ValueData: "([^"]+)"', slot).group(1).replace('{{', '{')
    assert f"ThumbnailSlot = '{subkey}';" in ISS and f"ThumbnailHandler = '{handler}';" in ISS
    assert "(Handler <> '') and (CompareText(Handler, ThumbnailHandler) <> 0)" in routine('OtherThumbnailHandler')
    # This user's entry would hide a handler registered for all users, except in the private test tree.
    available = routine('ThumbnailSlotAvailable')
    assert "not OtherThumbnailHandler(HKCU, GetRegistryPrefix(''), Handler)" in available
    assert re.search(r"if Result and not IsIsolatedTest then\s+Result := not OtherThumbnailHandler\(HKLM, '', Handler\)", available)
    # An isolated test's uninstaller has no token; the DLL check keeps it off the real slot.
    conditions, removal = routine('CurUninstallStepChanged').split(' then begin')
    assert 'CompareText(Handler, ThumbnailHandler) = 0' in conditions
    assert "CompareText(ExtractFilePath(Server), ExpandConstant('{app}\\')) = 0" in conditions
    assert 'RegDeleteValue(HKCU, ThumbnailSlot' in removal


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows PowerShell')
def test_release_checksums_list_every_uploaded_file(tmp_path):
    script = (ROOT/'scripts/prepare-windows-release.ps1').read_text(encoding='utf-8-sig')
    guard = script.index('throw "Release files missing from the checksums')
    assert guard < script.index('gh release upload')
    block = script[script.index('$build = '):script.index('\n', guard)]
    download, output = tmp_path/'download', tmp_path/'output'
    (download/'.tools/ci').mkdir(parents=True)
    (download/'.tools/ci/build-origin.json').write_text('{"commit": "abc"}', encoding='utf-8')
    output.mkdir()
    updates = ['ADF-Update-9.9.8-to-9.9.9.exe', 'ADF-Files-9.9.9-Windows.json']
    for name in ['ADF-Setup-9.9.9.exe', 'ADF-Source-9.9.9.zip', 'ADF-ThirdParty-Sources-9.9.9.zip',
                 'ADF-Guide-9.9.9-Windows.html', *updates]:
        (output/name).write_bytes(name.encode())
    runner = tmp_path/'checksums.ps1'
    runner.write_text(f"$ErrorActionPreference = 'Stop'\n$version = '9.9.9'\n$download = '{download}'\n$output = '{output}'\n"
                      f"$updates = @({', '.join(repr(name) for name in updates)})\n{block}\n", encoding='utf-8-sig')

    def run():
        return subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(runner)],
                              capture_output=True, text=True)

    assert run().returncode == 0
    sums = dict(reversed(line.split('  ', 1)) for line in (output/'SHA256SUMS-9.9.9.txt').read_text(encoding='utf-8-sig').splitlines())
    # The build's provenance record is uploaded, so it is listed too.
    assert 'ADF-Build-9.9.9-Windows.json' in sums
    assert sums == {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in output.iterdir()
                    if file.name != 'SHA256SUMS-9.9.9.txt'}
    # A leftover file would be uploaded without a checksum.
    (output/'ADF-Setup-9.9.8.exe').write_bytes(b'old')
    failed = run()
    assert failed.returncode != 0 and 'ADF-Setup-9.9.8.exe' in failed.stdout + failed.stderr
