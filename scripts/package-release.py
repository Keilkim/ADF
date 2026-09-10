"""Package source code and matching third-party archives beside the installer."""
from pathlib import Path
import hashlib
import shutil
import sys
import zipfile
from release_common import source_files, version

ROOT = Path(__file__).resolve().parents[1]
release = ROOT / "release"
release.mkdir(exist_ok=True)
VERSION = version(ROOT)
installer = release / f'ADF-Setup-{VERSION}.exe'
shutil.copyfile(ROOT / 'docs' / '사용안내.html', release / f'사용안내-{VERSION}.html')
source_zip = release / f"ADF-Source-{VERSION}.zip"
third_party_zip = release / f"ADF-ThirdParty-Sources-{VERSION}.zip"
bundled = ROOT/'dist/ADF/_internal/SOURCES' if sys.platform == 'win32' else ROOT/'build/source-bundle'
with zipfile.ZipFile(bundled/source_zip.name) as archive:
    files = source_files(ROOT)
    assert set(archive.namelist()) == {'ADF/'+name for name in files}, 'Rebuild the source bundle before packaging'
    for name, path in files.items():
        assert archive.read('ADF/'+name) == path.read_bytes(), 'Source changed since build: '+name
for path in (source_zip, third_party_zip):
    shutil.copyfile(bundled/path.name, path)
checksums = []
for file in sorted(release.iterdir()):
    if file.is_file() and VERSION in file.name and file.suffix.lower() in (".exe", ".zip", ".dmg", ".html", ".pdf"):
        with file.open("rb") as stream:
            checksums.append(f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {file.name}")
(release / f"SHA256SUMS-{VERSION}.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
print("Release source archives and SHA256SUMS.txt ready")
