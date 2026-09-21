"""Package source code and matching third-party archives beside the installer."""
from pathlib import Path
import hashlib
import shutil
import sys
import zipfile
from release_common import platform_suffix, source_files, version

ROOT = Path(__file__).resolve().parents[1]
release = ROOT / "release"
release.mkdir(exist_ok=True)
VERSION = version(ROOT)
SUFFIX = platform_suffix()
installer = release / f'ADF-Setup-{VERSION}.exe'
shutil.copyfile(ROOT / 'docs' / '사용안내.html', release / f'사용안내-{VERSION}.html')
# Bundled names stay the same on every platform; release names carry the platform.
archives = {f"{prefix}-{VERSION}.zip": release / f"{prefix}-{VERSION}{SUFFIX}.zip"
            for prefix in ("ADF-Source", "ADF-ThirdParty-Sources")}
bundled = ROOT/'dist/ADF/_internal/SOURCES' if sys.platform == 'win32' else ROOT/'build/source-bundle'
with zipfile.ZipFile(bundled/f"ADF-Source-{VERSION}.zip") as archive:
    files = source_files(ROOT)
    assert set(archive.namelist()) == {'ADF/'+name for name in files}, 'Rebuild the source bundle before packaging'
    for name, path in files.items():
        assert archive.read('ADF/'+name) == path.read_bytes(), 'Source changed since build: '+name
for name, target in archives.items():
    shutil.copyfile(bundled/name, target)
checksums = []
for file in sorted(release.iterdir()):
    if (file.is_file() and VERSION in file.name and file.suffix.lower() in (".exe", ".zip", ".dmg", ".html", ".pdf")
            and (SUFFIX in file.name or file.suffix.lower() == ".html")) or file.name == f"ADF-Files-{VERSION}-Windows.json":
        with file.open("rb") as stream:
            checksums.append(f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {file.name}")
(release / f"SHA256SUMS-{VERSION}{SUFFIX}.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
print("Release source archives and SHA256SUMS.txt ready")
