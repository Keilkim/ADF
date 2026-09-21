"""The source inputs shared by bundling and release verification."""
import hashlib
import json
import re
import shutil
import sys
import zipfile

SOURCE_ITEMS = ['main.py', 'adf', 'assets', 'docs', 'tests', 'scripts', 'installer',
                'native-shell', 'ADF.spec', 'README.md', 'LICENSE', 'LICENSES', 'spec.md',
                '.github', '.gitattributes', 'CODE_SIGNING.md', 'CONTRIBUTING.md', 'SECURITY.md',
                'requirements.txt', 'requirements-ocr-lock.txt', 'requirements-build.txt', 'requirements-dev.txt', '.gitignore']


def ocr_packages(root):
    return [line.split('==')[0] for line in (root/'requirements-ocr-lock.txt').read_text(encoding='utf-8').splitlines()
            if line and not line.startswith('#')]


def notice_dir(root):
    """LICENSES records the Windows wheels. Other platforms bundle notices for their own binaries."""
    return root/'LICENSES' if sys.platform == 'win32' else root/'build/licenses'


def platform_suffix():
    """Release files of other platforms carry a suffix; their third-party sources differ."""
    return {'win32': '', 'darwin': '-macOS'}.get(sys.platform, '-' + sys.platform)


def version(root):
    return re.search(r"__version__ = '([^']+)'", (root/'adf/__init__.py').read_text(encoding='utf-8')).group(1)


def third_party_manifest(manifest):
    """The build manifest bundled with the third-party sources, without ADF's own version.

    Unchanged third-party sources then give a byte-identical archive, which an
    update patch reuses from the installation instead of shipping it again.
    """
    neutral = {key: value for key, value in manifest.items() if key != 'version'}
    if isinstance(neutral.get('native_shell'), dict):
        neutral['native_shell'] = {key: value for key, value in neutral['native_shell'].items() if key != 'version'}
    return (json.dumps(neutral, indent=2, ensure_ascii=False)+'\n').encode('utf-8')


def archive_entry(name):
    """A stored ZIP entry with a fixed time, so identical content gives identical bytes."""
    entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    entry.compress_type = zipfile.ZIP_STORED
    entry.external_attr = 0o644 << 16
    return entry


def write_third_party_archive(target, manifest, sources):
    """Bundle the verified upstream sources. The same sources give the same bytes in
    every ADF version, so an update patch copies the installed archive instead of
    shipping 0.7 GB again."""
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(archive_entry('build-manifest.json'), third_party_manifest(manifest))
        for entry in manifest['source_archives']:
            path = sources/entry['filename']
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != entry['sha256']:
                    raise RuntimeError('Upstream source checksum mismatch: '+entry['filename'])
            with path.open('rb') as stream, archive.open(archive_entry(path.name), 'w') as output:
                shutil.copyfileobj(stream, output, 1 << 20)


def source_files(root):
    files = {}
    for name in SOURCE_ITEMS:
        path = root/name
        for file in path.rglob('*') if path.is_dir() else [path]:
            if file.is_file() and '__pycache__' not in file.parts and file.suffix not in ('.pyc', '.log'):
                files[file.relative_to(root).as_posix()] = file
    return files
