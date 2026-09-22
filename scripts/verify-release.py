"""Audit working source, source ZIP, frozen code, resources and release hashes.

Only the requested JSON report is written. No packaged code is executed.
"""
import argparse
import hashlib
import json
import marshal
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import types
import unicodedata
import zipfile

from PyInstaller.archive.readers import CArchiveReader
from release_common import notice_dir, platform_suffix, source_files, third_party_manifest


ROOT = Path(__file__).resolve().parents[1]
# ADF never uses video: neither the Windows FFmpeg plugin nor the codecs linked by
# the macOS opencv-python wheels, which scripts/build-opencv-macos.py replaces.
VIDEO_CODECS = ('opencv_videoio_ffmpeg*', 'libavcodec*', 'libavformat*', 'libx264*', 'libx265*')


def normalize(code):
    return code.replace(co_filename='<audit>', co_consts=tuple(
        normalize(value) if isinstance(value, types.CodeType) else value for value in code.co_consts))


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def application(version):
    """Bundle root, PyInstaller's sys._MEIPASS folder, executable and installer name."""
    if sys.platform == 'darwin':
        # Data lives in Contents/Resources and is linked into Contents/Frameworks.
        app = ROOT/'dist/ADF.app'
        return app, app/'Contents/Frameworks', app/'Contents/MacOS/ADF', f'ADF-{version}-macOS.dmg'
    return ROOT/'dist/ADF', ROOT/'dist/ADF/_internal', ROOT/'dist/ADF/ADF.exe', f'ADF-Setup-{version}.exe'


def required_macos(app):
    """The newest macOS that any bundled arm64 binary is built for (LC_BUILD_VERSION minos)."""
    newest = (0,)
    for path in app.rglob('*'):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open('rb') as stream:
            if stream.read(4) not in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe'):
                continue
        commands = subprocess.run(['otool', '-arch', 'arm64', '-l', str(path)], capture_output=True, text=True).stdout
        for value in re.findall(r'minos (\d+(?:\.\d+)*)', commands):
            newest = max(newest, tuple(map(int, value.split('.'))))
    return newest


def verify():
    version = re.search(r"__version__ = '([^']+)'", (ROOT/'adf/__init__.py').read_text(encoding='utf-8')).group(1)
    release = ROOT/'release'
    suffix = platform_suffix()
    files = source_files(ROOT)
    with zipfile.ZipFile(release/f'ADF-Source-{version}{suffix}.zip') as archive:
        assert archive.testzip() is None, 'Source archive CRC failure'
        assert set(archive.namelist()) == {'ADF/'+name for name in files}, 'Source archive file list mismatch'
        for name, path in files.items():
            assert path.read_bytes() == archive.read('ADF/'+name), f'Unpackaged source change: {name}'
    bundle, internal, executable, installer = application(version)
    for prefix in ('ADF-Source', 'ADF-ThirdParty-Sources'):
        filename = f'{prefix}-{version}.zip'
        assert digest(internal/'SOURCES'/filename) == digest(release/f'{prefix}-{version}{suffix}.zip'), f'Installed sources differ: {filename}'
    for line in (internal/'SOURCES/SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
        expected, name = line.split('  ', 1)
        assert digest(internal/'SOURCES'/name) == expected, f'Bundled source checksum mismatch: {name}'
    assert (internal/'LICENSES/index.html').is_file(), 'Readable license entry point missing'
    container = CArchiveReader(str(executable))
    pyz = container.open_embedded_archive(next(name for name, entry in container.toc.items() if entry[-1] == 'z'))
    count = 0
    for name, path in files.items():
        if not name.startswith('adf/') or path.suffix != '.py':
            continue
        module = name[:-3].replace('/', '.').removesuffix('.__init__')
        assert module in pyz.toc, f'Missing frozen module: {module}'
        assert normalize(pyz.extract(module)) == normalize(compile(path.read_bytes(), '<audit>', 'exec', optimize=0)), f'Stale frozen module: {module}'
        count += 1
    assert normalize(marshal.loads(container.extract('main'))) == normalize(compile((ROOT/'main.py').read_bytes(), '<audit>', 'exec', optimize=0))
    notices = notice_dir(ROOT)
    resources = {name: path for name, path in files.items() if name.split('/')[0] in ('assets', 'docs') or name == 'README.md'}
    resources.update({'LICENSES/'+path.relative_to(notices).as_posix(): path for path in notices.rglob('*')
                      if path.is_file() and '__pycache__' not in path.parts and path.suffix not in ('.pyc', '.log')})
    data_count = 0
    for name, path in resources.items():
        target = internal/name
        assert target.is_file() and digest(target) == digest(path), f'Stale frozen resource: {name}'
        data_count += 1
    manifest = json.loads((notices/'build-manifest.json').read_text(encoding='utf-8'))
    assert manifest['version'] == version
    models = {'OCR_MODELS': manifest.get('ocr_models', []), 'SEARCH_MODEL': manifest.get('search_models', [])}
    for folder, entries in models.items():
        for model in entries:
            assert digest(internal/folder/model['name']) == model['sha256'], 'Model mismatch: '+model['name']
    assert {path.name for path in bundle.rglob('*.onnx')} == {model['name'] for entries in models.values() for model in entries
                                                              if model['name'].endswith('.onnx')}, 'Unexpected bundled model'
    assert not [path for pattern in VIDEO_CODECS for path in bundle.rglob(pattern)], 'Unused video codecs bundled'
    # Code signing and notarization reject links to files that were not collected.
    assert not [path for path in bundle.rglob('*') if path.is_symlink() and not path.exists()], 'Broken links in the application'
    platform = {}
    if sys.platform == 'darwin':
        assert all(path.name == unicodedata.normalize('NFD', path.name) for path in bundle.rglob('*')), \
            'Bundle filenames would change during Finder installation; rebuild with NFD destinations before signing'
        declared = plistlib.loads((bundle/'Contents/Info.plist').read_bytes())['LSMinimumSystemVersion']
        required = required_macos(bundle)
        assert required <= tuple(map(int, declared.split('.'))), \
            f'Bundled binaries need macOS {".".join(map(str, required))}; Info.plist declares {declared}'
        platform['minimum_macos'] = declared
        platform['finder_stable_filenames'] = True
    with zipfile.ZipFile(release/f'ADF-ThirdParty-Sources-{version}{suffix}.zip') as archive:
        assert archive.testzip() is None, 'Third-party archive CRC failure'
        assert archive.read('build-manifest.json') == third_party_manifest(manifest)
        for source in manifest['source_archives']:
            with archive.open(source['filename']) as stream:
                assert hashlib.file_digest(stream, 'sha256').hexdigest() == source['sha256'], source['filename']
    checksums = {}
    for line in (release/f'SHA256SUMS-{version}{suffix}.txt').read_text(encoding='utf-8').splitlines():
        expected, name = line.split('  ', 1)
        assert digest(release/name) == expected, f'Release checksum mismatch: {name}'
        checksums[name] = expected
    assert installer in checksums, 'Installer missing from release checksums'
    return dict(ok=True, platform=sys.platform, version=version, source_files=len(files), frozen_modules=count,
                frozen_resources=data_count, source_and_executable_match=True, sources_bundled=True,
                bundled_ocr_models=len(manifest.get('ocr_models', [])),
                bundled_search_models=len(manifest.get('search_models', [])),
                source_archives=len(manifest['source_archives']), checksums=checksums, **platform)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = verify()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
